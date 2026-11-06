#!/usr/bin/env python3
"""Zenity frontend for the allowlisted owner-triggered Linux worker."""
import contextlib
import datetime
import os
import tempfile
import json
import html
from pathlib import Path
import shutil
import runpy
import subprocess
import sys

HERE = Path(__file__).resolve().parent
HELP_PROCESS = None


class Cancelled(Exception):
    pass


def dialog(*args):
    result = subprocess.run(['zenity', *args], capture_output=True, text=True)
    if result.returncode == 1:
        if '--extra-button=使い方' in args and result.stdout.rstrip('\n') == '使い方':
            return '使い方'
        raise Cancelled()
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Dialog failed')
    return result.stdout.rstrip('\n')


def choose(title, values, help_button=False):
    if not values:
        raise ValueError('選択できる対象がありません。GPUや設定USBの検出状況は環境確認で調べてください。')
    return dialog('--list', '--title=' + title, '--column=選択', '--width=850', '--height=400', *(['--extra-button=使い方'] if help_button else []), *values)


def choose_combo(title, values):
    if not values or any(any(char in value for char in ('|', '\n', '\r', '\t', '\0')) for value in values):
        raise ValueError('No safe mounted configuration choices available')
    selected = dialog('--forms', '--title=' + title, '--add-combo=設定USB',
                      '--combo-values=' + '|'.join(values))
    if selected not in values:
        raise ValueError('Select a configuration USB from the dropdown')
    return selected


def mounted_configurations(data):
    found = []
    for item in data.get('blockdevices', []):
        if item.get('label') == 'GPU-LOGS' and item.get('fstype') == 'vfat':
            found.extend(str(p) for p in item.get('mountpoints', []) if p and not any(c in str(p) for c in '\n\r\0'))
        found.extend(mounted_configurations({'blockdevices': item.get('children', [])}))
    return sorted(set(found))


def gpu_choices(sysfs=Path('/sys/bus/pci/devices')):
    policy = HERE.parent / 'tools/fleet-registry.py'
    if not policy.is_file():
        policy = HERE.parent / 'runtime/fleet-registry.py'
    supported = runpy.run_path(str(policy))['GPU_NORMAL_IDS']
    values = []
    for device in sysfs.iterdir():
        try:
            if (device / 'vendor').read_text().strip() == '0x10de' and '10de:' + (device / 'device').read_text().strip().removeprefix('0x').lower() in supported:
                values.append(device.name)
        except OSError:
            continue
    return sorted(values)


@contextlib.contextmanager
def busy():
    progress = subprocess.Popen(['zenity', '--progress', '--pulsate', '--auto-close', '--no-cancel',
                                 '--title=GPU Linux: 処理中', '--text=確認・処理を行っています。完了までお待ちください。'],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    try:
        yield
    finally:
        if progress.poll() is None:
            try:
                progress.communicate('100\n', timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                progress.terminate()
                progress.wait()


def privilege_command():
    worker = str(HERE / 'fedora-worker.py')
    base = ['/usr/bin/python3', worker]
    if os.geteuid() == 0:
        return base
    if shutil.which('sudo'):
        probe = subprocess.run(['sudo', '-n', '/usr/bin/true'], capture_output=True)
        if probe.returncode == 0:
            return ['sudo', '-n', *base]
    if shutil.which('pkexec'):
        return ['pkexec', *base]
    raise ValueError('管理者認証の手段がありません。Fedora Liveのsudo/pkexecを確認してください。')


def invoke(request, password=None):
    # The JSON metadata never contains passwords. Secret is a separate stdin line.
    payload = json.dumps(request, ensure_ascii=False) + '\n'
    if password is not None:
        payload += password + '\n'
    command = privilege_command()
    with busy():
        result = subprocess.run(command, input=payload, capture_output=True, text=True)
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError('実行部から結果を取得できません。管理者認証の取消や媒体パスを確認してください。\n' + result.stderr.strip()) from error
    if not response.get('ok'):
        raise ValueError(response.get('error', '操作を完了できませんでした。'))
    if result.returncode:
        raise ValueError('実行部が異常終了しました。状態を確認し、自動で再実行しないでください。')
    return response['result']


def show(title, value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='gpu-gui-', suffix='.txt') as file:
        file.write(text)
        file.flush()
        dialog('--text-info', '--title=' + title, '--filename=' + file.name, '--width=760', '--height=480')


def confirm(title, text):
    dialog('--question', '--default-cancel', '--title=' + title, '--width=650', '--text=' + html.escape(text))


def opened_config():
    value = invoke({'operation': 'config-open', 'mode': 'ro'})
    return value['mount']


def pick_json(mount, title, multiple=False):
    args = ['--file-selection', '--title=' + title, '--filename=' + mount + '/', '--file-filter=JSON | *.json']
    if multiple:
        args += ['--multiple', '--separator=\n']
    value = dialog(*args)
    return value.splitlines() if multiple else value


def output_name(prefix):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    return dialog('--entry', '--title=新規JSONのファイル名', '--text=設定USBの直下へ保存します。既存ファイルは上書きしません。',
                  '--entry-text=' + prefix + '-' + stamp + '.json')


def mok_summary(state):
    own = state['sha1']
    if state['enrolled']:
        summary = 'このPCには、このMOK証明書が登録済みです。追加の登録申請は不要です。'
    elif own in state['pending_import']:
        summary = 'このPCには登録申請済みですが、登録は未完了です。再起動後、MokManagerで承認してください。'
    else:
        summary = 'このPCには、このMOK証明書は未登録です。登録申請もありません。'
    if own in state['pending_delete']:
        summary += '\nこの証明書の削除申請が残っています。再起動前に削除申請の扱いを確認してください。'
    if (set(state['pending_import']) | set(state['pending_delete'])) - {own}:
        summary += '\n別の証明書の申請もあります。そちらの状態も確認してください。'
    return summary


def mok_password():
    first = dialog('--password', '--title=MOK確認用パスワード（8〜16文字の半角英数字）')
    second = dialog('--password', '--title=同じMOK確認用パスワードをもう一度入力')
    import re
    if first != second or not re.fullmatch(r'[A-Za-z0-9]{8,16}', first):
        raise ValueError('パスワードが一致しないか、8〜16文字の半角英数字ではありません。申請していません。')
    return first


def show_usage():
    """Separate, unprivileged viewer: never wait for it or invoke the worker."""
    global HELP_PROCESS
    if HELP_PROCESS is not None and HELP_PROCESS.poll() is None:
        return
    path = HERE / 'usage.txt'
    if not path.is_file():
        raise ValueError('使い方ファイルがありません。GUI一式をコピーしてください。')
    HELP_PROCESS = subprocess.Popen(
        ['zenity', '--text-info', '--title=GPU Linux — 使い方（操作を続けられます）',
         '--filename=' + str(path), '--width=760', '--height=650'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def main():
    if not shutil.which('zenity'):
        sys.exit('Zenityがありません。CLI手順を利用してください。')
    operations = {
        '環境確認（Secure Boot・GPU・領域一覧）': 'environment',
        'Windows候補から保存先・GPUを登録': 'register',
        'PC登録一覧（台帳）を表示': 'status', '台帳をJSONへ回収': 'collect',
        '複数の台帳JSONをマージ': 'merge', '統合台帳をこのUSBへ配布': 'deploy',
        'MOK公開証明書・登録状態を確認': 'mok-check', 'MOK登録を申請': 'mok-import',
        'MOK削除を申請': 'mok-delete', 'MOK登録申請を取り消す': 'mok-revoke-import',
        'MOK削除申請を取り消す': 'mok-revoke-delete', '設定USBをアンマウント': 'config-close',
    }
    while True:
        try:
            try:
                selection = choose('GPU Linux 管理GUI', list(operations), help_button=True)
            except Cancelled:
                return
            if selection == '使い方':
                show_usage()
                continue
            operation = operations[selection]
            if operation == 'environment':
                show('環境確認（検査で停止する理由もここで確認できます）', invoke({'operation': operation}))
                continue
            if operation == 'config-close':
                confirm('アンマウントの確認', 'この起動USBの設定領域をアンマウントします。開いている設定ファイルは閉じてください。')
                show('設定USB', invoke({'operation': operation}))
                continue
            mount = opened_config()
            if operation.startswith('mok-'):
                cert = dialog('--file-selection', '--title=この設定USBの公開証明書を選択',
                              '--filename=' + mount + '/signing-cert.der', '--file-filter=公開DER | *.der')
                state = invoke({'operation': 'mok-check', 'certificate': cert})
                show('MOK証明書・状態（SHA-256を確認してください）', mok_summary(state) + '\n\n詳細:\n' + json.dumps(state, ensure_ascii=False, indent=2))
                if operation == 'mok-check':
                    continue
                if operation == 'mok-import' and (state['enrolled'] or state['sha1'] in state['pending_import']):
                    show('MOK', mok_summary(state))
                    continue
                if operation == 'mok-delete' and (not state['enrolled'] or state['sha1'] in state['pending_delete']):
                    show('MOK', '未登録、または削除申請済みです。重複申請はしません。')
                    continue
                warning = ('この証明書を削除すると、次回起動から署名済みgpu-unlock/NVIDIAモジュールを読み込めなくなる可能性があります。\n' if operation == 'mok-delete' else '')
                confirm('MOK操作の最終確認', warning + '操作: ' + selection + '\nSHA-256: ' + state['sha256'] + '\n申請しますか？ 最終承認は再起動後のMokManagerで行います。')
                password = mok_password() if operation in ('mok-import', 'mok-delete') else None
                request = {'operation': operation, 'certificate': cert, 'expected_sha256': state['sha256'], 'confirm': operation.upper()}
                try:
                    result = invoke(request, password)
                finally:
                    password = None
                show('MOK操作の結果', result)
                continue
            if operation == 'register':
                inventory = pick_json(mount, 'この設定USBのWindows候補JSONを選択')
                gpu = choose('登録するRTX 30シリーズのPCIアドレス', gpu_choices())
                evidence = None
                choice = choose('BitLockerの確認情報', ['候補JSONに含まれる観測情報を使う', '別の観測JSONを選択する'])
                if choice.startswith('別'):
                    evidence = pick_json(mount, 'BitLocker観測JSONを選択')
                request = {'operation': 'registration-preview', 'inventory': inventory, 'gpu_bdf': gpu}
                if evidence:
                    request['evidence'] = evidence
                review = invoke(request)
                show('登録前の確認（まだ保存していません）', review)
                confirm('登録を確定', '表示したPC・保存先UUID・フォルダ・GPUを、この起動USBの台帳へ登録します。NTFSやroot.imgは変更しません。')
                request.update(operation='register', confirm='REGISTER', review_digest=review['review_digest'])
                show('登録結果', invoke(request))
                continue
            request = {'operation': operation}
            if operation in ('collect', 'merge'):
                request['output_name'] = output_name('fleet-' + operation)
            if operation == 'merge':
                request['inputs'] = pick_json(mount, '設定USB内の台帳JSONを複数選択', multiple=True)
                request['generation'] = int(dialog('--entry', '--title=新しい台帳世代', '--text=全入力より大きい世代番号', '--entry-text=1'))
            if operation == 'deploy':
                request['registry'] = pick_json(mount, '配布する統合台帳JSONを選択')
            if operation != 'status':
                confirm('実行内容の確認', selection + '\n' + json.dumps(request, ensure_ascii=False, indent=2) + '\nこの設定USBへ書き込みます。よろしいですか？')
                request['confirm'] = operation.upper()
            show('操作結果', invoke(request))
        except Cancelled:
            # Cancelling an input/confirmation returns to the menu, with no write.
            continue
        except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
            try:
                dialog('--error', '--title=操作を完了できませんでした', '--width=700', '--text=' + html.escape(str(error)))
            except Cancelled:
                pass


if __name__ == '__main__':
    main()
