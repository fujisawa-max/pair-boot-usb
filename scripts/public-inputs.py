"""Helpers for official public keys and signed metadata; never use user keyrings."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FETCH = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))


def primary_fingerprints(text):
    fingerprints = []
    primary = False
    for line in text.splitlines():
        fields = line.split(':')
        if fields[0] in ('sec', 'ssb'):
            raise ValueError('Only official public keys are accepted')
        if fields[0] in ('pub', 'sub'):
            primary = fields[0] == 'pub'
        elif fields[0] == 'fpr' and primary:
            fingerprints.append(fields[9].upper())
            primary = False
    return fingerprints


def public_key(name):
    entries = json.loads((ROOT / 'inputs/public-keys.json').read_text())['archives']
    entry = next(item for item in entries if item['name'] == name)
    path = FETCH['cache_path'](ROOT, entry['file'])
    FETCH['verify'](path, entry)
    with tempfile.TemporaryDirectory(prefix='public-key-inspection-') as folder:
        result = subprocess.run(['gpg', '--no-options', '--homedir', folder, '--batch',
                                 '--with-colons', '--show-keys', str(path)],
                                check=True, capture_output=True, text=True)
    fingerprints = primary_fingerprints(result.stdout)
    if entry['fingerprint'] not in fingerprints:
        raise ValueError(f'Official key fingerprint mismatch: {name}')
    if name != 'fedora' and fingerprints != [entry['fingerprint']]:
        raise ValueError(f'Unexpected extra primary keys: {name}')
    return path, entry['fingerprint']


def signed_cleartext(signed, name):
    key, expected = public_key(name)
    with tempfile.TemporaryDirectory(prefix='public-signature-verification-') as folder:
        home = Path(folder)
        command = ['gpg', '--no-options', '--homedir', str(home), '--batch']
        subprocess.run([*command, '--import-options', 'import-minimal', '--import', str(key)],
                       check=True, capture_output=True)
        # Export only the pinned primary key and its signing subkeys.
        ring = home / 'verified-public.gpg'
        ring.write_bytes(subprocess.check_output([*command, '--export', expected]))
        plain = home / 'verified-content'
        result = subprocess.run(['gpgv', '--homedir', str(home), '--keyring', str(ring),
                                 '--status-fd', '1', '--output', str(plain), str(signed)],
                                check=True, capture_output=True, text=True)
        valid = [line.split() for line in result.stdout.splitlines()
                 if line.startswith('[GNUPG:] VALIDSIG ')]
        if any(line.split()[1] in ('BADSIG', 'ERRSIG', 'EXPKEYSIG', 'EXPSIG', 'REVKEYSIG')
               for line in result.stdout.splitlines() if line.startswith('[GNUPG:] ')):
            raise ValueError('Rejected invalid, expired or revoked signature')
        if not any(expected in (line[2], line[-1]) for line in valid):
            raise ValueError('Signature is not from the pinned official key')
        return plain.read_bytes()


def metadata_file(url, relative, offline=False, refresh=False, max_bytes=1024 * 1024):
    """Cache bounded signed metadata; its caller must verify before using it."""
    path = FETCH['cache_path'](ROOT, relative)
    if path.exists() and not refresh:
        if path.stat().st_size > max_bytes:
            raise ValueError('Metadata exceeds size limit')
        return path
    if offline:
        raise ValueError(f'Missing offline signed metadata: {relative}')
    if not url.startswith('https://'):
        raise ValueError('Signed metadata requires HTTPS')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with urllib.request.build_opener(FETCH['HTTPSRedirects']()).open(url, timeout=60) as response:
            if not response.url.startswith('https://'):
                raise ValueError('Refusing non-HTTPS response')
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError('Metadata exceeds size limit')
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.metadata-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
