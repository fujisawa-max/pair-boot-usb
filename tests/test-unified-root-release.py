"""Unified releases upgrade legacy roots and retain settings on file fixtures."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
fixtures = runpy.run_path(str(ROOT / 'tests/test-root-revisions.py'))
api, policy = fixtures['api'], fixtures['policy']


class UnifiedRootReleaseTests(unittest.TestCase):
    def prepare(self, revision=4, release='0.9.2'):
        self.fixture = fixtures['RevisionTests']()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare(old=revision, new=1)
        value = self.fixture.manifest
        value.update(version=release, project_version=release)
        value.pop('root_revision')
        self.rewrite(self.fixture.master, value)
        value['sha256'] = api.digest(self.fixture.master)

    def rewrite(self, path, info):
        metadata = self.fixture.parent / 'rewritten-release.json'
        metadata.write_text(json.dumps({key: value for key, value in info.items()
                                       if key not in ('size', 'sha256', 'persistent_state')}))
        target = '/usr/lib/gpu-root/image.json'
        for command in ('rm ' + target, 'write ' + str(metadata) + ' ' + target):
            subprocess.run(['debugfs', '-w', '-R', command, str(path)], check=True, capture_output=True)

    def current_release(self, release):
        info = {**self.fixture.manifest, 'version': release, 'project_version': release}
        self.rewrite(self.fixture.target, info)
        receipt = self.fixture.receipt
        receipt.pop('root_revision', None)
        receipt.update(version=release, original_sha256=api.digest(self.fixture.target))
        (self.fixture.parent / 'root-image.json').write_text(json.dumps(receipt))

    def test_0_9_0_and_r3_r4_upgrade_automatically_without_state_changes(self):
        for revision in (0, 3, 4):
            with self.subTest(revision=revision):
                self.prepare(revision)
                before = api.digest(self.fixture.target)
                self.fixture.run_replace(ask=lambda *_: self.fail('Upgrade must be automatic'))
                receipt = json.loads((self.fixture.parent / 'root-image.json').read_text())
                self.assertEqual(receipt['version'], '0.9.2')
                self.assertEqual(receipt['boot_abi'], 1)
                self.assertNotIn('root_revision', receipt)
                backup = next(self.fixture.parent.glob('root-before-update-*.img'))
                self.assertEqual(api.digest(backup), before)
                self.assertEqual(self.fixture.state.read_bytes(), b'retained fixture settings')

    def test_newer_release_replaces_previous_unified_root(self):
        self.prepare(release='0.9.3')
        self.current_release('0.9.2')
        self.fixture.run_replace()
        self.assertEqual(api.digest(self.fixture.target), self.fixture.manifest['sha256'])

    def test_older_release_can_keep_current_or_downgrade_with_explicit_choice(self):
        for action in ('keep', 'replace', 'poweroff'):
            with self.subTest(action=action):
                self.prepare()
                self.current_release('0.9.3')
                before = api.digest(self.fixture.target)
                def choose(old, new, can_keep, can_replace):
                    self.assertEqual((old, new, can_keep, can_replace), ('0.9.3', '0.9.2', True, True))
                    return action
                if action == 'poweroff':
                    with self.assertRaises(api.RootPoweroff):
                        self.fixture.run_replace(choose=choose)
                else:
                    self.fixture.run_replace(choose=choose)
                expected = self.fixture.manifest['sha256'] if action == 'replace' else before
                self.assertEqual(api.digest(self.fixture.target), expected)
                self.assertEqual(self.fixture.state.read_bytes(), b'retained fixture settings')

    def test_same_release_different_distribution_hash_stops_before_transaction(self):
        self.prepare()
        self.current_release('0.9.2')
        with self.assertRaisesRegex(api.SafetyError, 'Same root release'):
            self.fixture.run_replace()
        self.assertFalse(list(self.fixture.parent.glob('.root-replace*')))

    def test_same_distribution_can_keep_mutable_root(self):
        self.prepare()
        shutil.copyfile(self.fixture.master, self.fixture.target)
        receipt = self.fixture.receipt
        receipt.pop('root_revision')
        receipt.update(version='0.9.2', original_sha256=self.fixture.manifest['sha256'])
        (self.fixture.parent / 'root-image.json').write_text(json.dumps(receipt))
        content = self.fixture.parent / 'settings.txt'
        content.write_text('synthetic mutable root file')
        subprocess.run(['debugfs', '-w', '-R', 'write ' + str(content) + ' /settings.txt',
                        str(self.fixture.target)], check=True, capture_output=True)
        before = api.digest(self.fixture.target)
        self.assertNotEqual(before, self.fixture.manifest['sha256'])
        self.fixture.run_replace()
        self.assertEqual(api.digest(self.fixture.target), before)
        self.assertFalse(list(self.fixture.parent.glob('root-before-update-*')))

    def test_unified_identity_rejects_split_versions_revisions_and_unknown_state(self):
        self.prepare()
        info = self.fixture.manifest
        self.assertTrue(policy['compatible'](info, fixtures['KERNEL'], info))
        for changed in ({'root_revision': 1}, {'project_version': '0.9.3'},
                        {'version': '0.9.2-shared'}, {'state_schema': 2}, {'boot_abi': 2}):
            with self.subTest(changed=changed):
                self.assertFalse(policy['compatible']({**info, **changed}, fixtures['KERNEL'], info))


if __name__ == '__main__':
    unittest.main()
