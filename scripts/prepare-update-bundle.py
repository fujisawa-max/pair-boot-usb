#!/usr/bin/env python3
"""Produce a contract-bound update bundle from verified ordinary image files."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
A = runpy.run_path(str(ROOT / 'scripts/updater-artifacts.py'))
ordinary, digest, canonical = (A[key] for key in ('ordinary', 'digest', 'canonical'))
PROFILE = 'inputs/layouts/production-v1/pair-r3-gpt512-v1.json'
NAMES = {'efi': 'efi.img', 'master': 'usb-data.img', 'logs': 'usb-logs.img', 'fedora': 'fedora-live.img'}


def read_json(path):
    path = ordinary(path)
    if path.stat().st_size > 1024**2: raise ValueError('Bounded JSON metadata required')
    raw = path.read_bytes()
    return json_bytes(raw)


def json_bytes(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def contract(profile='standard'):
    if profile not in ('standard', 'loan'):
        raise ValueError('Unknown media profile')
    pin = read_json(ROOT / ('inputs/updater-loan-contract-input.json' if profile == 'loan' else 'inputs/updater-contract-input.json'))
    root = ROOT / pin['directory']
    if digest(root / 'CONTENTS.json') != pin['contents_sha256']:
        raise ValueError('Contract transfer pin mismatch')
    contents = read_json(root / 'CONTENTS.json')
    for name, expected in contents['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or digest(root / name) != expected:
            raise ValueError('Contract source SHA mismatch')
    if digest(root / 'inputs/production-contract-v1.lock.json') != pin['contract_lock_sha256']:
        raise ValueError('Contract lock SHA mismatch')
    module = runpy.run_path(str(root / 'schemas/production-v1/validate.py'))
    module['check_lock'](root)
    layout = module['check'](module['load'](root / module['PROFILE']), 'layout', root)
    return module, root, layout, pin['contract_lock_sha256']


def verify_tools(sysroot):
    """Verify local tools against locked .deb bytes before executing them."""
    lock_path = ROOT / 'inputs/updater-inspection-tools.json'
    lock = read_json(lock_path)
    expected = {}
    for entry in lock['packages']:
        archive = ROOT / '.cache/debian/apt/archives' / entry['filename']
        if digest(archive) != entry['sha256']: raise ValueError('Inspection package SHA mismatch')
        with subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(archive)], stdout=subprocess.PIPE) as process:
            with tarfile.open(fileobj=process.stdout, mode='r|') as tar:
                for member in tar:
                    name = member.name.removeprefix('./')
                    if not name or member.isdir(): continue
                    if Path(name).is_absolute() or '..' in Path(name).parts:
                        raise ValueError('Unsafe inspection package path')
                    if name.split('/')[0] in ('bin', 'sbin', 'lib', 'lib64'): name = 'usr/' + name
                    if member.isfile():
                        stream = tar.extractfile(member)
                        value = ('file', hashlib.file_digest(stream, 'sha256').hexdigest())
                    elif member.issym(): value = ('symlink', member.linkname)
                    elif member.islnk(): value = ('hardlink', member.linkname.removeprefix('./'))
                    else: raise ValueError('Special inspection tool file')
                    if name in expected and expected[name] != value: raise ValueError('Conflicting inspection packages')
                    expected[name] = value
            if process.wait(): raise ValueError('Inspection package extraction failed')
    for name, (kind, wanted) in expected.items():
        path = sysroot / name
        if kind == 'symlink':
            if not path.is_symlink() or os.readlink(path) != wanted: raise ValueError('Inspection symlink changed')
        elif kind == 'file':
            if digest(path) != wanted: raise ValueError('Inspection tool/library changed: ' + name)
        else:
            if digest(path) != digest(sysroot / wanted): raise ValueError('Inspection hardlink changed')
    # The extraction-only sysroot has no ldconfig cache, preload or extra files.
    allowed = set(expected) | {'bin', 'sbin', 'lib', 'lib64', 'usr/bin/sh', 'usr/bin/awk'}
    for path in sysroot.rglob('*'):
        if not path.is_symlink() and path.is_dir(): continue
        if str(path.relative_to(sysroot)) not in allowed:
            raise ValueError('Unowned inspection sysroot file')
    for name, target in (('bin','usr/bin'),('sbin','usr/sbin'),('lib','usr/lib'),('lib64','usr/lib64'),
                         ('usr/bin/sh','dash'),('usr/bin/awk','mawk')):
        if os.readlink(sysroot / name) != target: raise ValueError('Inspection sysroot layout changed')
    return {'lock_sha256': digest(lock_path), 'packages': lock['packages'],
            'tool_files_verified': len(expected), 'host_installation': False}


def is_managed(path, layout):
    protected = layout['merge']['protected_roots'] + [layout['merge']['marker_path']]
    if any(path.casefold() == p.casefold() or path.casefold().startswith(p.casefold() + '/') for p in protected):
        return False
    return path in layout['managed_exact_paths'] or any(path.startswith(p + '/') for p in layout['managed_roots'])


def check_release(master, inside, certificate, marker):
    version = inside.get('project_version', inside['version'].removesuffix('-shared'))
    release = {'version': version, 'root_version': master['version'],
               'root_revision': master.get('root_revision', 0), 'boot_abi': master['boot_abi'],
               'state_schema': master['state_schema'], 'registry_schema': marker.get('registry_schema', 1),
               'kernel': master['kernel_release'], 'certificate_sha256': hashlib.sha256(certificate).hexdigest(),
               'master_uuid': master['usb_data_uuid'].lower(), 'master_json_sha256': '', 'root_sha256': master['sha256']}
    if (inside['version'] != master['version'] or inside['kernel_release'] != release['kernel']
            or inside.get('state_schema') != release['state_schema'] or inside.get('boot_abi') != release['boot_abi']
            or inside.get('root_revision', 0) != release['root_revision']
            or inside.get('module_signing_certificate_sha256') != release['certificate_sha256']
            or marker.get('schema') != 1 or marker.get('kind') != 'gpu-linux-config'
            or marker.get('root_version') != master['version'] or master.get('secure_boot_signed_artifacts') is not True
            or not re.fullmatch(r'[A-Za-z0-9.+_-]{1,160}', release['kernel'])):
        raise ValueError('Inconsistent root/kernel/ABI/state/certificate/marker')
    return release


def archive_path(repository, version, profile, sequence):
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', version)
            or profile not in ('standard', 'loan') or type(sequence) is not int or sequence < 1):
        raise ValueError('Invalid update archive identity')
    return Path(repository).absolute() / 'build/update-bundles' / version / profile / f'sequence-{sequence}'


def bundle_destination(output, repository, version, profile, sequence):
    path = output.absolute()
    archive = archive_path(repository, version, profile, sequence)
    if path != archive:
        return runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output'](path)
    ordinary(Path(repository), True)
    if '..' in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Refusing symlink or ambiguous archive path')
    if path.exists():
        raise ValueError('Update archive already exists; preserve it and choose a fresh output')
    return path


def media_paths(image_dir):
    evidence_path = image_dir / 'shared-media-verification.json'
    if not evidence_path.exists() and (image_dir / 'manifest.json').is_file():
        return image_dir / 'reports/shared-media-verification.json', image_dir / 'images/gpu-linux-usb.img'
    return evidence_path, image_dir / 'gpu-linux-usb.img'


def inspect(image_dir, work, sysroot, layout, module, *, image=None):
    image_dir = ordinary(image_dir, True)
    evidence_path, original_image = media_paths(image_dir)
    evidence = read_json(evidence_path)
    image = ordinary(image or original_image)
    if image.stat().st_size != evidence['bytes']:
        raise ValueError('Image does not match independent readback evidence')
    parts = A['read_gpt'](image, layout)
    partition_records=A['image_records'](image,parts,evidence['sha256'])
    work.mkdir(exist_ok=False)
    inputs={};stamps={}
    for part in parts:
        print('Inspecting fixed partition:', part['role'], flush=True)
        name=NAMES[part['role']];path=image_dir/name;expected=partition_records[part['role']]
        if path.exists() or path.is_symlink():
            ordinary(path)
            if path.stat().st_size!=expected['bytes'] or digest(path)!=expected['sha256']:
                raise ValueError('Partition file differs from the whole image')
            inputs[name]=path.absolute()
        else:
            path=work/name
            A['copy_region'](image,path,part['start_sector']*512,expected['bytes'],expected['sha256'])
        info=path.stat();stamps[path]=(info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    tools = A['Tools'](sysroot, work,inputs)
    fats = {}
    try:
        has_fedora = any(p['role'] == 'fedora' for p in parts)
        for role in ('efi', 'logs') + (('fedora',) if has_fedora else ()):
            tools.run('/usr/sbin/fsck.fat', '-n', '/work/' + NAMES[role])
            label = next(p['label'] for p in layout['partitions'] if p['role'] == role)
            fats[role] = A['Fat'](inputs.get(NAMES[role],work/NAMES[role]), label)
        tools.run('/usr/sbin/e2fsck', '-f', '-n', '/work/usb-data.img')
        master_raw = tools.cat('/work/usb-data.img', '/gpu-linux/master.json')
        master = json_bytes(master_raw)
        logical = []
        indexed = {}
        for role in ('efi',) + (('fedora',) if has_fedora else ()):
            indexed[role] = {name: fats[role].record(name) for name in sorted(fats[role].files)}
        indexed['master'] = {v['path']: v for v in tools.inventory('/work/usb-data.img')}
        for role in ('efi', 'master') + (('fedora',) if has_fedora else ()):
            for record in indexed[role].values():
                module['safe_path'](record['path']); logical.append({'role': role, **record})
        for name, meta in (('root.img', master), ('state.img', master['persistent_state'])):
            record = indexed['master']['gpu-linux/' + name]
            if record['bytes'] != meta['size'] or record['sha256'] != meta['sha256']:
                raise ValueError('MASTER root/state image differs from metadata')
        root = work / 'out/root.img'
        tools.cat('/work/usb-data.img', '/gpu-linux/root.img', root)
        if digest(root) != master['sha256']: raise ValueError('Root extraction mismatch')
        tools.run('/usr/sbin/e2fsck', '-f', '-n', '/work/out/root.img')
        inside = json_bytes(tools.cat('/work/out/root.img', '/usr/lib/gpu-root/image.json'))
        certificate = fats['efi'].bytes('EFI/BOOT/signing-cert.der')
        if has_fedora and certificate != fats['logs'].bytes('signing-cert.der'):
            raise ValueError('EFI/LOGS signing certificate mismatch')
        if not has_fedora and {'signing-cert.der', 'signing-cert-info.txt'} & fats['logs'].files.keys():
            raise ValueError('Loan LOGS must not contain signing certificate files')
        marker = json_bytes(fats['logs'].bytes('usb-config.json'))
        release = check_release(master, inside, certificate, marker)
        release['master_json_sha256'] = hashlib.sha256(master_raw).hexdigest()
        with inputs.get('usb-data.img',work/'usb-data.img').open('rb') as stream:
            stream.seek(1024); superblock = stream.read(1024)
        if (superblock[56:58] != b'\x53\xef' or str(uuid.UUID(bytes=superblock[104:120])) != release['master_uuid']
                or superblock[120:136].split(b'\0')[0] != b'GPU-MASTER'):
            raise ValueError('MASTER filesystem identity mismatch')
        kernel = release['kernel']
        boot = work / 'out/kernel'
        tools.cat('/work/usb-data.img', '/boot/vmlinuz', boot)
        raw = boot.open('rb').read(65536)
        pointer = struct.unpack_from('<H', raw, 0x20e)[0] + 0x200
        if raw[0x202:0x206] != b'HdrS' or not raw[pointer:].startswith(kernel.encode() + b' '):
            raise ValueError('Kernel header release mismatch')
        inner_kernel = work / 'out/inner-kernel'
        tools.cat('/work/out/root.img', '/boot/vmlinuz-' + kernel, inner_kernel)
        if digest(inner_kernel) != digest(boot): raise ValueError('Root/USB kernel mismatch')
        initrd = work / 'out/initramfs'
        tools.cat('/work/usb-data.img', '/boot/initramfs.img', initrd)
        shutdown = work / 'out/shutdown-initramfs'
        tools.cat('/work/out/root.img', '/boot/initramfs-' + kernel + '.img', shutdown)
        if digest(initrd) != digest(shutdown): raise ValueError('USB/root shutdown initramfs mismatch')
        cpio = runpy.run_path(str(ROOT / 'scripts/validate-appliance.py'))['cpio_entries']
        init_data, init_entries = cpio(initrd)
        if 'shutdown' not in init_entries or not any('shutdown-root.sh' in name for name in init_entries):
            raise ValueError('Missing shutdown archive components')
        cert_path = work / 'out/cert.der'; cert_path.write_bytes(certificate)
        module_records=verify_modules(tools, work, kernel, inside, init_data, init_entries)
        for path in ('EFI/BOOT/grub.cfg', 'boot/grub/grub.cfg'):
            raw = fats['efi'].bytes(path) if path.startswith('EFI') else tools.cat('/work/usb-data.img', '/' + path)
            if release['master_uuid'].encode() not in raw:
                raise ValueError('EFI/GRUB MASTER UUID reference mismatch')
        for role in (('logs', 'fedora') if has_fedora else ()):
            trust = json_bytes(fats[role].bytes('gui/trust.json'))
            if trust.get('certificate_sha256') != release['certificate_sha256']:
                raise ValueError('GUI trust pin mismatch')
        if release['root_revision'] == 0:
            info = fats['logs'].bytes('MEDIA-INFO.txt')
            if ((has_fedora and info != fats['fedora'].bytes('MEDIA-INFO.txt'))
                    or f'Project version: {release["version"]}\n'.encode() not in info
                    or f'Kernel: {kernel}\n'.encode() not in info):
                raise ValueError('Media release information mismatch')
        if not has_fedora:
            if marker.get('media_profile') != 'loan' or b'Media profile: loan\n' not in info:
                raise ValueError('Loan marker/profile information required')
            if any(name.startswith(('tools/', 'gui/')) or name.endswith(('.py', '.ps1', '.cmd', '.sh', '.exe')) for name in fats['logs'].files):
                raise ValueError('Loan LOGS contains executable management files')
        # Only the build's explicit file readback list establishes ownership.
        owned = []
        for item in evidence['checks']:
            path = item['path']
            if item['image'] == 'usb-logs.img' and is_managed(path, layout):
                module['safe_path'](path)
                if Path(path).suffix.lower() in ('.key', '.pem', '.der', '.p12', '.pfx', '.gpg', '.asc') or Path(path).name.startswith('.env'):
                    raise ValueError('Key/credential filename is not a management payload')
                record = fats['logs'].record(path)
                if record['sha256'] != item['sha256']: raise ValueError('Managed file differs from readback evidence')
                owned.append(record)
        module['file_paths']([v['path'] for v in owned])
        if not owned: raise ValueError('No verified managed ownership inventory')
        for item in owned:
            path = item['path']
            if has_fedora and path.startswith(('tools/', 'gui/')):
                if fats['fedora'].record(path)['sha256'] != item['sha256']:
                    raise ValueError('Fedora and LOGS management files disagree')
        payload = {v['path']: fats['logs'].bytes(v['path'], limit=16 * 1024**2) for v in owned}
        for path,before in stamps.items():
            info=path.stat()
            if (info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=before:raise ValueError('Inspection input changed')
        return {'release': release, 'partitions': [partition_records[p['role']] for p in parts if p['role'] != 'logs'],
                'logical_inventory': logical, 'managed_files': sorted(owned, key=lambda v: v['path']),
                'module_records':module_records,'gpu_unlock_commit':inside['gpu_unlock_commit'],
                'payload': payload, 'verification_evidence_sha256': digest(evidence_path)}
    finally:
        for fat in fats.values(): fat.close()


def verify_modules(tools, work, kernel, inside, init_data, init_entries):
    marker = b'~Module signature appended~\n'
    expected_driver = inside['pilot_components']['nvidia_driver_version']
    records=[]
    for name in ('gpu_unlock', 'nvidia', 'nvidia-modeset', 'nvidia-drm', 'nvidia-uvm', 'nvidia-peermem'):
        path = '/usr/lib/modules/' + kernel + '/updates/' + ('' if name == 'gpu_unlock' else 'dkms/') + name + '.ko'
        target = work / ('out/' + name + '.ko')
        tools.cat('/work/out/root.img', path, target)
        info = {field: tools.run('/usr/sbin/modinfo', '-F', field, '/out/' + target.name).decode().strip()
                for field in ('name', 'vermagic', 'version', 'sig_id', 'sig_hashalgo')}
        if (info['name'] != name.replace('-', '_') or not info['vermagic'].startswith(kernel + ' ')
                or info['sig_id'] != 'PKCS#7' or info['sig_hashalgo'].lower() != 'sha256'
                or name != 'gpu_unlock' and info['version'] != expected_driver
                or name=='gpu_unlock' and inside.get('components',{}).get('gpu-unlock version',info['version']) != info['version']):
            raise ValueError('Module/kernel/signature metadata mismatch')
        signed = target.read_bytes()
        if not signed.endswith(marker): raise ValueError('Missing module signature')
        trailer = signed[-len(marker)-12:-len(marker)]
        length = struct.unpack('>I', trailer[8:])[0]
        if trailer[2] != 2 or not 0 < length <= 1024**2 or length + 12 + len(marker) >= len(signed):
            raise ValueError('Invalid module signature trailer')
        signature = work / 'out/signature.der'; body = work / 'out/module-body'
        signature.write_bytes(signed[-len(marker)-12-length:-len(marker)-12])
        body.write_bytes(signed[:-len(marker)-12-length])
        tools.run('/usr/bin/openssl', 'x509', '-inform', 'DER', '-in', '/out/cert.der', '-out', '/out/cert.pem')
        tools.run('/usr/bin/openssl', 'cms', '-verify', '-binary', '-inform', 'DER', '-in', '/out/signature.der',
                  '-content', '/out/module-body', '-certfile', '/out/cert.pem', '-nointern', '-noverify', '-out', '/dev/null')
        matches = [(offset, size) for filename, (offset, size) in init_entries.items() if filename.endswith('/' + name + '.ko')]
        if name == 'gpu_unlock' and not matches: raise ValueError('Unlock module missing from initramfs')
        if any(init_data[offset:offset+size] != signed for offset, size in matches):
            raise ValueError('Initramfs/root module bytes disagree')
        records.append({'name':name,'signed_sha256':hashlib.sha256(signed).hexdigest(),
                        'input_sha256':digest(body),'vermagic':info['vermagic'],'version':info['version']})
    return records


def external_evidence(source_root, source, signed_modules):
    fetch=runpy.run_path(str(ROOT/'scripts/fetch-inputs.py'))
    paths={item['path'] for item in source['files']}
    groups=[name for name,(lock,_) in fetch['GROUPS'].items() if 'inputs/'+lock in paths]
    entries=fetch['input_plan'](groups,root=source_root)
    files=[]
    print('Verifying pinned external build inputs',flush=True)
    for entry in entries:
        path=fetch['cache_path'](source_root,entry['file']);fetch['verify'](path,entry)
        files.append({**entry,'bytes':path.stat().st_size})
    upstream=read_json(source_root/'inputs/gpu-unlock-source.json')
    if (upstream['url']!='https://github.com/Zagitta/gpu-unlock.git'
            or not re.fullmatch('[a-f0-9]{40}',upstream['commit'])
            or Path(upstream['directory']).parts[:1]!=('vendor',) or '..' in Path(upstream['directory']).parts):
        raise ValueError('Unexpected upstream source binding')
    runpy.run_path(str(ROOT/'scripts/prepare-upstream.py'))['verify'](source_root/upstream['directory'],upstream['commit'])
    signed=read_json(signed_modules/'signing-manifest.json')
    if signed.get('schema')!=1 or signed.get('purpose')!='production':raise ValueError('Production signing evidence required')
    expected={'gpu_unlock','nvidia','nvidia-modeset','nvidia-drm','nvidia-uvm','nvidia-peermem'}
    if len(signed['modules'])!=6 or {item['name'] for item in signed['modules']}!=expected:
        raise ValueError('Signing input coverage mismatch')
    for item in signed['modules']:
        if digest(signed_modules/(item['name']+'.ko'))!=item['signed_sha256']:
            raise ValueError('Signed build input changed')
    return {'archives':files,'gpu_unlock':upstream,
            'signing_manifest_sha256':digest(signed_modules/'signing-manifest.json'),
            'certificate_sha256':digest(signed_modules/'signing-cert.der'),
            'modules':[{k:item[k] for k in ('name','input_sha256','signed_sha256','vermagic')} for item in signed['modules']]}


def produce(args):
    if args.output.exists():
        raise ValueError('Output already exists; preserve it and choose a fresh path')
    profile = getattr(args, 'profile', 'standard')
    module, contract_root, layout, lock_sha = contract(profile)
    version = read_json(args.source_root / 'config/release.json')['version']
    output = bundle_destination(args.output, args.repository, version, profile, args.update_sequence)
    image_dir = ordinary(args.image_directory, True)
    if output.is_relative_to(image_dir) or image_dir.is_relative_to(output): raise ValueError('Bundle/image directories overlap')
    readback_path, image_source = media_paths(image_dir)
    publish = getattr(args, 'publish_image', False)
    if publish and (image_source.parent != image_dir or not image_source.is_relative_to(ROOT / 'build')):
        raise ValueError('Only a workspace image can be published')
    if not args.inspection_sysroot.absolute().is_relative_to(ROOT / 'build'):
        raise ValueError('Inspection sysroot must be prepared below build/')
    tools_evidence = verify_tools(ordinary(args.inspection_sysroot, True))
    source = read_json(args.source_record)
    actual = runpy.run_path(str(ROOT / 'scripts/updater-source.py'))['provenance'](
        args.source_root, args.repository, source['commit'], {v['path']: v['sha256'] for v in source['files']})
    if canonical(actual) != canonical(source): raise ValueError('Build source or recorded provenance differs')
    args.source_root=ordinary(args.source_root,True)
    externals=external_evidence(args.source_root,source,args.signed_modules or args.source_root/'build/signed-modules')
    output.mkdir(parents=True)
    for folder in ('images', 'payload', 'reports'): (output / folder).mkdir()
    evidence = read_json(readback_path)
    image_source = ordinary(image_source)
    source_stat = image_source.stat()
    if source_stat.st_size != evidence['bytes']:
        raise ValueError('Image size differs from readback evidence')
    print('Preparing verified distribution image in a fresh bundle', flush=True)
    image = output / 'images/gpu-linux-usb.img'
    linked = False
    if publish:
        try:
            os.link(image_source, image)
            linked = True
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
    # inspect() below independently hashes the entire linked or copied image.
    image_record = {'path': 'images/gpu-linux-usb.img'}
    if linked:
        image_record.update(bytes=evidence['bytes'], sha256=evidence['sha256'])
    else:
        image_record.update(A['copy_region'](image_source, image, 0, evidence['bytes'], evidence['sha256']))
    scratch = Path(tempfile.mkdtemp(prefix='update-inspection-', dir=ROOT / 'build'))
    try:
        target = inspect(image_dir, scratch / 'target', args.inspection_sysroot, layout, module, image=image)
        if target['release']['root_revision'] or target['release']['registry_schema'] != 2:
            raise ValueError('Unified schema-2 target required')
        actual_modules=[{key:item[key] for key in ('name','input_sha256','signed_sha256','vermagic')} for item in target['module_records']]
        configured=read_json(args.source_root/'config/release.json')
        source_kernel=read_json(args.source_root/'inputs/debian-build-inputs.json')['kernel_release']
        actual_unlock=next(v['version'] for v in target['module_records'] if v['name']=='gpu_unlock')
        if (externals['certificate_sha256']!=target['release']['certificate_sha256']
                or sorted(externals['modules'],key=lambda v:v['name'])!=sorted(actual_modules,key=lambda v:v['name'])
                or any(configured[k]!=target['release'][k] for k in ('version','boot_abi','state_schema'))
                or source_kernel!=target['release']['kernel']
                or externals['gpu_unlock']['commit']!=target['gpu_unlock_commit']
                or externals['gpu_unlock']['version']!=actual_unlock):
            raise ValueError('Signed build inputs differ from the inspected root')
        accepted = []
        for index, directory in enumerate(args.accepted_image_directory):
            old = inspect(directory, scratch / f'source-{index}', args.inspection_sysroot, layout, module)
            accepted.append(source_entry(old, 'source-' + str(index)))
        for index, manifest_path in enumerate(args.accepted_bundle):
            previous = module['check'](read_json(manifest_path), 'manifest', contract_root)
            if previous['update_sequence'] >= args.update_sequence:
                raise ValueError('Update sequence must increase beyond every accepted bundle')
            previous_root = ordinary(manifest_path.parent, True)
            for item in [previous['image'], previous['producer']['verification_report'],
                         *(v['payload'] for v in previous['managed_files'])]:
                path = ordinary(previous_root / item['path'])
                if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
                    raise ValueError('Previous bundle file SHA/size mismatch')
            previous_evidence = scratch / f'previous-evidence-{index}'; previous_evidence.mkdir()
            (previous_evidence / 'shared-media-verification.json').write_bytes(canonical({
                'bytes': previous['image']['bytes'], 'sha256': previous['image']['sha256'],
                'checks': [{'image': 'usb-logs.img', 'path': v['path'], 'sha256': v['payload']['sha256']}
                           for v in previous['managed_files']]}))
            old = inspect(previous_evidence, scratch / f'previous-{index}', args.inspection_sysroot, layout, module,
                          image=previous_root / previous['image']['path'])
            if old['release'] != previous['release'] or old['logical_inventory'] != previous['logical_inventory']:
                raise ValueError('Previous manifest differs from actual filesystems')
            entry = source_entry(old, f'updated-{index}')
            entry.update(kind='updated-media', update_sequence=previous['update_sequence'],
                         prior_manifest_sha256=digest(manifest_path))
            accepted.append(entry)
        accepted.append(source_entry(target, 'initial-current-media'))
        maintenance = []
        if getattr(args, 'accepted_worker_merge_fix', None) is not None:
            entry, proof = worker_merge_fix_source(args.accepted_worker_merge_fix, accepted,
                                                   args.repository, module, contract_root)
            accepted.append(entry)
            maintenance.append(proof)
        managed = []
        for item in target['managed_files']:
            path = output / 'payload' / item['path']; path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(target['payload'][item['path']])
            managed.append({'path': item['path'], 'payload': {'path': 'payload/' + item['path'],
                            'bytes': item['bytes'], 'sha256': item['sha256']}})
        report = {'schema': 1, 'source': source, 'inspection_tools': tools_evidence,
                  'external_build_inputs':externals,
                  'maintenance_source_verification': maintenance,
                  'input_readback_report_sha256': target['verification_evidence_sha256'],
                  'contract_lock_sha256': lock_sha, 'logical_files_verified': len(target['logical_inventory']),
                  'module_cms_signatures_verified': 6, 'filesystems_verified': True,
                  'media_profile': getattr(args, 'profile', 'standard'),
                  'updater_support': 'existing standard contract' if layout['contract_revision'] == 1 else 'requires updater implementation of proposed loan contract 1.2',
                  'scope': 'producer only; LOGS merge helper and updater raw worker are separate implementations',
                  'native_hardware_tested': False}
        report_path = output / 'reports/verification.json'; report_path.write_bytes(canonical(report))
        readback = ordinary(readback_path).read_bytes()
        if hashlib.sha256(readback).hexdigest() != target['verification_evidence_sha256']:
            raise ValueError('Image readback report changed during update bundle generation')
        (output / 'reports/shared-media-verification.json').write_bytes(readback)
        manifest = {'contract_version': 1, 'contract_revision': layout['contract_revision'], 'contract_lock_sha256': lock_sha,
                    'helper_protocol': 1, 'kind': 'pair-boot-usb-update', 'minimum_updater': '1.0.0',
                    'layout': {'id': layout['id'], 'sha256': digest(contract_root / module['PROFILE'])},
                    'producer': {'id': 'pair-boot-usb', 'version': target['release']['version'],
                                 'commit': source['commit'], 'worktree_sha256': source['worktree_sha256'],
                                 'verification_report': {'path': 'reports/verification.json',
                                     'bytes': report_path.stat().st_size, 'sha256': digest(report_path)}},
                    'update_sequence': args.update_sequence, 'release': target['release'], 'accepted_sources': accepted,
                    'image': image_record, 'partitions': target['partitions'],
                    'logical_inventory': target['logical_inventory'], 'managed_files': managed,
                    'remove_managed_files': []}
        module['check'](manifest, 'manifest', contract_root)
        raw = canonical(manifest)
        if len(raw) > 1024**2: raise ValueError('Manifest exceeds contract metadata limit')
        (output / 'manifest.json').write_bytes(raw)
        (output / 'SHA256SUMS').write_text(hashlib.sha256(raw).hexdigest() + '  manifest.json\n')
        if publish:
            current = ordinary(image_source).stat()
            if ((source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns)
                    != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)):
                raise ValueError('Workspace image changed; refusing to remove it')
            image_source.unlink()
        print(f'Validated update manifest: {output / "manifest.json"}', flush=True)
        if maintenance:
            print('This bundle requires updater 1.0.7 or later for maintenance-source disambiguation.', flush=True)
    except BaseException:
        print(f'Incomplete output preserved; inspection files: {scratch}', file=sys.stderr)
        raise
    else:
        shutil.rmtree(scratch)


def source_entry(info, name):
    release = info['release']
    if release['root_revision']:
        kind = 'legacy-r3'
        if (release['version'], release['root_version'], release['root_revision'], release['registry_schema']) != ('0.9.1', '0.9.1-shared', 3, 1):
            raise ValueError('Unsupported legacy source; only inspected r3 is allowed')
    else: kind = 'built-media'
    return {'id': name, 'kind': kind, 'release': release, 'update_sequence': 0,
            'prior_manifest_sha256': '0' * 64, 'managed_files': info['managed_files']}


def committed_worker_record(repository, commit):
    """Read the single known maintenance file from an actual Git object."""
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Full worker maintenance commit required')
    raw = subprocess.check_output(['git', '-C', str(ordinary(repository, True)), 'cat-file',
                                   'blob', commit + ':gui/windows-worker.ps1'])
    return {'path': 'gui/windows-worker.ps1', 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}


def worker_merge_fix_source(record_path, sources, repository, module, contract_root):
    """Accept only the pinned 0.9.3 worker fix on a fully inspected base."""
    definition = ROOT / 'inputs/updater-worker-merge-fix.json'
    pin = read_json(definition)
    if (pin['schema'] != 1 or pin['id'] != 'source-0-9-3-r2-worker-merge-fix'
            or pin['base_version'] != '0.9.3' or pin['managed_path'] != 'gui/windows-worker.ps1'):
        raise ValueError('Unsupported worker maintenance definition')
    known = {}
    for stage in ('before', 'after'):
        known[stage] = {'path': pin['managed_path'],
                        **{key: pin[stage][key] for key in ('bytes', 'sha256')}}
        if committed_worker_record(repository, pin[stage]['commit']) != known[stage]:
            raise ValueError('Pinned worker maintenance commit differs from its bytes/SHA')
    candidate = read_json(record_path)
    schema = module['load'](contract_root / 'schemas/production-v1/manifest.schema.json')
    module['validate'](candidate, schema['properties']['accepted_sources']['items'])
    module['file_paths']([item['path'] for item in candidate['managed_files']])
    if candidate['id'] != pin['id'] or any(s['id'] == candidate['id'] for s in sources):
        raise ValueError('Unsupported or duplicate worker maintenance source id')
    # Derive every field from an inspected initial 0.9.3 image. The only
    # permitted differences are this id and the one pinned worker record.
    expected_candidate = {**candidate, 'managed_files': sorted(candidate['managed_files'], key=lambda v: v['path'])}
    matches = []
    for source in sources:
        if (source['kind'] != 'built-media' or source['release']['version'] != pin['base_version']
                or source['release']['root_version'] != pin['base_version']
                or source['release']['root_revision'] != 0 or source['release']['registry_schema'] != 2
                or source['update_sequence'] != 0 or source['prior_manifest_sha256'] != '0' * 64):
            continue
        workers = [item for item in source['managed_files'] if item['path'] == pin['managed_path']]
        if workers != [known['before']]:
            continue
        owned = [dict(known['after'] if item['path'] == pin['managed_path'] else item)
                 for item in source['managed_files']]
        derived = {**source, 'id': pin['id'], 'managed_files': sorted(owned, key=lambda v: v['path'])}
        if canonical(derived) == canonical(expected_candidate):
            matches.append((derived, source))
    if len(matches) != 1:
        raise ValueError('Worker maintenance record does not uniquely match an inspected 0.9.3 base plus the pinned fix')
    derived, base = matches[0]
    evidence = {'id': derived['id'], 'base_source_id': base['id'],
                'definition_sha256': digest(definition), 'record_sha256': digest(record_path),
                'before': pin['before'], 'after': pin['after'], 'path': pin['managed_path'],
                'managed_files_verified': len(derived['managed_files']),
                'verification_method': 'inspected base image plus pinned committed worker; all other source fields unchanged',
                'required_updater': '1.0.7'}
    return derived, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('standard', 'loan'), default='standard')
    parser.add_argument('--image-directory', type=Path, required=True)
    parser.add_argument('--inspection-sysroot', type=Path, default=ROOT / 'build/updater-inspection-sysroot')
    parser.add_argument('--source-record', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--signed-modules', type=Path, help='actual signed inputs used to build this image')
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--accepted-image-directory', type=Path, action='append', default=[])
    parser.add_argument('--accepted-bundle', type=Path, action='append', default=[], help='previous verified bundle manifest, for updated-media receipts')
    parser.add_argument('--accepted-worker-merge-fix', type=Path,
                        help='explicit accepted-source.json for the pinned 0.9.3 Windows worker maintenance fix')
    parser.add_argument('--update-sequence', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish-image', action='store_true',
                        help='keep the verified image only in the bundle after successful generation')
    args = parser.parse_args()
    if args.update_sequence < 1: parser.error('Positive update sequence required')
    produce(args)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as error:
        sys.exit(f'Update bundle stopped; preserve existing files: {error}')
