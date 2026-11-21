"""Check the PAIR launcher's arguments using a stand-in for llama.cpp."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LlamaServerLaunchTests(unittest.TestCase):
    def test_preset_is_optional_and_applies_to_pair_router_only(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            preset = folder / 'llama-presets.ini'
            engine = folder / 'engine'
            engine.write_text(f'#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            engine.chmod(0o755)
            launcher = folder / 'launch.sh'
            text = (ROOT / 'runtime/llama-server-launch.sh').read_text()
            text = text.replace('/models/llama-presets.ini', str(preset))
            launcher.write_text(text.replace('/opt/llama.cpp/bin/llama-server.real', str(engine)))

            def arguments(*args):
                return json.loads(subprocess.check_output(
                    ['sh', str(launcher), *args], env={'PATH': '/usr/bin:/bin'}, text=True))

            router = ['--models-dir', '/models']
            self.assertEqual(arguments(*router), router)
            preset.write_text('version = 1\n')
            self.assertEqual(arguments(*router), router + ['--models-preset', str(preset)])
            local = ['--model', '/models/Example Model.gguf']
            self.assertEqual(arguments(*local), local)
            explicit = router + ['--models-preset=/tmp/explicit.ini']
            self.assertEqual(arguments(*explicit), explicit)
            preset.rename(folder / 'external.ini')
            preset.symlink_to(folder / 'external.ini')
            self.assertEqual(arguments(*router), router)
