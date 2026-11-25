"""Producer metadata and synthetic disk fixtures; no physical USB access."""
import hashlib
import copy
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid
import zlib
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
P = runpy.run_path(str(ROOT / 'scripts/prepare-update-bundle.py'))
A = P['A']
S = runpy.run_path(str(ROOT / 'scripts/updater-source.py'))


class SourceFingerprintTests(unittest.TestCase):
    def test_receipt_and_manifest_id_do_not_distinguish_identical_media(self):
        release = {key: key for key in P['SOURCE_RELEASE_FIELDS']}
        files = [
            {'path': 'gui/windows-worker.ps1', 'bytes': 1, 'sha256': 'a' * 64},
            {'path': 'tools/version.txt', 'bytes': 2, 'sha256': 'b' * 64},
        ]
        initial = {'id': 'source-2', 'kind': 'built-media', 'release': release,
                   'managed_files': files}
        updated = {**initial, 'id': 'updated-1', 'kind': 'updated-media',
                   'prior_manifest_sha256': 'c' * 64, 'update_sequence': 4,
                   'managed_files': list(reversed(files))}
        with self.assertRaisesRegex(ValueError, 'source-2 and updated-1'):
            P['require_distinct_sources']([initial, updated])
        variant = {**initial, 'id': 'maintenance', 'managed_files': [
            {**files[0], 'sha256': 'd' * 64}, files[1]]}
        P['require_distinct_sources']([updated, variant])


def gpt(path, layout):
    size = (10094592 + 6291456 + 2048) * 512
    table = bytearray(128 * 128)
    for index, part in enumerate(p for p in layout['partitions'] if p['geometry'] == 'fixed'):
        offset = index * 128
        table[offset:offset+16] = uuid.UUID(part['type_guid']).bytes_le
        table[offset+16:offset+32] = uuid.UUID(int=index + 1).bytes_le
        struct.pack_into('<QQQ', table, offset+32, part['start_sector'], part['start_sector'] + part['sector_count']-1, 0)
    last = size // 512 - 1
    def header(current, backup, entries):
        data = bytearray(512); data[:8] = b'EFI PART'
        struct.pack_into('<IIIIQQQQ', data, 8, 0x10000, 92, 0, 0, current, backup, 2048, last-33)
        data[56:72] = uuid.UUID(int=100).bytes_le
        struct.pack_into('<QIII', data, 72, entries, 128, 128, zlib.crc32(table))
        struct.pack_into('<I', data, 16, zlib.crc32(data[:92]))
        return data
    with path.open('wb') as stream:
        stream.truncate(size)
        mbr = bytearray(512);mbr[450]=0xee;mbr[510:]=b'\x55\xaa'
        struct.pack_into('<II',mbr,454,1,last)
        stream.write(mbr); stream.write(header(1,last,2));stream.write(table)
        stream.seek((last-32)*512);stream.write(table);stream.write(header(last,1,last-32))


def fat(path, bad=None):
    total=131072; reserved=32; sectors=1024
    boot=bytearray(512);struct.pack_into('<H',boot,11,512);boot[13]=1
    struct.pack_into('<H',boot,14,reserved);boot[16]=2
    struct.pack_into('<II',boot,32,total,sectors);struct.pack_into('<I',boot,44,2)
    struct.pack_into('<H',boot,50,6);boot[71:82]=b'TEST-FAT   ';boot[510:]=b'\x55\xaa'
    table=bytearray(sectors*512)
    for cluster in range(4):struct.pack_into('<I',table,cluster*4,0x0fffffff)
    if bad=='cycle':struct.pack_into('<I',table,12,3)
    directory=bytearray(512);directory[:11]=b'A       TXT';directory[11]=32
    struct.pack_into('<H',directory,26,3);struct.pack_into('<I',directory,28,3)
    if bad=='duplicate':directory[32:64]=directory[:32]
    with path.open('wb') as stream:
        stream.truncate(total*512);stream.write(boot);stream.seek(6*512);stream.write(boot)
        stream.seek(reserved*512);stream.write(table);stream.write(table)
        stream.seek((reserved+2*sectors)*512);stream.write(directory);stream.write(b'abc')


class UpdateBundleTests(unittest.TestCase):
    def maintenance_fixture(self):
        module,root,_,_=P['contract']()
        pin=P['read_json'](ROOT/'inputs/updater-worker-merge-fix.json')
        fixture=module['load'](root/'tests/fixtures/production-v1/manifest.json')
        source=copy.deepcopy(fixture['accepted_sources'][1])
        source['id']='original-0-9-3'
        source['release'].update(version='0.9.3',root_version='0.9.3')
        records={stage:{'path':pin['managed_path'],**{k:pin[stage][k] for k in ('bytes','sha256')}}
                 for stage in ('before','after')}
        source['managed_files']=[records['before'],{'path':'tools/example.md','bytes':3,'sha256':hashlib.sha256(b'abc').hexdigest()}]
        candidate=copy.deepcopy(source);candidate['id']=pin['id'];candidate['managed_files'][0]=records['after']
        def committed(repository,commit):
            return records['before' if commit==pin['before']['commit'] else 'after']
        return module,root,source,candidate,committed

    def test_known_worker_maintenance_source_preserves_original_release_and_other_files(self):
        module,root,source,candidate,committed=self.maintenance_fixture();before=copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as directory:
            record=Path(directory)/'source.json';record.write_bytes(A['canonical'](candidate))
            with patch.dict(P['worker_merge_fix_source'].__globals__,committed_worker_record=committed):
                result,proof=P['worker_merge_fix_source'](record,[source],ROOT,module,root)
            self.assertEqual(result,candidate);self.assertEqual(source,before)
            self.assertEqual(result['release']['version'],'0.9.3')
            self.assertEqual(result['release'],source['release'])
            self.assertEqual(result['managed_files'][1],source['managed_files'][1])
            self.assertEqual(proof['required_updater'],'1.0.7')

    def test_worker_maintenance_record_cannot_override_arbitrary_source_fields(self):
        module,root,source,candidate,committed=self.maintenance_fixture()
        for change in ('id','version','root','certificate','worker-hash','worker-size','other-file','missing','extra','duplicate','receipt'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                value=copy.deepcopy(candidate)
                if change=='id':value['id']='other-maintenance'
                elif change=='version':value['release']['version']='0.99.0'
                elif change=='root':value['release']['root_sha256']='0'*64
                elif change=='certificate':value['release']['certificate_sha256']='0'*64
                elif change=='worker-hash':value['managed_files'][0]['sha256']='0'*64
                elif change=='worker-size':value['managed_files'][0]['bytes']+=1
                elif change=='other-file':value['managed_files'][1]['sha256']='0'*64
                elif change=='missing':value['managed_files'].pop()
                elif change=='extra':value['managed_files'].append({'path':'gpu-config/registry.json','bytes':1,'sha256':'1'*64})
                elif change=='duplicate':value['managed_files'].append(value['managed_files'][0])
                elif change=='receipt':value['prior_manifest_sha256']='1'*64
                record=Path(directory)/'source.json';record.write_bytes(A['canonical'](value))
                with patch.dict(P['worker_merge_fix_source'].__globals__,committed_worker_record=committed),self.assertRaises(ValueError):
                    P['worker_merge_fix_source'](record,[source],ROOT,module,root)

    def test_worker_maintenance_requires_a_unique_inspected_initial_base(self):
        module,root,source,candidate,committed=self.maintenance_fixture()
        changed=copy.deepcopy(source);changed['managed_files'][0]['sha256']='0'*64
        updated=copy.deepcopy(source);updated['kind']='updated-media'
        other=copy.deepcopy(source);other['id']='same-fingerprint'
        duplicate=copy.deepcopy(source);duplicate['id']=candidate['id']
        for sources in ([],[changed],[updated],[source,other],[source,duplicate]):
            with self.subTest(count=len(sources)),tempfile.TemporaryDirectory() as directory:
                record=Path(directory)/'source.json';record.write_bytes(A['canonical'](candidate))
                with patch.dict(P['worker_merge_fix_source'].__globals__,committed_worker_record=committed),self.assertRaises(ValueError):
                    P['worker_merge_fix_source'](record,sources,ROOT,module,root)

    def test_worker_maintenance_rejects_symlink_records_and_wrong_committed_fix(self):
        module,root,source,candidate,committed=self.maintenance_fixture()
        with tempfile.TemporaryDirectory() as directory:
            record=Path(directory)/'source.json';record.write_bytes(A['canonical'](candidate));alias=Path(directory)/'alias.json';alias.symlink_to(record)
            with patch.dict(P['worker_merge_fix_source'].__globals__,committed_worker_record=committed),self.assertRaisesRegex(ValueError,'Symlink'):
                P['worker_merge_fix_source'](alias,[source],ROOT,module,root)
            with patch.dict(P['worker_merge_fix_source'].__globals__,committed_worker_record=lambda *args:{'path':'gui/windows-worker.ps1','bytes':1,'sha256':'0'*64}),self.assertRaisesRegex(ValueError,'commit'):
                P['worker_merge_fix_source'](record,[source],ROOT,module,root)

    def test_received_contract_is_pinned_and_its_fixture_is_metadata_only(self):
        module, root, layout, sha = P['contract']()
        fixture=module['load'](root/'tests/fixtures/production-v1/manifest.json')
        module['check'](fixture,'manifest',root)
        self.assertEqual(sha, fixture['contract_lock_sha256'])
        self.assertEqual(layout['id'], fixture['layout']['id'])
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory);shutil.copytree(ROOT/'inputs/updater-contract',target/'inputs/updater-contract')
            shutil.copy2(ROOT/'inputs/updater-contract-input.json',target/'inputs/updater-contract-input.json')
            schema=target/'inputs/updater-contract/v1/schemas/production-v1/manifest.schema.json'
            schema.write_bytes(schema.read_bytes()+b' ')
            with patch.dict(P['contract'].__globals__,ROOT=target),self.assertRaisesRegex(ValueError,'SHA mismatch'):
                P['contract']()

    def test_protected_data_and_key_paths_are_not_selected_as_owned_payload(self):
        _,_,layout,_=P['contract']()
        for name in ('gpu-config/registry.json','gpu-config/hosts/example.env','data/info.json',
                     'candidates/pc.json','observations/state.json','signing-cert.der','usb-config.json'):
            self.assertFalse(P['is_managed'](name,layout),name)
        for name in ('tools/fleet.py','gui/ui-text.json','MEDIA-INFO.txt'):
            self.assertTrue(P['is_managed'](name,layout),name)

    def test_real_capacity_sparse_gpt_has_fixed_geometry_and_rejects_damage(self):
        _,_,layout,_=P['contract']()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'disk.img';gpt(path,layout)
            self.assertEqual([p['role'] for p in A['read_gpt'](path,layout)],['efi','master','logs','fedora'])
            with path.open('r+b') as stream:stream.seek(512+24);stream.write(b'x')
            with self.assertRaisesRegex(ValueError,'CRC'):
                A['read_gpt'](path,layout)
            alias=Path(directory)/'alias';alias.symlink_to(path)
            with self.assertRaisesRegex(ValueError,'Symlink'):
                A['read_gpt'](alias,layout)

    def test_fat32_reader_checks_chains_names_and_mirrors(self):
        for damage in (None,'cycle','duplicate','mirror','partition-padding'):
            with self.subTest(damage=damage),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'fat.img';fat(path,damage)
                if damage=='mirror':
                    with path.open('r+b') as stream:stream.seek((32+1024)*512+12);stream.write(b'\0')
                if damage=='partition-padding':
                    with path.open('ab') as stream:stream.write(b'\0'*(24*512))
                if damage and damage!='partition-padding':
                    with self.assertRaises(ValueError):A['Fat'](path,'TEST-FAT')
                else:
                    reader=A['Fat'](path,'TEST-FAT')
                    try:self.assertEqual(reader.record('A.TXT'),{'path':'A.TXT','bytes':3,'sha256':hashlib.sha256(b'abc').hexdigest()})
                    finally:reader.close()

    def test_copy_is_file_only_hash_bound_and_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.write_bytes(b'abc')
            target=root/'target'
            self.assertEqual(A['copy_region'](source,target,0,3)['sha256'],hashlib.sha256(b'abc').hexdigest())
            with self.assertRaises(FileExistsError):A['copy_region'](source,target,0,3)
            with self.assertRaisesRegex(ValueError,'SHA'):
                A['copy_region'](source,root/'bad',0,3,'0'*64)
            self.assertEqual(source.read_bytes(),b'abc')
            with self.assertRaises(ValueError):A['ordinary'](Path('/dev/null'))

    def test_producer_refuses_existing_bundle_before_reading_build_inputs(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as directory:
            output=Path(directory)/'bundle';output.mkdir();sentinel=output/'keep.txt'
            sentinel.write_bytes(b'Preserve existing output')
            with self.assertRaisesRegex(ValueError,'exists'):
                P['produce'](SimpleNamespace(output=output))
            self.assertEqual(sentinel.read_bytes(),b'Preserve existing output')

    def test_source_inventory_uses_actual_git_and_explicit_untracked_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            env={**os.environ,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
                 'GIT_AUTHOR_NAME':'Fixture','GIT_COMMITTER_NAME':'Fixture',
                 'GIT_AUTHOR_EMAIL':'fixture@example.invalid','GIT_COMMITTER_EMAIL':'fixture@example.invalid'}
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],env=env,stderr=subprocess.DEVNULL)
            git('init');(root/'README.md').write_text('original\n');git('add','README.md')
            git('-c','commit.gpgsign=false','commit','-m','Synthetic source fixture')
            (root/'README.md').write_text('changed\n');(root/'scripts').mkdir();(root/'scripts/new.py').write_text('pass\n')
            with self.assertRaisesRegex(ValueError,'explicitly include'):
                S['provenance'](root,root)
            value=S['provenance'](root,root,extras=['scripts/new.py'])
            self.assertEqual(value['commit'],git('rev-parse','HEAD').decode().strip())
            self.assertEqual(value['worktree_sha256'],hashlib.sha256(A['canonical'](value['files'])).hexdigest())
            self.assertEqual(len(value['uncommitted_changes']),2)
            with self.assertRaises(ValueError):S['inventory'](root,['.local/private.md'])
            with self.assertRaises(ValueError):S['inventory'](root,['scripts/../secret.key'])
            alias=root/'scripts/alias.py';alias.symlink_to(root/'README.md')
            with self.assertRaises(ValueError):S['inventory'](root,['scripts/alias.py'])

    def test_unknown_legacy_revisions_are_not_claimed_as_accepted_sources(self):
        for revision in (1,2,4):
            with self.assertRaisesRegex(ValueError,'legacy'):
                P['source_entry']({'release':{'version':'0.9.1','root_version':'0.9.1-shared',
                    'root_revision':revision,'registry_schema':1},'managed_files':[]},'fixture')

    def test_release_binding_rejects_mismatched_kernel_state_certificate_and_marker(self):
        cert=b'Synthetic public certificate bytes'
        master={'version':'0.99.0','boot_abi':1,'state_schema':1,'kernel_release':'fixture-kernel',
                'usb_data_uuid':str(uuid.UUID(int=1)),'sha256':'a'*64,'secure_boot_signed_artifacts':True}
        inside={'version':'0.99.0','boot_abi':1,'state_schema':1,'kernel_release':'fixture-kernel',
                'module_signing_certificate_sha256':hashlib.sha256(cert).hexdigest()}
        marker={'schema':1,'kind':'gpu-linux-config','root_version':'0.99.0','registry_schema':2}
        self.assertEqual(P['check_release'](master,inside,cert,marker)['version'],'0.99.0')
        for key,value in (('kernel_release','other'),('state_schema',2),('boot_abi',2),
                          ('module_signing_certificate_sha256','0'*64)):
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Inconsistent'):
                P['check_release'](master,{**inside,key:value},cert,marker)
        with self.assertRaisesRegex(ValueError,'Inconsistent'):
            P['check_release'](master,inside,cert,{**marker,'root_version':'0.9.3'})

    def test_standard_image_plan_can_include_producer_without_changing_plain_image(self):
        build=runpy.run_path(str(ROOT/'scripts/build-usb.py'))
        args=SimpleNamespace(signed_modules=ROOT/'build/signed',appliance_output=ROOT/'build/appliance-test',
            tools_output=ROOT/'build/tools-test',fedora_output=ROOT/'build/fedora-test',output=ROOT/'build/image-test',
            update_bundle=False,source_extra=[],accepted_image_directory=[],accepted_bundle=[],update_output=None,
            update_sequence=1,no_previous_source=True)
        plain=build['image_commands'](args)
        self.assertEqual(len(plain),9)
        args.update_bundle=True;commands=build['image_commands'](args)
        self.assertIn('record-build-source.py',commands[0][1])
        self.assertIn('prepare-update-bundle.py',commands[-1][1])
        self.assertEqual(Path(commands[-1][commands[-1].index('--output')+1]),
                         ROOT/'build/update-bundles'/build['VERSION']/'standard/sequence-1')
        self.assertEqual(commands[1:10],plain)
        args.accepted_worker_merge_fix=ROOT/'build/source-record.json'
        commands=build['image_commands'](args)
        self.assertEqual(commands[-1][-2:],['--accepted-worker-merge-fix',str(args.accepted_worker_merge_fix)])
        args.update_output=args.output
        with self.assertRaisesRegex(ValueError,'overlap'):build['validate_image_outputs'](args)

    def test_default_update_source_uses_the_latest_completed_archive_for_the_profile(self):
        build=runpy.run_path(str(ROOT/'scripts/build-usb.py'))
        with tempfile.TemporaryDirectory() as directory:
            repository=Path(directory)
            def bundle(version,profile,sequence,complete=True):
                output=P['archive_path'](repository,version,profile,sequence)
                output.mkdir(parents=True)
                raw=json.dumps({'release':{'version':version},'update_sequence':sequence}).encode()
                (output/'manifest.json').write_bytes(raw)
                if complete:
                    (output/'SHA256SUMS').write_text(hashlib.sha256(raw).hexdigest()+'  manifest.json\n')
                return output
            bundle('0.9.9','standard',7)
            latest=bundle('0.10.1','standard',8)
            bundle('0.10.1','standard',9,complete=False)
            loan=bundle('0.10.1','loan',6)
            bundle('0.11.0','standard',10)
            self.assertEqual(build['previous_archive'](repository,'standard','0.11.0'),latest)
            self.assertEqual(build['previous_archive'](repository,'loan','0.11.0'),loan)
            args=SimpleNamespace(accepted_image_directory=[],accepted_bundle=[],source_repository=repository,update_sequence=12)
            with patch.dict(build['accepted_update_arguments'].__globals__,{'VERSION':'0.11.0'}):
                self.assertEqual(build['accepted_update_arguments'](args),['--accepted-bundle',latest/'manifest.json'])
                args.profile='loan'
                self.assertEqual(build['accepted_update_arguments'](args),['--accepted-image-directory',loan])
                args.no_previous_source=True
                self.assertEqual(build['accepted_update_arguments'](args),[])


if __name__=='__main__':unittest.main()
