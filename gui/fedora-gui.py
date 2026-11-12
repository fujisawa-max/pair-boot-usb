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
UI = runpy.run_path(str(HERE / 'ui-model.py'))
T = UI['text']


class Cancelled(Exception):
    pass


def dialog(*args):
    result = subprocess.run(['zenity', *args], capture_output=True, text=True,
                            env={**os.environ, 'LANGUAGE': UI['language']()})
    if result.returncode == 1:
        if '--extra-button=' + T('help') in args and result.stdout.rstrip('\n') == T('help'):
            return T('help')
        raise Cancelled()
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Dialog failed')
    return result.stdout.rstrip('\n')


def choose(title, values, help_button=False):
    if not values:
        raise ValueError(T('required'))
    return dialog('--list', '--title=' + title, '--column=' + T('operation'), '--width=850', '--height=400', *(['--extra-button=' + T('help')] if help_button else []), '--', *values)


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
                                 '--title=' + T('working'), '--text=' + T('working')],
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
    raise ValueError(T('failure') + ': sudo / pkexec')


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
        raise ValueError(T('failure') + '\n' + result.stderr.strip()) from error
    if not response.get('ok'):
        raise ValueError(T('failure') + '\n' + T('details') + ': ' + response.get('error', ''))
    if result.returncode:
        raise ValueError(T('failure'))
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


def pick_json(mount, title, multiple=False, registry=False):
    folder = Path(mount) / ('gpu-config' if registry else 'data')
    initial = folder if folder.is_dir() and not folder.is_symlink() else Path(mount)
    args = ['--file-selection', '--title=' + title, '--filename=' + str(initial) + '/', '--file-filter=JSON | *.json']
    if multiple:
        args += ['--multiple', '--separator=\n']
    value = dialog(*args)
    return value.splitlines() if multiple else value


def output_name(prefix, registry=False):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    return dialog('--entry', '--title=' + T('output'), '--text=' + T('new_registry_file' if registry else 'new_file'),
                  '--entry-text=' + prefix + '-' + stamp + '.json')


def mok_summary(state):
    own = state['sha1']
    if state['enrolled']:
        summary = T('mok_enrolled')
    elif own in state['pending_import']:
        summary = T('mok_pending')
    else:
        summary = T('mok_absent')
    if own in state['pending_delete']:
        summary += '\n' + T('mok_delete_pending')
    if (set(state['pending_import']) | set(state['pending_delete'])) - {own}:
        summary += '\n' + T('mok_other_pending')
    return summary


def mok_password():
    first = dialog('--password', '--title=' + T('password'))
    second = dialog('--password', '--title=' + T('password_again'))
    import re
    if first != second or not re.fullmatch(r'[A-Za-z0-9]{8,16}', first):
        raise ValueError(T('bad_password'))
    return first


def show_usage():
    """Separate, unprivileged viewer: never wait for it or invoke the worker."""
    global HELP_PROCESS
    if HELP_PROCESS is not None and HELP_PROCESS.poll() is None:
        return
    path = HERE / ('usage.' + UI['language']() + '.txt')
    if not path.is_file():
        path = HERE / 'usage.txt'
    if not path.is_file():
        raise ValueError('使い方ファイルがありません。GUI一式をコピーしてください。')
    HELP_PROCESS = subprocess.Popen(
        ['zenity', '--text-info', '--title=' + T('help'),
         '--filename=' + str(path), '--width=760', '--height=650'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def select_id(title, entries, help_button=False):
    labels = [T(item['label']) for item in entries]
    choice = choose(title, labels, help_button)
    if choice == T('help') and help_button:
        return None
    return entries[labels.index(choice)]['id']


def registry_table(value):
    rows = UI['pc_rows'](value)
    if not rows:
        show(T('fedora.Status'), {'schema': value['schema'], 'generation': value['generation'], 'records': []})
        return
    flat = [cell for row in rows for cell in row]
    summary = (T('fedora.Status') + ' — ' + T('schema') + ': ' + str(value['schema'])
               + ' / ' + T('current_generation') + ': ' + str(value['generation'])
               + ' / ' + T('host_count') + ': ' + str(len(rows)))
    selected = dialog('--list', '--title=' + summary, '--width=1000', '--height=500',
                      '--column=' + T('name'), '--column=' + T('mode'), '--column=' + T('folder'),
                      '--column=' + T('uuid'), '--print-column=4', '--', *flat)
    if selected:
        record = next(item for item in value['records'] if item['hardware_uuid'] == selected)
        show(T('details'), record)


def name_update(mount, request):
    registry = invoke({'operation': 'registry-read', 'registry': request['inputs'][0]})
    drafts = {item['hardware_uuid']: item.get('windows_computer_name') or '' for item in registry['records']}
    while True:
        action = select_id(T('name'), [
            {'id': 'edit', 'label': 'edit_pc_name'}, {'id': 'save', 'label': 'save_name_changes'}])
        if action == 'save':
            updates = [{'hardware_uuid': item['hardware_uuid'], 'windows_computer_name': drafts[item['hardware_uuid']]}
                       for item in registry['records']
                       if drafts[item['hardware_uuid']] != (item.get('windows_computer_name') or '')]
            if not updates:
                show(T('name'), T('no_name_changes'))
                continue
            request['name_updates'] = updates
            return registry['generation'] + 1
        try:
            display = {**registry, 'records': [{**item, 'windows_computer_name': drafts[item['hardware_uuid']]}
                                               for item in registry['records']]}
            rows = UI['pc_rows'](display)
            selected = dialog('--list', '--title=' + T('pick_pc'), '--width=1000', '--height=450',
                              '--column=' + T('name'), '--column=' + T('mode'), '--column=' + T('folder'),
                              '--column=' + T('uuid'), '--print-column=4', '--', *[cell for row in rows for cell in row])
            if selected not in drafts:
                raise ValueError(T('required'))
            source = select_id(T('name'), [
                {'id': 'manual', 'label': 'name'}, {'id': 'candidate', 'label': 'name_candidate'}])
            if source == 'candidate':
                candidate = invoke({'operation': 'candidate-name', 'inventory': pick_json(mount, T('inventory')), 'host_uuid': selected})
                name = candidate['windows_computer_name']
            else:
                name = dialog('--entry', '--title=' + T('name'), '--text=' + T('safe_name_update'),
                              '--entry-text=' + drafts[selected])
            if not name:
                raise ValueError(T('required'))
            drafts[selected] = name
        except Cancelled:
            continue
        except ValueError as error:
            show(T('failure'), str(error))


def main():
    if not shutil.which('zenity'):
        sys.exit('Zenity is required; use the CLI instructions.')
    categories = [{'id': item, 'label': item} for item in ('registration', 'registry', 'mok', 'maintenance')]
    while True:
        try:
            try:
                category = select_id(T('title') + ' — ' + T('pick_category'), categories, True)
            except Cancelled:
                return
            if category is None:
                show_usage()
                continue
            try:
                entries = UI['operations']('fedora', category)
                selected = select_id(T(category), entries)
                definition = next(item for item in entries if item['id'] == selected)
            except Cancelled:
                continue
            operation = {'Status': 'status', 'Collect': 'collect', 'Merge': 'merge', 'Deploy': 'deploy',
                         'Convert': 'convert', 'Name': 'name'}.get(selected, selected)
            if operation == 'environment':
                show(T(definition['label']), invoke({'operation': operation}))
                continue
            if operation == 'config-close':
                confirm(T('confirm'), T('unmount_notice'))
                show(T('result'), invoke({'operation': operation}))
                continue
            mount = opened_config()
            if operation.startswith('mok-'):
                cert = dialog('--file-selection', '--title=' + T('certificate'),
                              '--filename=' + mount + '/signing-cert.der', '--file-filter=DER | *.der')
                state = invoke({'operation': 'mok-check', 'certificate': cert})
                show(T('mok_details'), mok_summary(state) + '\n\n' + T('details') + ':\n' + json.dumps(state, ensure_ascii=False, indent=2))
                if operation == 'mok-check':
                    continue
                if operation == 'mok-import' and (state['enrolled'] or state['sha1'] in state['pending_import']):
                    continue
                if operation == 'mok-delete' and (not state['enrolled'] or state['sha1'] in state['pending_delete']):
                    continue
                warning = T('mok_delete_warning') + '\n' if operation == 'mok-delete' else ''
                confirm(T('confirm'), warning + T(definition['label']) + '\nSHA-256: ' + state['sha256'] + '\n' + T('mok_confirm'))
                password = mok_password() if operation in ('mok-import', 'mok-delete') else None
                request = {'operation': operation, 'certificate': cert, 'expected_sha256': state['sha256'], 'confirm': operation.upper()}
                try:
                    result = invoke(request, password)
                finally:
                    password = None
                show(T('result'), result)
                continue
            if operation in ('register', 'register-test'):
                test = operation == 'register-test'
                inventory = pick_json(mount, T('inventory'))
                gpu = None if test else choose(T('gpu'), gpu_choices())
                evidence_mode = select_id(T('evidence'), [{'id': 'embedded', 'label': 'embedded'}, {'id': 'separate', 'label': 'separate'}])
                request = {'operation': 'registration-preview', 'inventory': inventory, 'gpu_bdf': gpu, 'no_gpu_test': test}
                if evidence_mode == 'separate':
                    request['evidence'] = pick_json(mount, T('evidence'))
                review = invoke(request)
                show(T('registration_review'), review)
                confirm(T('confirm'), T('registration_confirm'))
                request.update(operation='register', confirm='REGISTER', review_digest=review['review_digest'])
                show(T('result'), invoke(request))
                continue
            if operation == 'status':
                registry_table(invoke({'operation': 'status'}))
                continue
            request = {'operation': operation}
            suggested = 1
            if operation in ('merge', 'convert', 'name'):
                source = pick_json(mount, T('files'), multiple=operation == 'merge', registry=True)
                request['inputs'] = source if isinstance(source, list) else [source]
                if operation == 'name':
                    suggested = name_update(mount, request)
                else:
                    values = [invoke({'operation': 'registry-read', 'registry': path}) for path in request['inputs']]
                    suggested = max(value['generation'] for value in values) + 1
                request['generation'] = int(dialog('--entry', '--title=' + T('generation'), '--text=' + T('conversion' if operation == 'convert' else 'generation'), '--entry-text=' + str(suggested)))
            if operation in ('collect', 'merge', 'convert', 'name'):
                request['output_name'] = output_name('fleet-' + operation, registry=operation != 'collect')
            if operation == 'deploy':
                request['registry'] = pick_json(mount, T('files'), registry=True)
            confirm(T('confirm'), T(definition['label']) + '\n' + T(definition['description']) + '\n' + T('this_usb') + '\n' + json.dumps(request, ensure_ascii=False, indent=2))
            request['confirm'] = operation.upper()
            show(T('result'), invoke(request))
        except Cancelled:
            # Cancelling any selection/confirmation performs no pending write.
            continue
        except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
            try:
                dialog('--error', '--title=' + T('failure'), '--width=700', '--text=' + html.escape(str(error)))
            except Cancelled:
                pass


if __name__ == '__main__':
    main()
