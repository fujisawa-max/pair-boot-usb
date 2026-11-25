"""Verify a staged boot authorization using only synthetic files and signing keys."""
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(ROOT / 'scripts/boot-integrity-manifest.py'))


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL is needed for the synthetic signing fixture')
class BootIntegrityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('veritysetup'), 'veritysetup is needed for the optional root hash fixture')
    def test_verity_artifacts_bound_to_boot_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            for name in API['FILES']:
                path = tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'synthetic fixture')
            root = tree / 'gpu-linux/root.img'
            root.write_bytes(b'R' * (4096 * 4))
            master = tree / 'gpu-linux/master.json'
            master.write_text(json.dumps({'version': '1.1.1', 'secure_boot_signed_artifacts': True,
                                          'size': root.stat().st_size,
                                          'sha256': hashlib.sha256(root.read_bytes()).hexdigest()}))
            output = tree / 'verity'
            API['prepare_verity'](tree, output)
            with self.assertRaisesRegex(ValueError, 'fresh'):
                API['prepare_verity'](tree, output)
            for artifact in output.iterdir():
                shutil.copyfile(artifact, root.parent / artifact.name)
            files = json.loads(API['manifest'](tree, '1.1.1', 'loan'))['files']
            for name in API['VERITY_FILES']:
                self.assertEqual(files[name], API['record'](tree / name))
            runtime = API['RUNTIME']
            copied = tree / 'copied.verity'
            parameters = runtime['verity_parameters'](
                tree, files, files['gpu-linux/root.img'], copied)
            table = runtime['verity_table'](parameters, '/dev/loop0', '/dev/loop1')
            self.assertTrue(table.startswith('0 32 verity 1 /dev/loop0 /dev/loop1 4096 4096 4 1 sha256 '))
            subprocess.run(
                ['veritysetup', '--no-superblock', '--hash-offset=4096', '--format=1', '--hash=sha256',
                 '--data-block-size=4096', '--hash-block-size=4096', f'--data-blocks={parameters[0]}',
                 f'--salt={parameters[2]}', 'verify', str(root), str(copied), parameters[1]],
                check=True, capture_output=True)
            hashes = root.parent / 'root.verity'
            original = hashes.read_bytes()
            hashes.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, 'hash image differs'):
                runtime['verity_parameters'](tree, files, files['gpu-linux/root.img'])
            hashes.write_bytes(original)
            descriptor = root.parent / 'root.verity.json'
            metadata = json.loads(descriptor.read_bytes())
            metadata['root_hash'] = '0' * 64
            descriptor.write_text(json.dumps(metadata))
            with self.assertRaises(subprocess.CalledProcessError):
                API['manifest'](tree, '1.1.1', 'loan')
            descriptor.unlink()
            with self.assertRaises(FileNotFoundError):
                API['manifest'](tree, '1.1.1', 'loan')

    def test_signed_tree_rejects_substitutions_and_wrong_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            tree = base / 'usb-tree'
            for name in API['FILES']:
                path = tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'Synthetic {name}')
            for name in API['MENUS']:
                (tree / name).write_text('menuentry "Normal / baseline" {}\n')
            root = tree / 'gpu-linux/root.img'
            master = tree / 'gpu-linux/master.json'
            master.write_text(json.dumps({'version': '1.1.1', 'secure_boot_signed_artifacts': True,
                                          'size': root.stat().st_size,
                                          'sha256': hashlib.sha256(root.read_bytes()).hexdigest()}))
            approved = self.signer(base, 'approved')
            metadata = json.loads(master.read_bytes())
            metadata['boot_integrity_signer_sha256'] = hashlib.sha256(approved.read_bytes()).hexdigest()
            master.write_text(json.dumps(metadata))
            payload = base / 'boot-integrity.json'
            payload.write_bytes(API['manifest'](tree, '1.1.1', 'loan'))
            unapproved = self.signer(base, 'unapproved')
            signature = base / 'signature.der'
            self.openssl('cms', '-sign', '-binary', '-md', 'sha256', '-in', payload,
                         '-signer', approved, '-inkey', approved.with_suffix('.key'),
                         '-outform', 'DER', '-out', signature)
            verify = API['verify']
            verify(tree, '1.1.1', 'loan', payload, signature, approved)
            runtime = API['RUNTIME']
            absent = base / 'absent.pem'
            self.assertIsNone(runtime['verify_master'](tree, absent))
            with self.assertRaises(FileNotFoundError):
                runtime['verify_master'](tree, approved)
            (tree / runtime['MANIFEST']).write_bytes(payload.read_bytes())
            (tree / runtime['SIGNATURE']).write_bytes(signature.read_bytes())
            self.assertEqual(runtime['verify_master'](tree, approved), master.read_bytes())
            installed = base / 'installed-root.img'
            installed.write_bytes(root.read_bytes())
            signed_metadata = json.loads(runtime['verify_master'](tree, approved))
            runtime['verify_installed_root'](installed, signed_metadata)
            installed.write_bytes(b'X' * signed_metadata['size'])
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                runtime['verify_installed_root'](installed, signed_metadata)
            installed.write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'size'):
                runtime['verify_installed_root'](installed, signed_metadata)
            installed.unlink()
            installed.symlink_to(root)
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                runtime['verify_installed_root'](installed, signed_metadata)
            with self.assertRaisesRegex(ValueError, 'no embedded'):
                runtime['verify_master'](tree, absent)
            with self.assertRaises(subprocess.CalledProcessError):
                runtime['verify_master'](tree, unapproved)
            with self.assertRaises(subprocess.CalledProcessError):
                verify(tree, '1.1.1', 'loan', payload, signature, unapproved)
            with self.assertRaisesRegex(ValueError, 'profile'):
                verify(tree, '1.1.1', 'standard', payload, signature, approved)
            with self.assertRaisesRegex(ValueError, 'release'):
                verify(tree, '1.1.0', 'loan', payload, signature, approved)
            initrd = tree / 'boot/initramfs.img'
            original = initrd.read_bytes()
            initrd.write_bytes(original + b'tampered')
            with self.assertRaisesRegex(ValueError, 'Signed MASTER artifact differs'):
                runtime['verify_master'](tree, approved)
            with self.assertRaisesRegex(ValueError, 'Signed boot manifest differs'):
                verify(tree, '1.1.1', 'loan', payload, signature, approved)
            initrd.unlink()
            with self.assertRaises(FileNotFoundError):
                verify(tree, '1.1.1', 'loan', payload, signature, approved)
            outside = base / 'outside.img'
            outside.write_bytes(original)
            initrd.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                verify(tree, '1.1.1', 'loan', payload, signature, approved)
            initrd.unlink()
            initrd.write_bytes(original)
            root.write_bytes(root.read_bytes() + b'tampered')
            with self.assertRaisesRegex(ValueError, 'Root image'):
                verify(tree, '1.1.1', 'loan', payload, signature, approved)
            payload.write_bytes(payload.read_bytes() + b' ')
            with self.assertRaises(subprocess.CalledProcessError):
                verify(tree, '1.1.1', 'loan', payload, signature, approved)

    def signer(self, base, name):
        cert = base / (name + '.pem')
        self.openssl('req', '-new', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                     '-noenc', '-days', '2', '-subj', '/CN=Synthetic ' + name,
                     '-out', cert, '-keyout', cert.with_suffix('.key'))
        return cert

    @staticmethod
    def openssl(*args):
        subprocess.run(['openssl', *map(str, args)], check=True, capture_output=True, timeout=15)
