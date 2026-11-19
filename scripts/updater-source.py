"""Explicit build-source provenance for update manifests; no key/cache discovery."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

PUBLIC_ROOTS = {'config', 'docs', 'gui', 'inputs', 'runtime', 'scripts', 'tests', 'toolkit'}
PUBLIC_FILES = {'.gitignore', 'AGENTS.md', 'LICENSE', 'README.md', 'README.ja.md', 'THIRD-PARTY.md',
                'TODO.md', 'requirements.txt', 'environment.yml'}
SECRET_SUFFIXES = {'.pem', '.der', '.key', '.pfx', '.p12', '.cer', '.crt', '.env', '.gpg', '.asc'}


def source_path(value):
    path = Path(value)
    if (path.is_absolute() or '..' in path.parts or not path.parts or path.as_posix() != value
            or '\\' in value or any(ord(c) < 32 for c in value) or path.suffix.lower() in SECRET_SUFFIXES
            or path.parts[0] not in PUBLIC_ROOTS and value not in PUBLIC_FILES):
        raise ValueError('Source inventory must contain only explicitly selected public source files')
    return path


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def inventory(source, names):
    records = []
    for name in sorted(set(names)):
        path = source / source_path(name)
        for part in (path, *path.parents):
            if part.is_symlink(): raise ValueError('Source symlink forbidden')
        if not path.is_file(): raise ValueError('Missing source inventory file')
        raw = path.read_bytes()
        records.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    if not records: raise ValueError('Empty build-source inventory')
    return records


def provenance(source, repository, commit=None, hashes=None, extras=()):
    source, repository = Path(source).absolute(), Path(repository).absolute()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repository), *args])
    commit = commit or git('rev-parse', 'HEAD').decode().strip()
    if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('Full actual Git commit required')
    git('cat-file', '-e', commit + '^{commit}')
    if hashes is None:
        tracked = [p.decode() for p in git('ls-tree', '-r', '--name-only', '-z', commit).split(b'\0') if p]
        # Deleted tracked sources are represented by their absence and the diff below.
        names = [p for p in tracked if (source / p).exists()] + list(extras)
        untracked = [p.decode() for p in git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if p]
        missing = [p for p in untracked if Path(p).parts[0] in PUBLIC_ROOTS and p not in extras and (source / p).exists()]
        if missing:
            raise ValueError('Commit new sources or explicitly include them with --source-extra: ' + ', '.join(missing))
        records = inventory(source, names)
    else:
        records = inventory(source, hashes.keys())
        if any(hashes[item['path']] != item['sha256'] for item in records):
            raise ValueError('Build-source snapshot differs from its actual files')
    base = {}
    for line in git('ls-tree', '-rz', commit).split(b'\0'):
        if not line: continue
        metadata, name = line.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split()
        name = name.decode()
        source_path(name)
        if mode not in ('100644', '100755') or kind != 'blob': raise ValueError('Nonregular tracked source')
        base[name] = hashlib.sha256(git('cat-file', 'blob', blob)).hexdigest()
    current = {item['path']: item['sha256'] for item in records}
    changes = [{'path': name, 'base_sha256': base.get(name), 'worktree_sha256': current.get(name)}
               for name in sorted(base.keys() | current.keys()) if base.get(name) != current.get(name)]
    return {'schema': 1, 'commit': commit, 'files': records,
            'worktree_sha256': hashlib.sha256(canonical(records)).hexdigest(),
            'uncommitted_changes': changes,
            'uncommitted_changes_sha256': hashlib.sha256(canonical(changes)).hexdigest()}
