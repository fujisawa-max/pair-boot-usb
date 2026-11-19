"""Portable, metadata-only production contract checks. No device or helper execution."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'inputs/production-contract-v1.lock.json'
PROFILE = 'inputs/layouts/production-v1/pair-loan-gpt512-v1.json'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def load(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        need(not part.is_symlink(), 'symlink input')
    info = path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_size <= 2**20, 'bounded ordinary JSON file required')
    raw = path.read_bytes()
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: need(False, 'nonfinite JSON'))


def validate(value, schema):
    """Only the closed vocabulary used by the shipped JSON Schema 2020-12 files."""
    allowed = {'$schema', '$id', 'type', 'properties', 'required', 'additionalProperties',
               'const', 'enum', 'minLength', 'maxLength', 'pattern', 'minItems',
               'maxItems', 'items', 'minimum', 'maximum'}
    need(schema.keys() <= allowed, 'unsupported schema vocabulary')
    if 'type' in schema:
        need(type(value) is {'object': dict, 'array': list, 'string': str,
                            'integer': int}[schema['type']], 'type mismatch')
    if 'const' in schema:
        need(type(value) is type(schema['const']) and value == schema['const'], 'constant mismatch')
    if 'enum' in schema:
        need(any(type(value) is type(x) and value == x for x in schema['enum']), 'unsupported value')
    if schema.get('type') == 'object':
        need(set(schema['required']) <= value.keys() <= schema['properties'].keys(), 'unknown/missing fields')
        for key, item in value.items():
            validate(item, schema['properties'][key])
    if schema.get('type') == 'array':
        need(schema['minItems'] <= len(value) <= schema['maxItems'], 'array bounds')
        for item in value:
            validate(item, schema['items'])
    if isinstance(value, str):
        need(schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 2**20), 'string bounds')
        if 'pattern' in schema:
            need(re.fullmatch(schema['pattern'], value) is not None, 'string pattern')
    if type(value) is int:
        need(schema.get('minimum', value) <= value <= schema.get('maximum', value), 'integer bounds')


def safe_path(path):
    need(re.fullmatch(r'[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*', path) is not None, 'relative ASCII path required')
    for part in path.split('/'):
        need(part not in ('.', '..') and not part.endswith(('.', ' ')), 'unsafe path component')
        need(part.split('.')[0].upper() not in {'CON', 'PRN', 'AUX', 'NUL',
             *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}, 'DOS device name')


def unique(values):
    need(len(values) == len(set(values)), 'duplicate identifiers/paths')


def file_paths(paths):
    unique([p.casefold() for p in paths])
    seen = {}
    files = {p.casefold() for p in paths}
    for path in paths:
        safe_path(path)
        parts = path.split('/')
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            folded = prefix.casefold()
            need(folded not in seen or seen[folded] == prefix, 'directory case collision')
            seen[folded] = prefix
            if index < len(parts):
                need(folded not in files, 'file/directory collision')


def version(value):
    return tuple(int(x) for x in value.split('.'))


def check_lock(root=ROOT):
    lock = load(root / LOCK)
    need(lock['contract_version'] == 1 and lock['contract_revision'] == 2, 'unsupported lock')
    for path, expected in lock['files'].items():
        safe_path(path)
        load_path = root / path
        # Read bounded source/schema files only; never resolve arbitrary inputs from a manifest.
        for part in (load_path, *load_path.parents):
            need(not part.is_symlink(), 'symlink contract')
        need(load_path.is_file() and load_path.stat().st_size <= 2**20, 'missing/large contract file')
        need(sha(load_path.read_bytes()) == expected, 'contract file SHA mismatch: ' + path)
    return sha((root / LOCK).read_bytes())


def check_layout(layout):
    parts = layout['partitions']
    roles = {p['role']: p for p in parts}
    unique([p['role'] for p in parts]); unique([p['number'] for p in parts])
    writable = {p['role'] for p in parts if p['operation'] != 'preserve'}
    need(set(layout['write_order']) == writable, 'write order coverage')
    unique(layout['write_order']); unique(layout['required_roles'])
    need(set(layout['required_roles']) <= writable, 'required roles')
    for group in layout['coherence_groups']:
        unique(group); need(set(group) <= writable, 'coherence roles')
    fixed = sorted((p['start_sector'], p['start_sector'] + p['sector_count'])
                   for p in parts if p['geometry'] == 'fixed')
    need(all(a < b for a, b in fixed), 'empty fixed partition')
    need(all(a[1] <= b[0] for a, b in zip(fixed, fixed[1:])), 'partition overlap')
    for p in parts:
        need((p['geometry'] == 'target-preserved') == (p['operation'] == 'preserve'), 'dynamic write forbidden')
        if p['geometry'] == 'target-preserved':
            need(p['start_sector'] == p['sector_count'] == 0, 'dynamic sentinel')
    merge = layout['merge']
    need(merge['role'] in roles and roles[merge['role']]['operation'] == 'merge', 'merge role')
    for path in [*layout['managed_roots'], *layout['managed_exact_paths'],
                 *merge['protected_roots'], merge['registry_path'], merge['derived_hosts_path'],
                 merge['marker_path'], merge['receipt_path']]:
        safe_path(path)


def check_manifest(value, layout):
    roles = {p['role']: p for p in layout['partitions']}
    replace = {r for r, p in roles.items() if p['operation'] == 'replace'}
    unique([p['role'] for p in value['partitions']])
    need({p['role'] for p in value['partitions']} == replace, 'replacement role coverage')
    for part in value['partitions']:
        need(part['bytes'] == roles[part['role']]['sector_count'] * layout['logical_sector_bytes'], 'partition size')
    need(value['image']['bytes'] % layout['logical_sector_bytes'] == 0, 'image alignment')
    need(value['image']['bytes'] >= max((p['start_sector'] + p['sector_count']) * layout['logical_sector_bytes']
                                     for p in roles.values() if p['geometry'] == 'fixed') + 33 * layout['logical_sector_bytes'], 'truncated source image')
    unique([x['role'] + ':' + x['path'].casefold() for x in value['logical_inventory']])
    need({x['role'] for x in value['logical_inventory']} == replace, 'logical inventory roles')
    for item in value['logical_inventory']:
        safe_path(item['path'])
    all_paths = [x['path'] for x in value['managed_files']] + value['remove_managed_files']
    file_paths(all_paths)
    for path in all_paths:
        safe_path(path)
        need(path in layout['managed_exact_paths'] or
             any(path.startswith(r + '/') for r in layout['managed_roots']), 'unmanaged payload path')
        need(not any(path.casefold() == p.casefold() or path.casefold().startswith(p.casefold() + '/')
                     for p in layout['merge']['protected_roots']), 'protected payload path')
    for item in [value['image'], value['producer']['verification_report'],
                 *(x['payload'] for x in value['managed_files'])]:
        safe_path(item['path'])
    paths = [value['image']['path'], value['producer']['verification_report']['path'],
             *(x['payload']['path'] for x in value['managed_files'])]
    file_paths(paths)
    new = value['release']
    need(new['registry_schema'] == 2 and new['root_revision'] == 0 and
         new['root_version'] == new['version'], 'unified target release required')
    unique([s['id'] for s in value['accepted_sources']])
    for source in value['accepted_sources']:
        old = source['release']
        need(version(old['version']) <= version(new['version']), 'release downgrade')
        for key in ('boot_abi', 'state_schema', 'certificate_sha256'):
            need(old[key] == new[key], 'unsupported compatibility/certificate change')
        if source['kind'] == 'legacy-r3':
            need((old['version'], old['root_version'], old['root_revision'], old['registry_schema']) ==
                 ('0.9.1', '0.9.1-shared', 3, 1), 'unknown legacy release')
        else:
            need(old['registry_schema'] == 2 and old['root_revision'] == 0 and
                 old['root_version'] == old['version'], 'unknown built source')
        if source['kind'] == 'updated-media':
            need(0 < source['update_sequence'] < value['update_sequence'] and
                 source['prior_manifest_sha256'] != '0' * 64, 'sequence rollback')
        else:
            need(source['update_sequence'] == 0 and source['prior_manifest_sha256'] == '0' * 64, 'initial source receipt')
        file_paths([p['path'] for p in source['managed_files']])
        for item in source['managed_files']:
            safe_path(item['path'])
        need(set(value['remove_managed_files']) <= {p['path'] for p in source['managed_files']}, 'unowned removal')


def check(value, kind, root=ROOT):
    need(kind in ('manifest', 'layout', 'helper-request', 'helper-receipt'), 'unknown kind')
    lock_sha = check_lock(root)
    validate(value, load(root / f'schemas/production-v1/{kind}.schema.json'))
    if kind == 'layout':
        check_layout(value)
        return value
    need(value['contract_lock_sha256'] == lock_sha, 'wrong contract binding')
    layout = load(root / PROFILE)
    validate(layout, load(root / 'schemas/production-v1/layout.schema.json'))
    check_layout(layout)
    if kind == 'manifest':
        need(value['layout'] == {'id': layout['id'], 'sha256': sha((root / PROFILE).read_bytes())}, 'wrong layout binding')
        check_manifest(value, layout)
    else:
        roles = {p['role']: p for p in layout['partitions']}
        snapshots = value['snapshots']
        unique([s['role'] for s in snapshots])
        need({s['role'] for s in snapshots} == {p['role'] for p in roles.values() if p['operation'] != 'preserve'}, 'snapshot coverage')
        for item in snapshots:
            safe_path(item['file']['path'])
            need(item['file']['bytes'] == roles[item['role']]['sector_count'] * layout['logical_sector_bytes'], 'snapshot size')
        file_paths([s['file']['path'] for s in snapshots])
        if kind == 'helper-request':
            selected = value['selected_roles']; unique(selected)
            need(set(layout['required_roles']) <= set(selected) <= {s['role'] for s in snapshots}, 'selection')
            for group in layout['coherence_groups']:
                need(not set(selected).intersection(group) or set(group) <= set(selected), 'incomplete coherence group')
        else:
            output = value['output']; reg = value['registry']
            need(output['role'] == layout['merge']['role'], 'wrong merge output role')
            need(output['file']['bytes'] == roles[output['role']]['sector_count'] * layout['logical_sector_bytes'], 'merge size')
            for item in (output['file'], output['inventory_before'], output['inventory_after']):
                safe_path(item['path'])
            file_paths([s['file']['path'] for s in snapshots] +
                       [output[k]['path'] for k in ('file', 'inventory_before', 'inventory_after')])
            migration = reg['migration']
            need(migration in layout['merge']['migrations'], 'unsupported migration')
            if migration == 'registry-1-to-2':
                need(reg['before_schema'] == 1 and reg['after_generation'] == reg['before_generation'] + 1, 'migration generation')
            else:
                need(reg['before_schema'] == 2 and reg['after_generation'] == reg['before_generation'] and
                     reg['before_sha256'] == reg['after_sha256'], 'identity registry changed')
    return value


def check_exchange(manifest, request, receipt, root=ROOT):
    """Bind metadata objects; caller additionally verifies raw JSON/file SHA and evidence."""
    for obj, kind in ((manifest, 'manifest'), (request, 'helper-request'), (receipt, 'helper-receipt')):
        check(obj, kind, root)
    for key in ('request_id', 'manifest_sha256', 'target', 'helper', 'snapshots'):
        need(request[key] == receipt[key], 'request/receipt mismatch: ' + key)
    sources = {s['id']: s for s in manifest['accepted_sources']}
    need(receipt['accepted_source_id'] in sources, 'unaccepted source')
    source = sources[receipt['accepted_source_id']]
    need(receipt['registry']['before_schema'] == source['release']['registry_schema'], 'source registry mismatch')


def check_exchange_files(manifest_path, request_path, receipt_path, root=ROOT):
    manifest, request, receipt = (load(p) for p in (manifest_path, request_path, receipt_path))
    check_exchange(manifest, request, receipt, root)
    need(request['manifest_sha256'] == sha(Path(manifest_path).read_bytes()), 'manifest SHA mismatch')
    need(receipt['request_sha256'] == sha(Path(request_path).read_bytes()), 'request SHA mismatch')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['manifest', 'layout', 'helper-request', 'helper-receipt'])
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        check(load(args.file), args.kind)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'refused: {exc}\n')
    print('metadata compatible; payload, filesystems and hardware NOT verified')
