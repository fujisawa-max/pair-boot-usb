#!/usr/bin/env python3
"""Allowlisted privileged GUI worker. Native actions are owner-triggered, never automatic."""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / 'toolkit'
if not (TOOLS / 'fleet.py').is_file():
    TOOLS = HERE.parent / 'tools'
spec = importlib.util.spec_from_file_location('gui_fleet_core', TOOLS / 'fleet.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
RUN_ROOT = Path('/run/gpu-linux-gui')
OPERATIONS = {'environment', 'config-open', 'config-close', 'status', 'collect', 'merge', 'deploy',
              'registration-preview', 'register', 'mok-check', 'mok-import', 'mok-delete',
              'mok-revoke-import', 'mok-revoke-delete'}


def run(args, secret_input=None, allowed=(0,)):
    result = subprocess.run(args, input=secret_input, text=True, capture_output=True,
                            timeout=60, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode not in allowed:
        # Password operations must never echo utility output into the response/log.
        detail = '' if secret_input is not None else (result.stderr.strip() or result.stdout.strip())
        raise ValueError(f'{args[0]} の処理に失敗しました（終了コード {result.returncode}）。{detail}')
    return result


def output(*args):
    return run(list(args)).stdout.strip()


def configuration_device():
    live = output('findmnt', '-n', '-o', 'SOURCE', '--target', '/run/initramfs/live')
    if not live.startswith('/dev/'):
        raise ValueError('Fedora Liveの物理USBを確認できません。')
    live = str(Path(live).resolve())
    parent = output('lsblk', '-dn', '-o', 'PKNAME', live)
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', parent) or output('lsblk', '-dn', '-o', 'TRAN', '/dev/' + parent) != 'usb':
        raise ValueError('起動Liveの親ディスクがUSBではありません。')
    children = []
    for entry in (Path('/sys/class/block') / parent).resolve().iterdir():
        if (entry / 'partition').is_file() and (entry / 'partition').read_text().strip() == '3':
            children.append('/dev/' + entry.name)
    if len(children) != 1:
        raise ValueError('同じ起動USBの第3設定領域を特定できません。')
    device = children[0]
    if output('blkid', '-p', '-s', 'TYPE', '-o', 'value', device) != 'vfat' or output('blkid', '-p', '-s', 'LABEL', '-o', 'value', device) != 'GPU-LOGS':
        raise ValueError('起動USBの設定領域がGPU-LOGS/FATではありません。')
    return device


def caller_ids():
    raw = os.environ.get('SUDO_UID', os.environ.get('PKEXEC_UID', str(os.getuid())))
    if not raw.isdecimal():
        raise ValueError('呼出元ユーザーを確認できません。')
    uid = int(raw)
    return uid, pwd.getpwuid(uid).pw_gid


def configuration(mode):
    if mode not in ('ro', 'rw'):
        raise ValueError('不正なマウントモードです。')
    device = configuration_device()
    found = run(['findmnt', '-n', '-r', '-o', 'TARGET', '-S', device], allowed=(0, 1))
    mounts = found.stdout.strip().splitlines()
    if len(mounts) > 1:
        raise ValueError('設定領域が複数箇所で使用中です。二重マウントは行いません。')
    if mounts:
        mount = Path(mounts[0]).resolve()
        core.live_config(mount)
        opts = output('findmnt', '-n', '-o', 'OPTIONS', '--target', str(mount)).split(',')
        uid, gid = caller_ids()
        if mode not in opts or mount.stat().st_uid != uid:
            run(['mount', '-o', f'remount,{mode},nodev,nosuid,noexec,uid={uid},gid={gid},fmask=0177,dmask=0077', str(mount)])
    else:
        if RUN_ROOT.is_symlink():
            raise ValueError('不正なGUI実行ディレクトリです。')
        RUN_ROOT.mkdir(mode=0o755, exist_ok=True)
        mount = RUN_ROOT / 'config'
        if mount.is_symlink():
            raise ValueError('不正な設定マウント先です。')
        mount.mkdir(mode=0o755, exist_ok=True)
        uid, gid = caller_ids()
        opts = f'{mode},nodev,nosuid,noexec,uid={uid},gid={gid},fmask=0177,dmask=0077'
        run(['mount', '-t', 'vfat', '-o', opts, device, str(mount)])
    core.live_config(mount)
    if mode not in output('findmnt', '-n', '-o', 'OPTIONS', '--target', str(mount)).split(','):
        raise ValueError('設定領域の実際のマウントモードが一致しません。')
    return mount


def bounded_input(value, mount):
    if not isinstance(value, str) or any(c in value for c in '\0\r\n'):
        raise ValueError('入力ファイル名が不正です。')
    path = Path(value)
    if path.is_symlink() or not path.resolve().is_relative_to(mount.resolve()):
        raise ValueError('入力JSONは、この起動USBのGPU-LOGS内から選択してください。')
    core.read_input(path)
    return path


def new_output(name, mount):
    core.fleet.validate_storage_directory(name)
    if not name.endswith('.json'):
        raise ValueError('出力名は単一の.jsonファイル名にしてください。')
    path = mount / name
    if path.exists() or path.is_symlink():
        raise ValueError('出力ファイルが存在します。別のファイル名を指定してください。')
    return path


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != data:
        raise ValueError('出力ファイルの読み戻しが一致しません。')


def environment():
    try:
        secure = {'ok': core.secure_boot_state(), 'message': 'Secure Boot有効、SetupMode無効'}
    except (OSError, ValueError) as error:
        secure = {'ok': False, 'message': str(error)}
    gpus = []
    for path in Path('/sys/bus/pci/devices').iterdir():
        try:
            if (path / 'vendor').read_text().strip() == '0x10de':
                gpus.append({'pci_address': path.name, 'device': (path / 'device').read_text().strip()})
        except OSError:
            continue
    return {'secure_boot': secure, 'gpus': gpus,
            'tools': {name: bool(shutil.which(name)) for name in ('mokutil', 'openssl', 'mount', 'lsblk', 'blkid')},
            'block_devices': json.loads(output('lsblk', '-J', '-o', 'NAME,TRAN,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS'))}


def certificate(path, mount):
    cert = Path(path)
    if cert.is_symlink() or cert.resolve() != (mount / 'signing-cert.der').resolve() or cert.name != 'signing-cert.der':
        raise ValueError('この設定USB直下のsigning-cert.derだけを選択してください。')
    if not cert.is_file() or not 1 <= cert.stat().st_size <= 128 * 1024:
        raise ValueError('証明書が通常の公開DERファイルではありません。')
    pin = json.loads((HERE / 'trust.json').read_text())
    sha = hashlib.sha256(cert.read_bytes()).hexdigest()
    if pin.get('schema') != 1 or pin.get('certificate_sha256') != sha:
        raise ValueError('証明書のSHA-256が媒体作成時の値と一致しません。')
    core.secure_boot_state()
    if 'ro' not in output('findmnt', '-n', '-o', 'OPTIONS', '--target', str(cert)).split(','):
        raise ValueError('MOK操作の証明書領域は読み取り専用である必要があります。')
    description = output('openssl', 'x509', '-inform', 'DER', '-in', str(cert), '-noout', '-subject', '-dates', '-fingerprint', '-sha256')
    run(['openssl', 'x509', '-inform', 'DER', '-in', str(cert), '-noout', '-checkend', '0'])
    return cert, sha, hashlib.sha1(cert.read_bytes(), usedforsecurity=False).hexdigest(), description


def pending_fingerprints(text):
    if not text.strip():
        return set()
    found = re.findall(r'SHA1 Fingerprint:\s*((?:[0-9a-fA-F]{2}:){19}[0-9a-fA-F]{2})', text)
    if not found:
        raise ValueError('MOK申請一覧の形式を確認できません。CLIで状態を確認してください。')
    return {value.replace(':', '').lower() for value in found}


def mok_status(cert, mount):
    cert, sha, sha1, description = certificate(cert, mount)
    test = run(['mokutil', '--test-key', str(cert)], allowed=(0, 1))
    # Some shipped mokutil versions return zero even for "is not enrolled".
    # Require a recognized statement AND a matching actual MOK-list fingerprint.
    statement = test.stdout.strip()
    negative = statement == f'{cert} is not enrolled'
    positive = statement == f'{cert} is already enrolled'
    if test.stderr.strip() or not (negative or positive):
        raise ValueError('MOKの既登録状態を確認できません。test-keyの出力をCLIで確認してください。')
    enrolled_text = output('mokutil', '--list-enrolled')
    enrolled_fingerprints = pending_fingerprints(enrolled_text)
    listed = sha1 in enrolled_fingerprints
    if listed != positive or (positive and test.returncode != 0):
        raise ValueError('MOKのtest-key結果と実際の登録一覧が一致しません。登録状態を確認してください。')
    pending_new = pending_fingerprints(output('mokutil', '--list-new'))
    pending_delete = pending_fingerprints(output('mokutil', '--list-delete'))
    return {'certificate': str(cert), 'sha256': sha, 'sha1': sha1, 'description': description,
            'enrolled': listed, 'test_key_exit': test.returncode,
            'test_key_output': statement, 'verification': 'test-key-and-enrolled-fingerprint',
            'pending_import': sorted(pending_new), 'pending_delete': sorted(pending_delete)}


def mok_action(operation, request, mount, password):
    state = mok_status(request.get('certificate', str(mount / 'signing-cert.der')), mount)
    if operation == 'mok-check':
        return state
    if request.get('confirm') != operation.upper():
        raise ValueError('MOK操作の明示確認が必要です。')
    if request.get('expected_sha256') != state['sha256']:
        raise ValueError('確認後に証明書が変わりました。再確認してください。')
    own = state['sha1']
    new, deleted = set(state['pending_import']), set(state['pending_delete'])
    if (new | deleted) - {own}:
        raise ValueError('別の証明書のMOK申請があります。先にその状態を確認してください。')
    if operation == 'mok-import' and (state['enrolled'] or own in new):
        message = ('このPCには登録済みです。再申請は不要です。' if state['enrolled'] else '登録申請済み・未承認です。再起動後、MokManagerで承認してください。')
        if own in deleted:
            message += ' この証明書の削除申請も残っています。状態を確認してください。'
        return {**state, 'message': message}
    if operation == 'mok-delete' and (not state['enrolled'] or own in deleted):
        return {**state, 'message': '未登録、または削除申請済みです。重複申請はしません。'}
    if (operation == 'mok-import' and deleted) or (operation == 'mok-delete' and new):
        raise ValueError('逆方向の申請が残っています。状態を確認してください。')
    if operation in ('mok-revoke-import', 'mok-revoke-delete'):
        active = new if operation.endswith('import') else deleted
        if active != {own}:
            raise ValueError('この証明書だけの取消可能な申請を確認できません。')
        flag = '--revoke-import' if operation.endswith('import') else '--revoke-delete'
        run(['mokutil', flag])
        return {'sha256': state['sha256'], 'message': 'この証明書の申請取消を要求しました。状態を再確認してください。'}
    if not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9]{8,16}', password):
        raise ValueError('MOK確認用パスワードは8〜16文字の半角英数字にしてください。')
    flag = '--import' if operation == 'mok-import' else '--delete'
    run(['mokutil', flag, state['certificate']], secret_input=password + '\n' + password + '\n')
    return {'sha256': state['sha256'], 'request_sent': True, 'owner_approval_pending': True, 'message': 'MOK申請を受け付けました。再起動後のMokManagerで最終承認してください。承認はまだ完了していません。'}


def dispatch(request, password=None):
    operation = request.get('operation')
    if operation not in OPERATIONS or any(key in request for key in ('password', 'argv', 'command', 'tools_path')):
        raise ValueError('対応していない操作要求です。')
    if operation == 'environment':
        return environment()
    if operation == 'config-close':
        mount = configuration('ro')
        run(['umount', str(mount)])
        return {'message': '設定USBをアンマウントしました。'}
    if operation == 'config-open':
        if request.get('mode') == 'rw' and request.get('confirm') != 'CONFIG-OPEN-RW':
            raise ValueError('設定USBを書込み可能にする確認が必要です。')
        mount = configuration(request.get('mode', 'ro'))
        return {'mount': str(mount), 'mode': request.get('mode', 'ro')}
    writes = {'register', 'collect', 'merge', 'deploy'}
    if operation in writes and request.get('confirm') != operation.upper():
        raise ValueError('書込み操作の確認が必要です。')
    mount = configuration('rw' if operation in writes else 'ro')
    if operation.startswith('mok-'):
        return mok_action(operation, request, mount, password)
    if operation in ('registration-preview', 'register'):
        core.secure_boot_state()
        inventory = bounded_input(request.get('inventory'), mount)
        evidence = bounded_input(request['evidence'], mount) if request.get('evidence') else None
        gpu = request.get('gpu_bdf', '')
        if operation == 'registration-preview':
            context = core.registration_context(inventory, gpu, mount, evidence)
            return {'record': context['record'], 'review_digest': core.registration_digest(context, mount),
                    'message': '保存先とGPUを確認してください。確定するまで台帳へ書き込みません。'}
        if not re.fullmatch(r'[0-9a-f]{64}', str(request.get('review_digest', ''))):
            raise ValueError('登録プレビューの確認情報が必要です。')
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            core.register(inventory, gpu, mount, 'REGISTER', evidence, expected_review=request['review_digest'])
        return {'message': '設定USBの台帳へ登録しました。NTFSやroot.imgは変更していません。', 'details': captured.getvalue()}
    if operation in ('status', 'collect'):
        registry = core.fleet.load(mount / 'gpu-config/registry.json')
        if operation == 'status':
            return registry
        target = new_output(request.get('output_name'), mount)
        write_new(target, core.fleet.encode(registry))
        return {'message': '台帳を回収しました。', 'output': str(target)}
    if operation == 'merge':
        names = request.get('inputs')
        if not isinstance(names, list) or len(names) < 2 or len(names) > 100:
            raise ValueError('マージするJSONを2〜100個選択してください。')
        files = [bounded_input(name, mount) for name in names]
        marker = core.read_input(mount / 'usb-config.json')
        registry = core.fleet.merge([core.read_input(path) for path in files], request.get('generation'), marker['root_version'])
        target = new_output(request.get('output_name'), mount)
        write_new(target, core.fleet.encode(registry))
        return {'message': '台帳をマージしました。', 'output': str(target), 'generation': registry['generation']}
    if operation == 'deploy':
        source = bounded_input(request.get('registry'), mount)
        version = core.read_input(mount / 'usb-config.json')['root_version']
        registry = core.fleet.for_version(core.fleet.load(source), version)
        core.fleet.deploy(registry, mount)
        return {'message': 'この起動USBの設定領域へ台帳を配布しました。'}
    raise ValueError('対応していない操作です。')


def explain_error(message):
    if 'different PC' in message:
        return '候補JSONが別のPC用です。このPCのWindowsで収集したJSONを選択してください。\n詳細: ' + message
    if 'SecureBoot' in message or 'Secure Boot' in message:
        return 'Secure Bootの状態を確認できないか、この操作の条件を満たしていません。環境確認の結果を確認してください。\n詳細: ' + message
    if 'BitLocker' in message:
        return 'BitLockerの暗号化解除・保護状態を確認できないため停止しました。Windowsの観測情報と選択した領域を確認してください。\n詳細: ' + message
    if 'Insufficient' in message:
        return '保存先の空き容量が不足しています。初回6GiB rootのコピーには7GiB以上の空きが必要です。\n詳細: ' + message
    if 'Conflicting configuration' in message or 'remove/change' in message:
        return '既存台帳と保存先設定が競合しています。既存の登録は自動上書きしません。\n詳細: ' + message
    return message


def main():
    try:
        if os.geteuid() != 0:
            raise ValueError('この実行部には管理者認証が必要です。GUIランチャーから実行してください。')
        line = sys.stdin.readline(65537)
        if len(line) > 65536 or not line.endswith('\n'):
            raise ValueError('操作要求が空か、大きすぎます。')
        request = json.loads(line)
        if not isinstance(request, dict):
            raise ValueError('操作要求が不正です。')
        password = None
        if request.get('operation') in ('mok-import', 'mok-delete'):
            password = sys.stdin.readline(258).rstrip('\n')
        if RUN_ROOT.is_symlink():
            raise ValueError('不正なGUI実行ディレクトリです。')
        RUN_ROOT.mkdir(mode=0o755, exist_ok=True)
        fd = os.open(RUN_ROOT / 'operation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = dispatch(request, password)
        finally:
            os.close(fd)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        message = str(error)
        if isinstance(error, subprocess.TimeoutExpired):
            message = '処理が時間切れになりました。MOK申請などは状態を再確認し、自動再試行しないでください。'
        print(json.dumps({'ok': False, 'error': explain_error(message)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
