#!/usr/bin/env python3
"""Per-PC identity on the writable ext4 root; never run on the USB master."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid


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
        def generate():
            managed = json.loads(Path('/run/gpu-root/state.json').read_text())
            if managed.get('state_schema') != 1:
                subprocess.run(["ssh-keygen", "-A"], check=True)
        info = initialize(Path("/"), Path("/sys/class/dmi/id/product_uuid").read_text(), generate)
        subprocess.run(["hostname", info["hostname"]], check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Identity initialization stopped: {error}")
