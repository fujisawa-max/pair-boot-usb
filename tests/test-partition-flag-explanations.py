"""Windows safety flag diagnostics use mock APIs; never edit real partitions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = ROOT / 'build/powershell-7.5.4/pwsh'


@unittest.skipUnless(PWSH.exists(), 'Optional workspace PowerShell needed')
class PartitionFlagExplanations(unittest.TestCase):
    def test_hidden_true_unknown_missing_and_wrong_types_stay_closed(self):
        cases = (
            ('IsHidden=$true;', 'IsHidden=True', '非表示領域（IsHidden=True）'),
            ('IsHidden=$null;', 'value=<null>', '非表示領域と断定はできません'),
            ("IsHidden='False';", 'type=System.String', '非表示領域と断定はできません'),
            ('IsHidden=0;', 'type=System.Int32', '非表示領域と断定はできません'),
            ('', 'property missing', '非表示領域と断定はできません'),
        )
        env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
               'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
               'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            work = Path(folder)
            for field, detail, explanation in cases:
                with self.subTest(field=field):
                    output = work / 'must-not-exist.json'
                    script = work / 'fixture.ps1'
                    script.write_text('''
function Get-CimInstance { [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef'} }
function Get-Volume { [pscustomobject]@{FileSystem='NTFS';Size=8589930496} }
function Get-Partition { [pscustomobject]@{Guid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';Offset=1048576;Size=8589934592;IsBoot=$false;IsSystem=$false;''' + field + '''} }
function Get-ItemProperty { throw 'Must not query power after flag rejection' }
& "''' + str(ROOT / 'toolkit/export-standard-inventory.ps1') + '" -Volume F: -PersonalUseOsVolume -PersonalUseConfirmation "個人利用で許可" -OutputFile "' + str(output) + '"\nexit $LASTEXITCODE\n', encoding='utf-8-sig')
                    result = subprocess.run([str(PWSH), '-NoProfile', '-NonInteractive', '-File', str(script)],
                                            env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(explanation, result.stderr)
                    self.assertIn(detail, result.stderr)
                    self.assertNotIn('CategoryInfo', result.stderr)
                    self.assertNotIn('Must not query power', result.stderr)
                    self.assertFalse(output.exists())
