"""Media variants are tested using ordinary files and fake certificate checks."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-usb.py'))
STAGE = runpy.run_path(str(ROOT / 'scripts/prepare-shared-usb-tools.py'))
CLONE = runpy.run_path(str(ROOT / 'scripts/clone-appliance.py'))
PRODUCER = runpy.run_path(str(ROOT / 'scripts/prepare-update-bundle.py'))

class MediaProfileTests(unittest.TestCase):
    def args(self, profile):
        return argparse.Namespace(profile=profile, signed_modules=ROOT/'build/signed',
            appliance_output=ROOT/'build/fixture-appliance',tools_output=ROOT/'build/fixture-config',
            fedora_output=ROOT/'build/fixture-fedora',output=ROOT/'build/fixture-image')

    def test_loan_plan_omits_fedora_and_reuses_root_only_when_requested(self):
        args=self.args('loan');commands=BUILD['image_commands'](args)
        for cmd in commands:
            self.assertNotIn('--fedora-tree',cmd);self.assertNotIn('--fedora-source',cmd)
        self.assertTrue(any(c[1].endswith('/build-root-appliance.py') for c in commands))
        args.reuse_appliance=ROOT/'build/existing'
        commands=BUILD['image_commands'](args)
        self.assertFalse(any(c[1].endswith('/build-root-appliance.py') for c in commands))
        clone=next(c for c in commands if c[1].endswith('/clone-appliance.py'))
        self.assertEqual(clone[clone.index('--source')+1],str(args.reuse_appliance))
        self.assertFalse(any('prepare-fedora-live.py' in str(c) for c in BUILD['prepare_commands']('loan')))

    def test_loan_staging_contains_data_only_and_a_separate_portable_manager(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            root=Path(folder);cert=root/'fixture.der';cert.write_bytes(b'synthetic certificate')
            output=root/'config';manager=root/'manager';fedora=root/'must-not-exist'
            argv=['prepare-shared-usb-tools.py','--profile','loan','--output',str(output),
                  '--manager-output',str(manager),'--fedora-output',str(fedora),'--certificate',str(cert)]
            with patch.object(sys,'argv',argv),patch('subprocess.run',return_value=subprocess.CompletedProcess([],0)):
                STAGE['main']()
            self.assertFalse(fedora.exists())
            self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()},
                             {'usb-config.json','gpu-config/registry.json','MEDIA-INFO.txt'})
            self.assertEqual(json.loads((output/'usb-config.json').read_text())['media_profile'],'loan')
            self.assertEqual(runpy.run_path(str(ROOT/'runtime/fleet-registry.py'))['load'](output/'gpu-config/registry.json')['records'],[])
            self.assertTrue((manager/'tools/fleet.ps1').is_file());self.assertTrue((manager/'gui/windows-gui.ps1').is_file())
            self.assertTrue((manager/'start-windows-gui.cmd').is_file())
            self.assertFalse((manager/'usb-config.json').exists())
            self.assertEqual((manager/'LICENSE').read_bytes(),(ROOT/'LICENSE').read_bytes())
            for name in ('backups','data','gpu-config'):self.assertTrue((manager/name).is_dir())
            before=(output/'usb-config.json').read_bytes()
            with patch.object(sys,'argv',argv),self.assertRaises(ValueError):STAGE['main']()
            self.assertEqual((output/'usb-config.json').read_bytes(),before)

    def test_bad_certificate_leaves_both_outputs_absent(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            root=Path(folder);cert=root/'fixture.der';cert.write_bytes(b'bad')
            argv=['prepare-shared-usb-tools.py','--profile','loan','--output',str(root/'config'),
                  '--manager-output',str(root/'manager'),'--certificate',str(cert)]
            with patch.object(sys,'argv',argv),patch('subprocess.run',side_effect=subprocess.CalledProcessError(1,[])):
                with self.assertRaises(subprocess.CalledProcessError):STAGE['main']()
            self.assertFalse((root/'config').exists());self.assertFalse((root/'manager').exists())

    def test_loan_contract_cannot_be_confused_with_standard_or_include_tools(self):
        standard,_,old,oldsha=PRODUCER['contract']()
        loan,root,layout,sha=PRODUCER['contract']('loan')
        self.assertNotEqual(sha,oldsha);self.assertNotEqual(layout['id'],old['id'])
        self.assertEqual(layout['managed_roots'],[])
        self.assertEqual(layout['managed_exact_paths'],['MEDIA-INFO.txt'])
        self.assertNotIn('fedora',[p['role'] for p in layout['partitions']])
        self.assertEqual(layout['partitions'][-1]['number'],4)
        original=Path('inputs/updater-contract/v1/inputs/production-contract-v1.lock.json')
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),oldsha)
        fixture=standard['load'](ROOT/'inputs/updater-contract/v1/tests/fixtures/production-v1/manifest.json')
        with self.assertRaises(ValueError):loan['check'](fixture,'manifest',root)
        self.assertFalse(PRODUCER['is_managed']('tools/fleet.ps1',layout))
        self.assertFalse(PRODUCER['is_managed']('gpu-config/registry.json',layout))
        self.assertTrue(PRODUCER['is_managed']('MEDIA-INFO.txt',layout))

    def test_reuse_rejects_different_release_and_corruption_before_copying(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            root=Path(folder);source=root/'source';tree=source/'usb-tree/gpu-linux';tree.mkdir(parents=True)
            release=json.loads((ROOT/'config/release.json').read_text())
            data=b'fixture root';(tree/'root.img').write_bytes(data);(tree/'state.img').write_bytes(data)
            meta={**release,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                  'persistent_state':{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}}
            meta['version']='0.0.0';(tree/'master.json').write_text(json.dumps(meta))
            with patch('subprocess.run') as run,self.assertRaises(ValueError):CLONE['clone'](source,root/'out','loan')
            run.assert_not_called();self.assertFalse((root/'out').exists())
            meta['version']=release['version'];meta['sha256']='0'*64;(tree/'master.json').write_text(json.dumps(meta))
            with patch('subprocess.run') as run,self.assertRaises(ValueError):CLONE['clone'](source,root/'out','loan')
            run.assert_not_called();self.assertFalse((root/'out').exists())
            self.assertEqual((tree/'root.img').read_bytes(),data)

    def test_portable_windows_defaults_do_not_follow_the_selected_usb(self):
        shell=ROOT/'build/powershell-7.5.4/pwsh'
        if not shell.is_file():self.skipTest('Optional workspace PowerShell needed')
        result=subprocess.run([str(shell),'-NoProfile','-File',str(ROOT/'tests/windows-portable-manager-fixture.ps1'),
                               '-GuiDirectory',str(ROOT/'gui')],capture_output=True,text=True,
                              env={**os.environ,'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT':'1'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_reuse_can_edit_readonly_copied_metadata_without_changing_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            root=Path(folder);source=root/'source';tree=source/'usb-tree'
            (tree/'gpu-linux').mkdir(parents=True)
            release=json.loads((ROOT/'config/release.json').read_text())
            data=b'synthetic image'
            for name in ('root.img','state.img'):(tree/'gpu-linux'/name).write_bytes(data)
            meta={**release,'usb_data_uuid':'00000000-0000-0000-0000-000000000001',
                  'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                  'persistent_state':{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}}
            original=json.dumps(meta);(tree/'gpu-linux/master.json').write_text(original)
            paths=['gpu-linux/master.json','boot/grub/grub.cfg','EFI/BOOT/grub.cfg','EFI/debian/grub.cfg']
            for relative in paths:
                path=tree/relative;path.parent.mkdir(parents=True,exist_ok=True)
                if relative!='gpu-linux/master.json':path.write_text('Original Fedora menu\n')
                path.chmod(0o444)
            real_run=subprocess.run
            def run(command,**kwargs):
                if command[0]=='cp':return real_run(command,**kwargs)
                self.assertTrue(command[1].endswith('/validate-appliance.py'))
                return subprocess.CompletedProcess(command,0)
            with patch('subprocess.run',side_effect=run):CLONE['clone'](source,root/'out','loan')
            output=root/'out/usb-tree'
            self.assertEqual((tree/'gpu-linux/master.json').read_text(),original)
            self.assertNotEqual(json.loads((output/'gpu-linux/master.json').read_text())['usb_data_uuid'],meta['usb_data_uuid'])
            for relative in paths:
                self.assertEqual((tree/relative).stat().st_mode&0o777,0o444)
                self.assertEqual((output/relative).stat().st_mode&0o777,0o444)
            for relative in paths[1:]:
                self.assertNotIn('Fedora',(output/relative).read_text())
                self.assertEqual((tree/relative).read_text(),'Original Fedora menu\n')
            for name in ('root.img','state.img'):self.assertEqual((output/'gpu-linux'/name).read_bytes(),data)

    def test_copied_metadata_never_follows_a_symlink(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            root=Path(folder);target=root/'original';target.write_text('retain');target.chmod(0o444)
            alias=root/'alias';alias.symlink_to(target)
            with self.assertRaises(ValueError):CLONE['write_copied_metadata'](alias,'replace')
            directory=root/'directory';directory.mkdir();(directory/'metadata').write_text('retain')
            (root/'linked-directory').symlink_to(directory,target_is_directory=True)
            with self.assertRaises(ValueError):CLONE['write_copied_metadata'](root/'linked-directory/metadata','replace')
            self.assertEqual(target.read_text(),'retain');self.assertEqual(target.stat().st_mode&0o777,0o444)
            self.assertEqual((directory/'metadata').read_text(),'retain')
