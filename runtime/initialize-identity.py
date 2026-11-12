#!/usr/bin/env python3
"""Per-PC identity on the writable ext4 root; never run on the USB master."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
import unicodedata


def runtime_hostname(hardware, windows_name, fallback):
    """Use the registered label without changing persistent OS/PAIR identity."""
    if not isinstance(fallback, str) or not re.fullmatch(r'gpu-[a-f0-9]{12}', fallback):
        raise ValueError('Unknown persistent hostname format')
    if windows_name is None:
        return fallback
    if (not isinstance(windows_name, str) or not 1 <= len(windows_name) <= 255
            or windows_name != windows_name.strip()
            or any(ord(c) > 0xffff or unicodedata.category(c).startswith('C')
                   or c in '\u2028\u2029' for c in windows_name)):
        raise ValueError('Invalid registered Windows PC name')
    suffix = uuid.UUID(hardware.strip()).hex
    name = unicodedata.normalize('NFKD', windows_name).encode('ascii', 'ignore').decode().lower()
    name = re.sub(r'[^a-z0-9-]+', '-', name).strip('-') or 'gpu'
    # One DNS label: reserve a separator and the complete 32-hex hardware UUID.
    name = name[:30].rstrip('-')
    return name + '-' + suffix


def apply_hostname(root, hardware, managed, generate_keys, set_hostname):
    info = initialize(root, hardware, generate_keys)
    name = runtime_hostname(hardware, managed.get('windows_computer_name'), info['hostname'])
    # A runtime hostname is set before network/PAIR startup. Keep the state-v1
    # hostname and identity record intact, so old state stays verifiable.
    set_hostname(name)
    return name


def initialize(root, hardware, generate_keys):
    state = root / "var/lib/gpu-root"
    state.mkdir(parents=True, exist_ok=True)
    marker = state / "identity.json"
    hardware_hash = hashlib.sha256(hardware.strip().lower().encode()).hexdigest()
    machine = root / "etc/machine-id"
    identity = machine.read_text().strip() if machine.exists() else ""
    if marker.exists():
        known = json.loads(marker.read_text())
        if known["hardware_sha256"] != hardware_hash or known["machine_id"] != identity:
            raise ValueError("An initialized root was moved/cloned to another PC; manual re-provisioning required")
        return known
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        identity = uuid.uuid4().hex
        machine.write_text(identity + "\n")
    hostname = "gpu-" + identity[:12]
    (root / "etc/hostname").write_text(hostname + "\n")
    generate_keys()
    known = {"hardware_sha256": hardware_hash, "machine_id": identity, "hostname": hostname}
    temporary = state / "identity.json.new"
    with temporary.open("x") as stream:
        json.dump(known, stream)
    temporary.replace(marker)
    return known


if __name__ == "__main__":
    try:
        if not Path("/run/gpu-root/state.json").is_file():
            sys.exit("Identity initialization requires the managed writable root boot")
        managed = json.loads(Path('/run/gpu-root/state.json').read_text())
        def generate():
            if managed.get('state_schema') != 1:
                subprocess.run(["ssh-keygen", "-A"], check=True)
        name = apply_hostname(Path("/"), Path("/sys/class/dmi/id/product_uuid").read_text(), managed,
                              generate, lambda value: subprocess.run(["hostname", value], check=True))
        print("PAIR BOOT: Runtime hostname: " + name, flush=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Identity initialization stopped: {error}")
