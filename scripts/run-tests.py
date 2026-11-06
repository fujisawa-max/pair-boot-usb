#!/usr/bin/env python3
"""Run isolated source fixtures and shell syntax checks; no real device writes."""
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
