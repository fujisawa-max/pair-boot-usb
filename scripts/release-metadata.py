#!/usr/bin/env python3
"""Construct public release information from fixed inputs, not host packages."""
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def metadata():
    release = json.loads((ROOT / 'config/release.json').read_text())
    def lock(name):
        return json.loads((ROOT / 'inputs' / name).read_text())
    return {'project_version': release['version'], 'version': release['version'],
            **{key: release[key] for key in ('boot_abi', 'state_schema')},
            'registry_schema': runpy.run_path(str(ROOT / 'runtime/fleet-registry.py'))['REGISTRY_SCHEMA'],
            'components': {
                'Kernel': lock('debian-build-inputs.json')['kernel_release'],
                'gpu-unlock commit': lock('gpu-unlock-source.json')['commit'],
                'Base distribution': lock('debian-build-inputs.json')['distribution'],
                'Fedora Workstation Live': lock('fedora-live-input.json')['version'],
                'NVIDIA driver package': lock('nvidia-driver-inputs.json')['nvidia_driver_version'],
                'PAIR': lock('pair-headless-input.json')['version'],
                'llama.cpp': lock('pair-llamacpp-inputs.json')['engine_version'],
            }}


def text():
    return runpy.run_path(str(ROOT / 'runtime/release-info.py'))['media_text'](metadata())
