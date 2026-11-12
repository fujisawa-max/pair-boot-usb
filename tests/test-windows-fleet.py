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
    value = {'schema': 1, 'windows_computer_name': None, 'hardware_uuid': '01234567-89ab-cdef-0123-456789abcdef',
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
    def test_name_editor_retains_all_pc_drafts_and_plans_one_batch(self):
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File',
                                 str(ROOT / 'tests/windows-name-editor-fixture.ps1'),
                                 '-GuiDirectory', str(ROOT / 'gui')],
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_name_batches_validate_all_entries_before_publishing_one_generation(self):
        a, b, c = host(test=True), host(test=True), host(test=True)
        b['hardware_uuid'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        c['hardware_uuid'] = '11111111-2222-3333-4444-555555555555'
        source = self.write('original.json', [a, b, c], generation=7)
        original = source.read_bytes()
        updates = [{'hardware_uuid': a['hardware_uuid'], 'windows_computer_name': '推論-A'},
                   {'hardware_uuid': b['hardware_uuid'], 'windows_computer_name': "PC'B"}]
        target = self.base / 'batch.json'
        self.call('Name', Inputs=[str(source)], OutputFile=str(target), Generation=8, NameUpdates=updates)
        value = fleet.load(target)
        self.assertEqual(value['generation'], 8)
        names = {item['hardware_uuid']: item['windows_computer_name'] for item in value['records']}
        self.assertEqual(names[a['hardware_uuid']], '推論-A')
        self.assertEqual(names[b['hardware_uuid']], "PC'B")
        self.assertIsNone(names[c['hardware_uuid']])
        self.assertEqual([fleet.mapping(item) for item in value['records']],
                         [fleet.mapping(item) for item in fleet.load(source)['records']])
        invalid = [[], [updates[0], updates[0]],
                   [updates[0], {**updates[1], 'windows_computer_name': 'bad\nname'}],
                   [updates[0], {**updates[1], 'hardware_uuid': '99999999-2222-3333-4444-555555555555'}],
                   [{**updates[0], 'gpu_bdf': '0000:02:00.0'}]]
        for number, batch in enumerate(invalid):
            output = self.base / f'reject-{number}.json'
            self.call('Name', expected=False, Inputs=[str(source)], OutputFile=str(output), Generation=8, NameUpdates=batch)
            self.assertFalse(output.exists())
        self.call('Name', expected=False, Inputs=[str(source)], OutputFile=str(self.base / 'same-gen.json'),
                  Generation=7, NameUpdates=updates)
        self.call('Name', expected=False, Inputs=[str(source)], OutputFile=str(target), Generation=9, NameUpdates=updates)
        self.assertEqual(fleet.load(target), value)
        self.assertEqual(source.read_bytes(), original)

    def test_name_worker_passes_the_complete_batch_to_real_fleet(self):
        import runpy
        tools = self.base / 'tools'; tools.mkdir()
        script = str(ROOT / 'toolkit/fleet.ps1').replace("'", "''")
        (tools / 'fleet.ps1').write_text(
            "param($Action,$Inputs,$OutputFile,$Generation,$NameUpdates)\n"
            "& '" + script + "' @PSBoundParameters -OfflineFixture\n")
        a, b = host(test=True), host(test=True)
        b['hardware_uuid'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        source = self.write('source.json', [a, b], generation=4)
        output = self.base / 'worker-batch.json'
        updates = [{'hardware_uuid': item['hardware_uuid'], 'windows_computer_name': name}
                   for item, name in ((a, 'NODE-A'), (b, 'NODE-B'))]
        request = {'operation': 'Name', 'inputs': [str(source)], 'output_file': str(output),
                   'generation': 5, 'name_updates': updates}
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File',
                                 str(ROOT / 'gui/windows-worker.ps1'), '-ToolsPath', str(tools)],
                                input=json.dumps(request)+'\n', capture_output=True, text=True,
                                env={**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                     'POWERSHELL_TELEMETRY_OPTOUT': '1',
                                     'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                     'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        core = runpy.run_path(str(ROOT / 'toolkit/fleet.py'))
        self.assertEqual(fleet.load(output), core['set_computer_names'](fleet.load(source), updates, 5))

    def test_gui_registry_picker_and_result_streams(self):
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File',
                                 str(ROOT / 'tests/windows-gui-io-fixture.ps1'),
                                 '-GuiDirectory', str(ROOT / 'gui'), '-WorkDirectory', str(self.base)],
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_status_worker_suppresses_progress_and_preserves_json_stdout(self):
        tools = self.base / 'tools'; tools.mkdir()
        (tools / 'fleet.ps1').write_text(
            "param($Action,$Inputs,[switch]$AsJson)\n"
            "if($ProgressPreference -ne 'SilentlyContinue'){throw 'Progress is enabled'}\n"
            "Write-Progress -Activity 'Synthetic module preparation' -PercentComplete 50\n"
            "[Console]::Error.WriteLine('Synthetic diagnostic')\n"
            "[Console]::Out.WriteLine('{\"schema\":2,\"records\":[]}')\n")
        worker = str(ROOT / 'gui/windows-worker.ps1').replace("'", "''")
        base = str(tools).replace("'", "''")
        code = "& '" + worker + "' -ToolsPath '" + base + "'"
        encoded = base64.b64encode(code.encode('utf-16le')).decode('ascii')
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-OutputFormat', 'Text',
                                 '-EncodedCommand', encoded],
                                input=json.dumps({'operation': 'Status', 'inputs': ['synthetic.json']})+'\n',
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'schema': 2, 'records': []})
        self.assertIn('Synthetic diagnostic', result.stderr)
        self.assertNotIn('CLIXML', result.stderr)

    def test_gui_actions_work_before_native_tab_selection_exists(self):
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File',
                                 str(ROOT / 'tests/windows-gui-tabs-fixture.ps1'),
                                 '-GuiDirectory', str(ROOT / 'gui')],
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_gui_controls_do_not_reuse_typed_script_parameters(self):
        # PowerShell binds parameter types for the script's entire scope and
        # resolves variable names case-insensitively, even after PlanOnly.
        path = str(ROOT / 'gui/windows-gui.ps1').replace("'", "''")
        code = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__GUI__',[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw ($errors | Out-String)}
$parameters=@($ast.ParamBlock.Parameters | ForEach-Object {$_.Name.VariablePath.UserPath.ToLowerInvariant()})
$assignments=$ast.FindAll({param($node) $node -is [Management.Automation.Language.AssignmentStatementAst]},$true)
$controls=0
foreach ($assignment in $assignments) {
    if ($assignment.Left -isnot [Management.Automation.Language.VariableExpressionAst]) {continue}
    if ($assignment.Right.Extent.Text -notmatch '^\[(System\.)?Windows\.Forms\.[\w]+\]::new\(') {continue}
    $controls++
    $name=$assignment.Left.VariablePath.UserPath
    if ($parameters -contains $name.ToLowerInvariant()) {throw "GUI control reuses script parameter: $name"}
}
if ($controls -lt 10) {throw 'GUI initialization was not inspected'}
""".replace('__GUI__', path)
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_convert_legacy_and_update_name_roundtrip_without_reregistering(self):
        legacy = {'schema': 1, 'generation': 4, 'root_version': '0.9.2', 'records': [host(test=True)]}
        legacy['records'][0].pop('windows_computer_name')
        source = self.base / 'legacy.json'
        source.write_bytes(fleet.encode(legacy))
        original = source.read_bytes()
        converted = self.base / 'converted.json'
        self.call('Convert', Inputs=[str(source)], OutputFile=str(converted), Generation=5)
        self.assertEqual(fleet.load(converted)['schema'], 2)
        self.assertIsNone(fleet.load(converted)['records'][0]['windows_computer_name'])
        named = self.base / 'named.json'
        self.call('Name', Inputs=[str(converted)], OutputFile=str(named), Generation=6,
                  HostUuid=host()['hardware_uuid'], ComputerName='推論PC-01')
        value = fleet.load(named)
        expected = fleet.set_computer_name(fleet.load(converted), host()['hardware_uuid'], '推論PC-01', 6)
        self.assertEqual(value, expected)
        self.assertEqual(source.read_bytes(), original)
        target = self.usb()
        self.call('Deploy', Inputs=[str(named)], UsbRoot=str(target))
        collected = self.base / 'names-collected.json'
        self.call('Collect', UsbRoot=str(target), OutputFile=str(collected))
        self.assertEqual(fleet.load(collected), value)
        self.call('Name', expected=False, Inputs=[str(named)], OutputFile=str(self.base / 'bad.json'), Generation=7,
                  HostUuid=host()['hardware_uuid'], ComputerName='bad\nname')
        self.assertFalse((self.base / 'bad.json').exists())
        marker = target / 'usb-config.json'
        data = json.loads(marker.read_text()); data.pop('registry_schema'); marker.write_text(json.dumps(data))
        before = (target / 'gpu-config/registry.json').read_bytes()
        self.call('Deploy', expected=False, Inputs=[str(named)], UsbRoot=str(target))
        self.assertEqual((target / 'gpu-config/registry.json').read_bytes(), before)

    def test_gui_plans_exclude_unrelated_hidden_fields(self):
        script = str(ROOT / 'gui/windows-gui.ps1').replace("'", "''")
        code = ("& '" + script + "' -PlanOnly -Operation Status -Inputs @('registry.json') "
                "-StorageDirectory 'INVALID/IGNORED' -Volume 'Z:' -UsbRoot 'X:\\'")
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True, env={**os.environ,
                                    'POWERSHELL_TELEMETRY_OPTOUT': '1', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                    'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                    'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['request'], {'operation': 'Status', 'inputs': ['registry.json']})

    def test_old_observations_can_be_collected_merged_and_deployed(self):
        value = host()
        value['checked_at_unix'] = 1000
        value['fast_startup_enabled'] = True
        source = self.write('old-observation.json', [value])
        merged = self.base / 'merged-old.json'
        self.call('Merge', Inputs=[str(source)], OutputFile=str(merged), Generation=2)
        target = self.usb()
        self.call('Deploy', Inputs=[str(merged)], UsbRoot=str(target))
        collected = self.base / 'collected-old.json'
        self.call('Collect', UsbRoot=str(target), OutputFile=str(collected))
        self.assertEqual(fleet.load(collected)['records'][0]['checked_at_unix'], 1000)

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
        (path / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'registry_schema': 2, 'root_version': version}))
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
        duplicate.write_text(json.dumps({'schema': payload['schema'], 'payload_base64': base64.b64encode(raw_bytes).decode(),
                                         'sha256': hashlib.sha256(raw_bytes).hexdigest()}))
        merged = self.base / (suffix + '-merged.json')
        self.call('Merge', Inputs=[str(raw), str(duplicate)], OutputFile=str(merged), Generation=2, RootVersion='0.9.1-shared')
        registry = fleet.load(merged)
        self.assertEqual(registry['records'], [fleet.record(other)])
        usb = self.base / (suffix + '-usb')
        usb.mkdir()
        (usb / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'registry_schema': 2, 'root_version': '0.9.1-shared'}))
        self.call('Deploy', Inputs=[str(merged)], UsbRoot=str(usb))
        self.call('Deploy', Inputs=[str(merged)], UsbRoot=str(usb))  # idempotent
        env = (usb / ('gpu-config/hosts/' + value['hardware_uuid'] + '.env')).read_bytes()
        self.assertEqual(len(env), 1024)
        self.assertIn(b'gpu_targets=unconfigured\ngpu_default=0\n', env)
        collected = self.base / (suffix + '-collected.json')
        self.call('Collect', UsbRoot=str(usb), OutputFile=str(collected))
        self.assertEqual(fleet.load(collected), registry)
        self.call('Status', Inputs=[str(collected)])

    def test_deploy_across_root_releases_preserves_registry_generation_and_evidence(self):
        value = host(test=True)
        raw = self.write('old.json', [value], version='0.9.0-shared', generation=3)
        usb = self.usb()
        self.call('Deploy', Inputs=[str(raw)], UsbRoot=str(usb))
        result = fleet.load(usb / 'gpu-config/registry.json')
        self.assertEqual(result['root_version'], '0.9.0-shared')
        self.assertEqual(result['generation'], 3)
        self.assertEqual(result['records'][0], fleet.record(value))
        # A legacy origin label alone may change without changing the data generation.
        relabelled = self.write('relabelled.json', [value], version='0.9.2-shared', generation=3)
        self.call('Deploy', Inputs=[str(relabelled)], UsbRoot=str(usb))
        self.assertEqual(fleet.load(usb / 'gpu-config/registry.json')['generation'], 3)
        future = self.write('future.json', [value], version='0.9.2-shared', generation=5)
        self.call('Deploy', Inputs=[str(future)], UsbRoot=str(usb))
        result = fleet.load(usb / 'gpu-config/registry.json')
        self.assertEqual(result['generation'], 5)
        self.assertEqual(result['records'], [fleet.record(value)])

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

    def test_unknown_schema_is_rejected_while_root_release_differences_are_accepted(self):
        old = self.write('old.json', [host(test=True)], version='0.9.0-shared')
        future = self.write('future.json', [host(test=True)], version='0.9.2-shared')
        output = self.base / 'bad-merge.json'
        self.call('Merge', Inputs=[str(old), str(future)], OutputFile=str(output), Generation=2)
        self.assertEqual(fleet.load(output)['schema'], 2)
        usb = self.usb('0.9.0-shared')
        current = self.write('current.json', [host(test=True)])
        self.call('Deploy', Inputs=[str(current)], UsbRoot=str(usb))
        before = (usb / 'gpu-config/registry.json').read_bytes()
        invalid = fleet.load(future)
        invalid['schema'] = 3
        future.write_bytes(fleet.encode(invalid))
        self.call('Deploy', expected=False, Inputs=[str(future)], UsbRoot=str(usb))
        self.assertEqual((usb / 'gpu-config/registry.json').read_bytes(), before)

    def test_merge_retains_origin_information_without_using_it_for_compatibility(self):
        value = host()
        old = self.write('old.json', [value], version='0.9.0-shared')
        new = self.write('new.json', [value])
        for index, extra in enumerate(({}, {'UsbRoot': str(self.usb())})):
            output = self.base / f'merged-{index}.json'
            self.call('Merge', Inputs=[str(old), str(new)], OutputFile=str(output), Generation=2, **extra)
            self.assertEqual(fleet.load(output)['root_version'], '0.9.1-shared' if extra else '0.9.0-shared')

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
                   {'fast_startup_enabled': None}, {'secure_boot_enabled': False}, {'checked_at_unix': -1}]
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
