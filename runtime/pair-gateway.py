#!/usr/bin/env python3
"""Optional, single-VIP HTTPS gateway for the managed PAIR session."""
from contextlib import ExitStack
import hashlib
import hmac
from http import HTTPStatus
from http.client import HTTPConnection, HTTPSConnection, HTTPException
from http.server import BaseHTTPRequestHandler
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
from socketserver import UnixStreamServer
import ssl
import stat
import subprocess
import sys
import threading
import time

RUN = Path('/run/pair-gateway')
CREDENTIALS = Path('/run/pair-gateway-credentials')
BOOT = Path('/run/gpu-root')
HELPER = '/usr/lib/gpu-root/pair-gateway.py'
GATEWAY_USER = 'pair-gateway'
GATEWAY_GID = 997
NFT = '/usr/sbin/nft'
PAIR_PORT = 8080
LIMIT = 1048576
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,14}', re.ASCII)
DNS = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
                 r'[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', re.ASCII)
TOKEN = re.compile(r'Bearer ([A-Za-z0-9_-]{43})', re.ASCII)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field')
        value[key] = item
    return value


def read_file(directory, name, *, private=False, optional=False):
    # Directory-relative opens prevent a path replacement from redirecting a read.
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory_fd)
        if private and (info.st_uid != 0 or info.st_mode & 0o077):
            raise ValueError('Credential directory permissions')
        if any(item.casefold() == name.casefold() and item != name for item in os.listdir(directory_fd)):
            raise ValueError('Case collision')
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            if optional:
                return None
            raise
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or (private and (info.st_uid != 0 or info.st_mode & 0o077)):
                raise ValueError('Unsafe file')
            data = stream.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('Oversized file')
            return data
    finally:
        os.close(directory_fd)


def parse(data):
    return json.loads(data, object_pairs_hook=unique_object)


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def settings(data):
    cfg = parse(data)
    if not isinstance(cfg, dict) or set(cfg) != {
        'schema', 'generation', 'enabled', 'virtual_ipv4', 'dns_name',
        'virtual_router_id', 'client_ipv4_networks', 'readiness_model_ids',
    }:
        raise ValueError('Gateway configuration fields')
    if not integer(cfg['schema'], 1, 1) or not integer(cfg['generation'], 1, 2**31 - 1):
        raise ValueError('Invalid gateway configuration version')
    if type(cfg['enabled']) is not bool:
        raise ValueError('Invalid enabled flag')
    if not isinstance(cfg['virtual_ipv4'], str):
        raise ValueError('Invalid VIP')
    vip = ipaddress.IPv4Interface(cfg['virtual_ipv4'])
    if (str(vip) != cfg['virtual_ipv4']
            or vip.network.prefixlen not in range(1, 31)
            or vip.ip in (vip.network.network_address, vip.network.broadcast_address)
            or vip.ip.is_multicast or vip.ip.is_loopback or vip.ip.is_unspecified or vip.ip.is_link_local
            or vip.ip.is_reserved):
        raise ValueError('Invalid VIP')
    hostname = cfg['dns_name']
    if not isinstance(hostname, str) or len(hostname) > 253 or not DNS.fullmatch(hostname):
        raise ValueError('Invalid DNS name')
    if not integer(cfg['virtual_router_id'], 1, 255):
        raise ValueError('Invalid VRID')
    networks = cfg['client_ipv4_networks']
    if (not isinstance(networks, list) or not 1 <= len(networks) <= 64
            or any(not isinstance(net, str) or str(ipaddress.IPv4Network(net)) != net
                   or ipaddress.IPv4Network(net).prefixlen == 0 for net in networks)):
        raise ValueError('Invalid client networks')
    cfg['client_ipv4_networks'] = [str(network) for network in
                                   ipaddress.collapse_addresses(map(ipaddress.IPv4Network, networks))]
    if cfg['client_ipv4_networks'] == ['0.0.0.0/0']:
        raise ValueError('Client networks cover all IPv4 addresses')
    models = cfg['readiness_model_ids']
    if (not isinstance(models, list) or not 1 <= len(models) <= 64
            or any(not isinstance(model, str) or not model.strip() or len(model) > 256 for model in models)):
        raise ValueError('Invalid readiness models')
    return cfg


def command(*args, env=None):
    return subprocess.run(args, check=True, capture_output=True, timeout=8, env=env).stdout


def node_interface(data):
    if data is None:
        return None
    value = parse(data)
    if (not isinstance(value, dict) or set(value) != {'interface'}
            or not isinstance(value['interface'], str) or not NAME.fullmatch(value['interface'])):
        raise ValueError('Invalid node interface')
    return value['interface']


def select_interface(vip, links, wired, requested):
    eligible = []
    for link in links:
        name = link['ifname']
        addresses = link.get('addr_info', [])
        if any(row['local'] == str(vip.ip) for row in addresses):
            raise ValueError('VIP already configured on this node')
        if (not NAME.fullmatch(name) or link.get('link_type') != 'ether'
                or not {'UP', 'LOWER_UP', 'MULTICAST'} <= set(link['flags'])
                or not wired(name)):
            continue
        if any(row.get('scope') == 'global' and row['prefixlen'] == vip.network.prefixlen
               and ipaddress.IPv4Address(row['local']) in vip.network for row in addresses):
            eligible.append(name)
    candidates = [name for name in eligible if requested is None or name == requested]
    if len(candidates) != 1:
        raise ValueError('Cannot select one eligible wired interface')
    return candidates[0]


def is_wired(name):
    device = Path('/sys/class/net') / name
    return (device / 'device').exists() and not any(
        (device / entry).exists() for entry in ('wireless', 'phy80211'))


def credential_metadata(data):
    value = parse(data)
    entries = value['tokens'] if isinstance(value, dict) and set(value) == {'tokens'} else [value]
    if not isinstance(entries, list) or not entries:
        raise ValueError('Token metadata requires a nonempty list')
    hashes = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'sha256', 'expires_at'}
                or not isinstance(entry['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                or not integer(entry['expires_at'], 1, 2**63 - 1)
                or entry['sha256'] in hashes):
            raise ValueError('Invalid or duplicate token metadata')
        hashes.add(entry['sha256'])
    if max(entry['expires_at'] for entry in entries) <= time.time():
        raise ValueError('All student tokens have expired')
    return entries


def authorized(headers, metadata, now):
    match = TOKEN.fullmatch(headers[0]) if len(headers) == 1 else None
    if match is None:
        return False
    digest = hashlib.sha256(match[1].encode('ascii')).hexdigest()
    return any(hmac.compare_digest(digest, entry['sha256']) and now < entry['expires_at']
               for entry in metadata)


def write_file(name, data, group=False):
    path = RUN / name
    with path.open('xb') as stream:
        if group:
            os.fchown(stream.fileno(), 0, GATEWAY_GID)
            os.fchmod(stream.fileno(), 0o640)
        stream.write(data if isinstance(data, bytes) else data.encode())


def setup_credentials(hostname):
    metadata = credential_metadata(read_file(CREDENTIALS, 'token.json', private=True))
    for name in ('fullchain.pem', 'key.pem', 'ca.pem'):
        write_file(name, read_file(CREDENTIALS, name, private=True), group=name != 'ca.pem')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(RUN / 'fullchain.pem', RUN / 'key.pem', password=lambda: '')
    san = command('/usr/bin/openssl', 'x509', '-in', str(RUN / 'fullchain.pem'), '-noout',
                  '-ext', 'subjectAltName')
    names = san.partition(b'\n')[2].split(b',')
    if b'DNS:' + hostname.encode('ascii') not in [name.strip() for name in names]:
        raise ValueError('TLS certificate requires the configured DNS SAN')
    command('/usr/bin/openssl', 'verify', '-purpose', 'sslserver', '-verify_hostname', hostname,
            '-CAfile', str(RUN / 'ca.pem'), '-no-CApath', '-no-CAstore', '-untrusted',
            str(RUN / 'fullchain.pem'), str(RUN / 'fullchain.pem'))
    return metadata


def caddy_config(hostname):
    return f'''{{
    admin off
    auto_https disable_redirects
    servers {{
        protocols h1 h2
        strict_sni_host
    }}
}}
(authenticated_pair) {{
    route {{
        forward_auth unix/{RUN}/auth.sock {{
            uri /verify
        }}
        rewrite * {{args.0}}
        reverse_proxy 127.0.0.1:{PAIR_PORT} {{
            header_up -Authorization
            header_up -Proxy-Authorization
            header_up -Upgrade
            header_up -Connection
            header_up Host 127.0.0.1:{PAIR_PORT}
            flush_interval -1
        }}
    }}
}}
https://{hostname} {{
    bind 0.0.0.0
    tls {RUN}/fullchain.pem {RUN}/key.pem
    @models {{
        method GET
        expression {{http.request.uri.path}} == "/v1/models"
    }}
    @chat {{
        method POST
        expression {{http.request.uri.path}} == "/v1/chat/completions"
    }}
    handle @models {{
        import authenticated_pair /v1/models
    }}
    handle @chat {{
        import authenticated_pair /v1/chat/completions
    }}
    handle {{
        respond 404
    }}
}}
https:// {{
    bind 0.0.0.0
    respond 404
}}
'''


def firewall_config(cfg, interface):
    vip = ipaddress.IPv4Interface(cfg['virtual_ipv4'])
    networks = ', '.join(cfg['client_ipv4_networks'])
    return f'''create table inet pair_student_gateway
add chain inet pair_student_gateway input {{ type filter hook input priority -10; policy accept; }}
add rule inet pair_student_gateway input iifname "lo" ip daddr 127.0.0.1 tcp dport 443 accept
add rule inet pair_student_gateway input iifname "{interface}" ip daddr {vip.ip} ip saddr {{ {networks} }} tcp dport 443 accept
add rule inet pair_student_gateway input tcp dport 443 drop
'''


def keepalived_config(cfg, interface):
    return f'''global_defs {{
    enable_script_security
    script_user root
}}
vrrp_script pair_availability {{
    script "/usr/bin/python3 {HELPER} check"
    interval 5
    timeout 4
    fall 2
    rise 2
    init_fail
    weight 0
}}
vrrp_instance PAIR_STUDENT {{
    state BACKUP
    interface {interface}
    version 3
    virtual_router_id {cfg['virtual_router_id']}
    priority 100
    advert_int 1
    nopreempt
    virtual_ipaddress {{
        {cfg['virtual_ipv4']} dev {interface}
    }}
    track_script {{
        pair_availability
    }}
}}
'''


class AuthHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(2)
        super().setup()

    def do_GET(self):
        metadata = self.server.metadata
        if self.path.partition('?')[0] != '/verify':
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        valid = authorized(self.headers.get_all('Authorization', []), metadata, time.time())
        self.send_response(HTTPStatus.NO_CONTENT if valid else HTTPStatus.UNAUTHORIZED)
        if not valid:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *_args):
        pass


class AuthServer(UnixStreamServer):
    request_queue_size = 64

    def server_bind(self):
        super().server_bind()
        os.chown(self.server_address, 0, GATEWAY_GID)
        os.chmod(self.server_address, 0o660)

    def handle_error(self, *_args):
        pass


class LocalHTTPS(HTTPSConnection):
    def connect(self):
        self.sock = self._context.wrap_socket(
            socket.create_connection(('127.0.0.1', self.port), timeout=self.timeout),
            server_hostname=self.host)


def healthy():
    cfg = parse((RUN / 'health.json').read_bytes())
    if time.time() >= cfg['expires_at']:
        return False
    context = ssl.create_default_context(cafile=str(RUN / 'ca.pem'))
    context.hostname_checks_common_name = False
    https = LocalHTTPS(cfg['dns_name'], 443, context=context, timeout=1)
    pair = HTTPConnection('127.0.0.1', PAIR_PORT, timeout=1)
    with ExitStack() as stack:
        stack.callback(https.close)
        stack.callback(pair.close)
        https.request('GET', '/v1/models')
        response = https.getresponse()
        if response.status != 401 or response.getheader('WWW-Authenticate') != 'Bearer':
            return False
        pair.request('GET', '/v1/models')
        response = pair.getresponse()
        if response.status != 200:
            return False
        payload = response.read(LIMIT + 1)
        if len(payload) > LIMIT:
            return False
        items = parse(payload)['data']
        return isinstance(items, list) and any(isinstance(item, dict) and item.get('id') in cfg['readiness_model_ids'] for item in items)


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def cleanup():
    if (RUN / 'nft-installed').exists():
        command(NFT, 'delete', 'table', 'inet', 'pair_student_gateway')
        (RUN / 'nft-installed').unlink()


def serve():
    boot = parse((BOOT / 'state.json').read_bytes())
    if boot.get('mode') not in ('Normal', 'Unlock'):
        return
    directory = boot.get('storage_directory')
    if not directory:
        return
    if not isinstance(directory, str) or Path(directory).name != directory or directory in ('.', '..'):
        raise ValueError('Invalid registered storage directory')
    if not (BOOT / 'ntfs').is_mount():
        raise ValueError('Registered NTFS mount is missing')
    storage = BOOT / 'ntfs' / directory
    raw = read_file(storage, 'pair-gateway.json', optional=True)
    if raw is None:
        return
    cfg = settings(raw)
    if not cfg['enabled']:
        return

    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    requested = node_interface(read_file(storage, 'pair-gateway-node.json', optional=True))
    # IPv4-only iproute2 output omits link_type unless details are requested.
    interface = select_interface(ipaddress.IPv4Interface(cfg['virtual_ipv4']),
                                 parse(command('/usr/sbin/ip', '-j', '-4', '-details', 'address', 'show')),
                                 is_wired, requested)
    os.chown(RUN, 0, GATEWAY_GID)
    metadata = setup_credentials(cfg['dns_name'])
    write_file('health.json', json.dumps({
        'dns_name': cfg['dns_name'],
        'expires_at': max(entry['expires_at'] for entry in metadata),
        'readiness_model_ids': cfg['readiness_model_ids'],
    }))
    write_file('Caddyfile', caddy_config(cfg['dns_name']), group=True)
    write_file('keepalived.conf', keepalived_config(cfg, interface))
    write_file('gateway.nft', firewall_config(cfg, interface))

    caddy = ('/usr/bin/setpriv', '--reuid=' + GATEWAY_USER, '--regid=' + GATEWAY_USER,
             '--clear-groups', '--inh-caps=-all,+net_bind_service',
             '--ambient-caps=-all,+net_bind_service', '--no-new-privs', '/usr/bin/caddy')
    caddy_args = ('--config', str(RUN / 'Caddyfile'), '--adapter', 'caddyfile')
    caddy_env = {**os.environ, 'HOME': str(RUN), 'XDG_DATA_HOME': str(RUN / 'caddy'),
                 'XDG_CONFIG_HOME': str(RUN / 'caddy')}
    (RUN / 'caddy').mkdir(mode=0o700)
    os.chown(RUN / 'caddy', GATEWAY_GID, GATEWAY_GID)
    keepalived = ('/usr/sbin/keepalived', '--vrrp', '--dont-fork', '--use-file', str(RUN / 'keepalived.conf'),
                  '--pid', str(RUN / 'keepalived.pid'), '--vrrp_pid', str(RUN / 'vrrp.pid'))

    command(*caddy, 'validate', *caddy_args, env=caddy_env)
    command(*keepalived, '--config-test')
    if stopped.is_set():
        return
    command(NFT, '-f', str(RUN / 'gateway.nft'))
    write_file('nft-installed', b'')
    with ExitStack() as stack:
        auth = stack.enter_context(AuthServer(str(RUN / 'auth.sock'), AuthHandler))
        auth.metadata = metadata
        thread = threading.Thread(target=auth.serve_forever, daemon=True)
        thread.start()
        stack.callback(auth.shutdown)
        proxy = subprocess.Popen((*caddy, 'run', *caddy_args), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=caddy_env)
        stack.callback(terminate, proxy)
        if stopped.is_set():
            return
        # init_fail holds the VRRP instance in FAULT until the HTTPS probe succeeds twice.
        vrrp = subprocess.Popen(keepalived)
        stack.callback(terminate, vrrp)
        while not stopped.wait(0.5):
            if proxy.poll() is not None or vrrp.poll() is not None or not thread.is_alive():
                raise RuntimeError('Gateway component exited')


def main():
    action = sys.argv[1]
    try:
        if action == 'check':
            return 0 if healthy() else 1
        if action == 'cleanup':
            cleanup()
        elif action == 'serve':
            serve()
        else:
            raise ValueError('Unknown gateway action')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, HTTPException,
            subprocess.SubprocessError) as error:
        if action != 'check':
            # Configuration, command stderr and request data can contain private values.
            detail = str(error) if type(error) is RuntimeError else type(error).__name__
            print(f'PAIR student gateway {action} failed ({detail})', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
