"""Student gateway fixtures use synthetic configuration and credentials."""
import hashlib
import ipaddress
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

API = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'runtime/pair-gateway.py'))
CONFIG = {
    'schema': 1, 'generation': 1, 'enabled': True,
    'virtual_ipv4': '192.0.2.240/24', 'dns_name': 'pair-lab.example',
    'virtual_router_id': 42, 'client_ipv4_networks': ['198.51.100.0/24'],
    'readiness_model_ids': ['PAIR-Code'],
}


def link(name, ip):
    return {'ifname': name, 'flags': ['UP', 'LOWER_UP', 'MULTICAST'], 'link_type': 'ether',
            'addr_info': [{'local': ip, 'prefixlen': 24, 'scope': 'global'}]}


class GatewayTests(unittest.TestCase):
    def test_settings_reject_unsafe_and_ambiguous_values(self):
        self.assertEqual(API['settings'](json.dumps(CONFIG)), CONFIG)
        invalid = [
            {'enabled': 1}, {'schema': True}, {'generation': 0}, {'virtual_router_id': 256},
            {'virtual_ipv4': '192.0.2.255/24'}, {'virtual_ipv4': '2001:db8::1/64'},
            {'dns_name': 'pair.example\nrespond 200'}, {'client_ipv4_networks': ['0.0.0.0/0']},
            {'client_ipv4_networks': ['198.51.100.1/24']}, {'readiness_model_ids': []},
            {'extra': True},
        ]
        for changed in invalid:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                API['settings'](json.dumps({**CONFIG, **changed}))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            API['settings']('{"enabled":true,"enabled":false}')

    def test_gateway_files_are_optional_but_links_and_case_collisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary)
            name = 'pair-gateway.json'
            path = storage / name
            self.assertIsNone(API['read_file'](storage, name, optional=True))
            path.write_text('{}')
            self.assertEqual(API['read_file'](storage, name), b'{}')
            (storage / name.upper()).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Case collision'):
                API['read_file'](storage, name)
            (storage / name.upper()).unlink()
            path.unlink()
            path.symlink_to(storage / 'missing')
            with self.assertRaises(OSError):
                API['read_file'](storage, name, optional=True)

    def test_missing_disabled_invalid_and_test_mode_never_start_services(self):
        serve = API['serve']
        with tempfile.TemporaryDirectory() as temporary:
            boot = Path(temporary)
            with patch.dict(serve.__globals__, {'BOOT': boot}), patch.object(Path, 'is_mount', return_value=True):
                (boot / 'ntfs' / 'GPU-Linux').mkdir(parents=True)
                state = boot / 'state.json'
                cfg = boot / 'ntfs/GPU-Linux/pair-gateway.json'
                state.write_text(json.dumps({'mode': 'Normal', 'storage_directory': 'GPU-Linux'}))
                with patch.dict(serve.__globals__, {'command': lambda *_: self.fail('Unexpected service')}):
                    serve()
                    cfg.write_text(json.dumps({**CONFIG, 'enabled': False}))
                    serve()
                    cfg.write_text('{')
                    with self.assertRaises(ValueError):
                        serve()
                    state.write_text(json.dumps({'mode': 'TEST', 'storage_directory': 'GPU-Linux'}))
                    serve()

    def test_interface_requires_unique_wired_link_in_vip_subnet(self):
        select = API['select_interface']
        vip = ipaddress.IPv4Interface(CONFIG['virtual_ipv4'])
        links = [link('enp1s0', '192.0.2.2'), link('enp2s0', '192.0.2.3'),
                 link('enp3s0', '203.0.113.2'), link('wlan0', '192.0.2.4')]
        wired = lambda name: name.startswith('en')
        self.assertEqual(select(vip, links, wired, 'enp2s0'), 'enp2s0')
        for requested in (None, 'wlan0', 'enp3s0'):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                select(vip, links, wired, requested)
        with self.assertRaisesRegex(ValueError, 'already configured'):
            select(vip, links + [link('enp4s0', str(vip.ip))], wired, 'enp2s0')

    def test_bearer_checks_hash_expiry_and_duplicate_headers(self):
        token = 'A' * 43
        metadata = {'sha256': hashlib.sha256(token.encode()).hexdigest(), 'expires_at': 20}
        valid = 'Bearer ' + token
        self.assertTrue(API['authorized']([valid], [metadata], 19))
        for headers in ([], [token], ['Bearer ' + 'B' * 43], [valid, valid], [valid + ' ']):
            self.assertFalse(API['authorized'](headers, [metadata], 19))
        self.assertFalse(API['authorized']([valid], [metadata], 20))
        with self.assertRaises(ValueError):
            API['credential_metadata'](json.dumps(metadata))

    def test_multiple_tokens_keep_independent_expirations(self):
        first = {'sha256': hashlib.sha256(b'A' * 43).hexdigest(), 'expires_at': 20}
        second = {'sha256': hashlib.sha256(b'B' * 43).hexdigest(), 'expires_at': 2**63 - 1}
        parse_metadata = API['credential_metadata']
        self.assertEqual(parse_metadata(json.dumps(second)), [second])
        entries = parse_metadata(json.dumps({'tokens': [first, second]}))
        self.assertTrue(API['authorized'](['Bearer ' + 'A' * 43], entries, 19))
        self.assertFalse(API['authorized'](['Bearer ' + 'A' * 43], entries, 20))
        self.assertTrue(API['authorized'](['Bearer ' + 'B' * 43], entries, 20))
        for invalid in ([], [second, second], [second, {}], [first]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_metadata(json.dumps({'tokens': invalid}))

    def test_generated_configs_limit_routes_and_network_scope(self):
        caddy = API['caddy_config'](CONFIG['dns_name'])
        self.assertIn('strict_sni_host', caddy)
        self.assertIn('protocols h1 h2', caddy)
        self.assertIn('header_up -Authorization', caddy)
        self.assertIn('forward_auth unix/', caddy)
        self.assertIn('flush_interval -1', caddy)
        self.assertIn('method GET', caddy)
        self.assertIn('method POST', caddy)
        nft = API['firewall_config'](CONFIG, 'enp1s0')
        self.assertIn('iifname "enp1s0" ip daddr 192.0.2.240 ip saddr { 198.51.100.0/24 } tcp dport 443 accept', nft)
        self.assertIn('tcp dport 443 drop', nft)
        self.assertNotIn('flush ruleset', nft)
        keepalived = API['keepalived_config'](CONFIG, 'enp1s0')
        for item in ('state BACKUP', 'nopreempt', 'init_fail', 'fall 2', 'rise 2', '192.0.2.240/24 dev enp1s0'):
            self.assertIn(item, keepalived)
