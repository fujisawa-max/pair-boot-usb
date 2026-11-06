#!/usr/bin/env python3
"""Run isolated source fixtures and shell syntax checks; no real device writes."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit(
        'Tests require Python 3.11 or newer; running '
        + '.'.join(map(str, sys.version_info[:3]))
        + '. No tests were executed. Run this script with python3.11 or newer; '
          'see docs/build.md. Do not replace the operating system Python.')

import importlib.util
from pathlib import Path
import subprocess
import unittest

root = Path(__file__).resolve().parents[1]
suite = unittest.TestSuite()
for number, path in enumerate(sorted((root / 'tests').glob('test-*.py'))):
    spec = importlib.util.spec_from_file_location(f'fixture_{number}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
for folder in ('runtime', 'scripts'):
    for path in sorted((root / folder).rglob('*.sh')):
        shell = 'bash' if path.read_text().startswith('#!/bin/bash') else 'sh'
        subprocess.run([shell, '-n', str(path)], check=True)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
