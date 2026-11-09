"""Windows fleet logic on temporary files, cross-checked by the boot reader."""
import copy
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh') or str(ROOT / 'build/powershell-7.5.4/pwsh')
spec = importlib.util.spec_from_file_location('windows_fleet_reference', ROOT / 'runtime/fleet-registry.py')
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


def host(test=False, mbr=False):
    value = {'schema': 1, 'hardware_uuid': '01234567-89ab-cdef-0123-456789abcdef',
             'ntfs_uuid': '0123456789ABCDEF', 'gpu_bdf': '0000:01:00.0', 'gpu_pci_id': '10de:2504',
             'checked_at_unix': int(time.time()) - 60, 'bitlocker_volume_status': 'FullyDecrypted',
             'bitlocker_protection': 'Off', 'fast_startup_enabled': False, 'secure_boot_enabled': True,
             'partition_is_boot': False, 'partition_is_system': False, 'partition_is_hidden': False,
             'storage_directory': 'GPU-Linux', 'storage_target': {
                 'kind': 'usb-ntfs-v1', 'partition_guid': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                 'disk_guid': '11111111-2222-3333-4444-555555555555',
                 'offset_bytes': 12 * 1024**3, 'size_bytes': 16 * 1024**3}}
    if test:
        value.update(boot_mode='no-gpu-test', gpu_bdf=None, gpu_pci_id=None)
    if mbr:
        value['storage_target'] = {'kind': 'usb-ntfs-mbr-v1', 'disk_signature': '1234abcd',
                                   'partition_number': 1, 'offset_bytes': 1024**2, 'size_bytes': 16 * 1024**3}
    return value


@unittest.skipUnless(Path(PWSH).is_file(), 'Optional fixed PowerShell fixture runner needed')
class WindowsFleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'build')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.request = self.base / 'request.json'

    def write(self, name, records, version='0.9.1-shared', generation=1):
        value = fleet.merge(records, generation, version)
        path = self.base / name
        path.write_bytes(fleet.encode(value))
        return path

    def usb(self, version='0.9.1-shared'):
        path = self.base / 'usb'
        path.mkdir()
        (path / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'root_version': version}))
        return path

    def call(self, action, expected=True, **parameters):
        self.request.write_text(json.dumps({'action': action, **parameters}))
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File',
                                 str(ROOT / 'tests/windows-fleet-fixture.ps1'),
                                 '-ScriptPath', str(ROOT / 'toolkit/fleet.ps1'), '-RequestFile', str(self.request)],
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        if expected:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_test_registry_collect_merge_deploy_roundtrip_and_python_interoperability(self):
        for mbr in (False, True):
            with self.subTest(mbr=mbr):
                self.exercise_roundtrip(mbr)

    def exercise_roundtrip(self, mbr):
        suffix = 'mbr' if mbr else 'gpt'
        value = host(test=True, mbr=mbr)
        raw = self.write(suffix + '-raw.json', [value], version='0.9.0-shared')
        other = copy.deepcopy(value)
        other['checked_at_unix'] += 1
        # Same contents, a different nested property order: not a host conflict.
        other['storage_target'] = dict(reversed(list(other['storage_target'].items())))
        duplicate = self.write(suffix + '-duplicate.json', [other], version='0.9.0-shared')
        payload = fleet.load(duplicate)
        payload['records'][0]['storage_target'] = dict(reversed(list(payload['records'][0]['storage_target'].items())))
        raw_bytes = json.dumps(payload).encode()
        duplicate.write_text(json.dumps({'schema': 1, 'payload_base64': base64.b64encode(raw_bytes).decode(),
                                         'sha256': hashlib.sha256(raw_bytes).hexdigest()}))
        merged = self.base / (suffix + '-merged.json')
        self.call('Merge', Inputs=[str(raw), str(duplicate)], OutputFile=str(merged), Generation=2, RootVersion='0.9.1-shared')
        registry = fleet.load(merged)
        self.assertEqual(registry['records'], [fleet.record(other)])
        usb = self.base / (suffix + '-usb')
        usb.mkdir()
        (usb / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.1-shared'}))
        self.call('Deploy', Inputs=[str(merged)], UsbRoot=str(usb))
        self.call('Deploy', Inputs=[str(merged)], UsbRoot=str(usb))  # idempotent
        env = (usb / ('gpu-config/hosts/' + value['hardware_uuid'] + '.env')).read_bytes()
        self.assertEqual(len(env), 1024)
        self.assertIn(b'gpu_targets=unconfigured\ngpu_default=0\n', env)
        collected = self.base / (suffix + '-collected.json')
        self.call('Collect', UsbRoot=str(usb), OutputFile=str(collected))
        self.assertEqual(fleet.load(collected), registry)
        self.call('Status', Inputs=[str(collected)])

    def test_deploy_upgrades_only_known_version_and_preserves_evidence_age(self):
        value = host(test=True)
        raw = self.write('old.json', [value], version='0.9.0-shared', generation=3)
        usb = self.usb()
        self.call('Deploy', Inputs=[str(raw)], UsbRoot=str(usb))
        result = fleet.load(usb / 'gpu-config/registry.json')
        self.assertEqual(result['root_version'], '0.9.1-shared')
        self.assertEqual(result['generation'], 4)
        self.assertEqual(result['records'][0], fleet.record(value))
        future = self.write('future.json', [value], version='0.9.2-shared', generation=5)
        before = (usb / 'gpu-config/registry.json').read_bytes()
        self.call('Deploy', expected=False, Inputs=[str(future)], UsbRoot=str(usb))
        self.assertEqual((usb / 'gpu-config/registry.json').read_bytes(), before)

    def test_mixed_gpu_and_test_hosts_generate_same_grub_environment_as_python(self):
        cpu = host(test=True)
        gpu = host()
        gpu['hardware_uuid'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        gpu['storage_directory'] = 'GPU-Linux-normal'
        registry = self.write('mixed.json', [cpu, gpu], generation=3)
        usb = self.usb()
        self.call('Deploy', Inputs=[str(registry)], UsbRoot=str(usb))
        reference = self.base / 'reference'; reference.mkdir()
        (reference / 'usb-config.json').write_bytes((usb / 'usb-config.json').read_bytes())
        fleet.deploy(fleet.load(registry), reference)
        for record in (cpu, gpu):
            name = 'gpu-config/hosts/' + record['hardware_uuid'] + '.env'
            self.assertEqual((usb / name).read_bytes(), (reference / name).read_bytes())

    def test_unsupported_merge_and_downgrade_do_not_publish(self):
        old = self.write('old.json', [host(test=True)], version='0.9.0-shared')
        future = self.write('future.json', [host(test=True)], version='0.9.2-shared')
        output = self.base / 'bad-merge.json'
        self.call('Merge', expected=False, Inputs=[str(old), str(future)], OutputFile=str(output), Generation=2)
        self.assertFalse(output.exists())
        usb = self.usb('0.9.0-shared')
        current = self.write('current.json', [host(test=True)])
        self.call('Deploy', expected=False, Inputs=[str(current)], UsbRoot=str(usb))
        self.assertFalse((usb / 'gpu-config').exists())

    def test_merge_infers_compatible_version_or_uses_selected_usb(self):
        value = host()
        old = self.write('old.json', [value], version='0.9.0-shared')
        new = self.write('new.json', [value])
        for index, extra in enumerate(({}, {'UsbRoot': str(self.usb())})):
            output = self.base / f'merged-{index}.json'
            self.call('Merge', Inputs=[str(old), str(new)], OutputFile=str(output), Generation=2, **extra)
            self.assertEqual(fleet.load(output)['root_version'], '0.9.1-shared')

    def test_gui_worker_passes_multiple_literal_inputs_and_target_usb_to_merge(self):
        tools = self.base / 'tools'; tools.mkdir()
        script = str(ROOT / 'toolkit/fleet.ps1').replace("'", "''")
        (tools / 'fleet.ps1').write_text(
            "param($Action,$Inputs,$OutputFile,$UsbRoot,$Generation)\n"
            "& '" + script + "' @PSBoundParameters -OfflineFixture\n")
        first = self.write('input one.json', [host(test=True)], version='0.9.0-shared')
        second = self.write("input'two.json", [host(test=True)], version='0.9.0-shared')
        usb = self.usb()
        output = self.base / "merged' output.json"
        request = {'operation': 'Merge', 'inputs': [str(first), str(second)],
                   'output_file': str(output), 'generation': 2, 'usb_root': str(usb)}
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File',
                                 str(ROOT / 'gui/windows-worker.ps1'), '-ToolsPath', str(tools)],
                                input=json.dumps(request)+'\n', text=True, capture_output=True,
                                env={**os.environ, 'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                     'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                     'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(fleet.load(output)['root_version'], '0.9.1-shared')

    def test_windows_scripts_parse_and_gui_plan_keeps_merge_usb_and_file_arguments(self):
        # Parse only: never load Windows Forms, run the worker, or request UAC.
        path = str(ROOT).replace("'", "''")
        code = ("$ErrorActionPreference='Stop'; "
                "Get-ChildItem '" + path + "/gui','" + path + "/toolkit' -Filter *.ps1 | ForEach-Object { "
                "$tokens=$null; $errors=$null; [void][Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors); "
                "if ($errors.Count) { throw ($errors | Out-String) } }; "
                "& '" + path + "/gui/windows-gui.ps1' -PlanOnly -Operation Merge -Inputs @('one file.json',\"two'file.json\") -UsbRoot 'X:\\' -OutputFile 'X:\\merged.json' -Generation 5")
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True,
                                env={**os.environ, 'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                     'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                     'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['request']['inputs'], ['one file.json', "two'file.json"])
        self.assertEqual(plan['request']['usb_root'], 'X:\\')
        self.assertIn('-UsbRoot', plan['argv'])

    def test_rejections_preserve_target_registry_and_host_environment(self):
        value = host(test=True)
        original = self.write('original.json', [value], generation=3)
        usb = self.usb()
        self.call('Deploy', Inputs=[str(original)], UsbRoot=str(usb))
        before = {str(p.relative_to(usb)): p.read_bytes() for p in usb.rglob('*') if p.is_file()}
        changed = {**value, 'storage_directory': 'Other'}
        changed_usb = {**value, 'storage_target': {**value['storage_target'], 'size_bytes': 8 * 1024**3}}
        for index, (records, generation) in enumerate((([changed], 4), ([changed_usb], 4),
                                                     ([], 4), ([value], 2), ([changed], 3))):
            path = self.write(f'reject-{index}.json', records, generation=generation)
            self.call('Deploy', expected=False, Inputs=[str(path)], UsbRoot=str(usb))
            self.assertEqual({str(p.relative_to(usb)): p.read_bytes() for p in usb.rglob('*') if p.is_file()}, before)

    def test_invalid_test_records_safety_values_and_checksum_never_publish(self):
        value = host(test=True)
        changes = [{'boot_mode': 'arbitrary'}, {'boot_mode': ['no-gpu-test']}, {'gpu_bdf': '0000:01:00.0'},
                   {'gpu_pci_id': '10de:2504'}, {'storage_target': None},
                   {'bitlocker_protection': 'Unknown'}, {'partition_is_hidden': None},
                   {'fast_startup_enabled': True}, {'secure_boot_enabled': False}, {'checked_at_unix': 0}]
        for index, change in enumerate(changes):
            invalid = self.base / f'invalid-{index}.json'
            invalid.write_bytes(fleet.encode({'schema': 1, 'generation': 1, 'root_version': '0.9.1-shared', 'records': [{**value, **change}]}))
            output = self.base / f'output-{index}.json'
            self.call('Merge', expected=False, Inputs=[str(invalid)], OutputFile=str(output), Generation=2)
            self.assertFalse(output.exists())
        envelope = json.loads(self.write('valid.json', [value]).read_text())
        envelope['sha256'] = '0' * 64
        invalid.write_text(json.dumps(envelope))
        self.call('Status', expected=False, Inputs=[str(invalid)])

    def test_existing_output_symlink_and_bad_marker_are_preserved(self):
        raw = self.write('valid.json', [host(test=True)])
        output = self.base / 'keep.json'
        output.write_bytes(b'original output')
        self.call('Merge', expected=False, Inputs=[str(raw)], OutputFile=str(output), Generation=2)
        self.assertEqual(output.read_bytes(), b'original output')
        alias = self.base / 'alias.json'
        alias.symlink_to(raw)
        self.call('Status', expected=False, Inputs=[str(alias)])
        usb = self.usb()
        (usb / 'usb-config.json').write_text('{"schema":1,"kind":"wrong","root_version":"0.9.1-shared"}')
        self.call('Deploy', expected=False, Inputs=[str(raw)], UsbRoot=str(usb))
        self.assertFalse((usb / 'gpu-config').exists())
