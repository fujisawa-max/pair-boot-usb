"""Virtual console routing is checked with fake descriptors, never real VTs."""
import configparser
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = runpy.run_path(str(ROOT / 'runtime/console-layout.py'))
MENU = runpy.run_path(str(ROOT / 'scripts/generate-boot-menu.py'))


class ConsoleLayoutTests(unittest.TestCase):
    def test_switch_uses_fixed_vts_and_closes_descriptor_on_failure(self):
        for screen, terminal in (('logs', 1), ('pair', 3)):
            calls = []
            def ioctl(fd, request, argument, *rest):
                calls.append((fd, request, bytes(argument) if isinstance(argument, (bytes,bytearray)) else argument, *rest))
                if request == 0x541C and argument[0] == 17:
                    argument[0] = 1
            LAYOUT['activate'](screen, lambda *args: 42,
                               ioctl, lambda fd: calls.append(('close', fd)))
            self.assertEqual(calls, [(42,0x541C,b'\x0b\x01'),(42,0x541C,b'\x11',True),
                                    (42,0x5606,terminal),(42,0x5607,terminal),('close',42)])
        calls = []
        def fail(*args):
            raise OSError('No virtual terminal')
        with self.assertRaises(OSError):
            LAYOUT['activate']('pair', lambda *args: 42, fail, calls.append)
        self.assertEqual(calls, [42])
        with self.assertRaises(ValueError):
            LAYOUT['activate']('tty0', lambda *_: self.fail('Invalid VT must not be opened'))

    def test_delayed_kernel_messages_stay_on_logs_after_pair_and_manual_switches(self):
        # Model the Linux VT driver's foreground default and explicit redirect;
        # never open a real console or change this host's kernel state.
        state={'foreground':1,'redirect':0};output={1:[],2:[],3:[]}
        def printk(message):output[state['redirect'] or state['foreground']].append(message)
        def ioctl(fd,request,argument,*rest):
            if request==0x541C:
                if argument[0]==11:state['redirect']=argument[1]
                elif argument[0]==17:argument[0]=state['redirect']
                else:self.fail('Unexpected console operation')
            elif request==0x5606:state['foreground']=argument
            else:self.assertEqual(request,0x5607)
        printk('early boot')
        LAYOUT['activate']('pair',lambda *args:42,ioctl,lambda fd:None)
        for terminal in (3,1,2,3):
            state['foreground']=terminal;printk('late kernel message')
        self.assertEqual(len(output[1]),5)
        self.assertEqual(output[2],[]);self.assertEqual(output[3],[])
        LAYOUT['activate']('logs',lambda *args:42,ioctl,lambda fd:None)
        self.assertEqual(state,{'foreground':1,'redirect':1})

    def test_failed_or_unverified_log_routing_never_activates_pair_screen(self):
        for failure in ('set','get','readback'):
            with self.subTest(failure=failure):
                calls=[];closed=[]
                def ioctl(fd,request,argument,*rest):
                    calls.append(request)
                    if (failure=='set' and argument[0]==11 or failure=='get' and argument[0]==17):
                        raise OSError('Synthetic console ioctl failure')
                    if argument[0]==17:argument[0]=0
                with self.assertRaises(OSError):
                    LAYOUT['activate']('pair',lambda *args:42,ioctl,closed.append)
                self.assertNotIn(0x5606,calls)
                self.assertEqual(closed,[42])

    def test_every_kernel_entry_fixes_console_to_logs(self):
        for menu in (MENU['generate']('1234-abcd', {}, True), MENU['generate_diagnostic']('1234-abcd')):
            lines = [line for line in menu.splitlines() if line.strip().startswith('linux ')]
            self.assertTrue(lines)
            for line in lines:
                self.assertEqual([word for word in line.split() if word.startswith('console=')], ['console=tty1'])

    def test_pair_uses_separate_console_and_returns_to_logs_when_stopped(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / 'runtime/systemd/gpu-pair.service')
        self.assertEqual(unit['Service']['TTYPath'], '/dev/tty3')
        self.assertIn('console-layout.py pair', unit['Service']['ExecStartPre'])
        self.assertIn('console-layout.py logs', unit['Service']['ExecStopPost'])
        self.assertEqual(unit['Unit']['Conflicts'], 'getty@tty3.service')
