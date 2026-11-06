"""Custom folders must stay one safe NTFS component across all consumers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = load('storage_fleet', 'runtime/fleet-registry.py')
boot = load('storage_boot', 'runtime/gpu-root.py')
GOOD = ('GPU-Linux', 'example-GPU-Linux', 'Lab-AI', 'team_01.root', 'A', 'A' * 64)
BAD = ('', '.', '..', '../Windows', '/models', 'D:\\models', 'team/sub', 'team\\sub',
       'Windows', 'WINDOWS.old', 'Users', 'ProgramData', 'BOOT', 'EFI', 'Recovery',
       'CON', 'con.txt', 'LPT1', 'COM9.data', 'NUL', 'root.', 'root ',
       'with spaces', 'Lab\n', 'Lab\r\n', 'Lab\t', '日本語', 'A' * 65, '$Recycle.Bin', 'PROGRA~1')


class StorageArgumentsTests(unittest.TestCase):
    def test_python_policies_match_and_paths_stay_below_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in GOOD:
                with self.subTest(name=name):
                    self.assertEqual(fleet.validate_storage_directory(name), name)
                    self.assertEqual(boot.validate_storage_directory(name), name)
                    parent, models = boot.storage_paths(Path(folder), name)
                    self.assertEqual(parent.parent, Path(folder))
                    self.assertEqual(models, parent / 'Models')
            for name in BAD:
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        fleet.validate_storage_directory(name)
                    with self.assertRaises(boot.SafetyError):
                        boot.storage_paths(Path(folder), name)

    @unittest.skipUnless((ROOT / 'build/powershell-7.5.4/pwsh').exists(), 'Workspace PowerShell optional')
    def test_powershell_defaults_and_validation_match_python(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            root = Path(folder)
            names = root / 'names.json'
            names.write_text(json.dumps({'good': GOOD, 'bad': BAD}))
            script = root / 'parameters.ps1'
            script.write_text('''$ErrorActionPreference='Stop'
$command = Get-Command "''' + str(ROOT / 'toolkit/export-standard-inventory.ps1') + '''"
$validation = @($command.Parameters['StorageDirectory'].Attributes | Where-Object { $_ -is [System.Management.Automation.ValidateScriptAttribute] })[0].ScriptBlock
$names = Get-Content -Raw "''' + str(names) + '''" | ConvertFrom-Json
foreach ($name in $names.good) { if (-not ($name | ForEach-Object $validation)) { throw "Valid name rejected: $name" } }
foreach ($name in $names.bad) { if ($name | ForEach-Object $validation) { throw "Unsafe name accepted: $name" } }
$ast = [System.Management.Automation.Language.Parser]::ParseFile($command.Path, [ref]$null, [ref]$null)
foreach ($parameter in $ast.ParamBlock.Parameters) {
  if ($parameter.Name.VariablePath.UserPath -eq 'Volume' -and $parameter.DefaultValue.Value -ne 'D:') { throw 'Wrong default volume' }
  if ($parameter.Name.VariablePath.UserPath -eq 'StorageDirectory' -and $parameter.DefaultValue.Value -ne 'GPU-Linux') { throw 'Wrong default folder' }
}
'PASS'
''')
            env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                   'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                   'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
            result = subprocess.run([str(ROOT / 'build/powershell-7.5.4/pwsh'), '-NoProfile', '-NonInteractive', '-File', str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
