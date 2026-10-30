import contextlib
import errno
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import install
import install_transaction as transaction
import installation_state as ownership
from installer_ui import Presentation
import desktop_runtime
import uninstall


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve();self.root = self.base / 'payload'
        self.ui = Presentation(self.base / 'diagnostics/install.log', stream=io.StringIO())
        self.addCleanup(self.ui.close)
        sync=patch.object(transaction,"synchronize")
        self.sync=sync.start();self.addCleanup(sync.stop)

    def execute(self, operation):
        transaction.execute(self.root, 'fixture', 'test', self.ui, operation)

    def fail(self, root, state):
        (root / 'work').mkdir();(root / 'work/partial-download').write_text('partial')
        raise RuntimeError('injected failure')

    def test_failed_acquisition_leaves_only_diagnostics(self):
        with self.assertRaisesRegex(RuntimeError, 'injected failure'):self.execute(self.fail)
        self.assertFalse(self.root.exists())
        record = next((self.base / 'diagnostics').glob('failed-*/installation.json'))
        self.assertEqual(json.loads(record.read_text())['transaction'], 'rolled-back')
        self.assertIn('injected failure', self.ui.log_path.read_text())

    def test_preexisting_empty_directory_is_restored(self):
        self.root.mkdir(mode=0o750)
        with self.assertRaises(RuntimeError):self.execute(self.fail)
        self.assertTrue(self.root.exists());self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o750)

    def test_readonly_private_sdk_directories_can_roll_back(self):
        def fail(root, state):
            sdk=root/'toolchain';sdk.mkdir()
            (sdk/'header.h').write_text('fixture')
            sdk.chmod(0o500)
            raise RuntimeError('fixture failure')
        with self.assertRaises(RuntimeError):self.execute(fail)
        self.assertFalse(self.root.exists())

    def test_committed_installation_is_never_replaced_or_rolled_back(self):
        self.execute(lambda root, state: (root / 'settings').write_text('keep'))
        before = {p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'already committed'):self.execute(self.fail)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()})

    def test_interrupted_preview_is_removed_before_new_attempt(self):
        with ownership.managed_directory(self.root, create=True, source='old', version='old') as (_, state):
            (self.root / 'logs').mkdir();(self.root / 'logs/old.log').write_text('old evidence')
            (self.root / 'old-download').write_text('partial')
            state['active'] = 'wine';ownership.save(self.root, state)
        def fresh(root, state):
            self.assertFalse((root / 'old-download').exists())
            self.assertEqual(state['source'], 'fixture')
            (root / 'new').write_text('committed')
        self.execute(fresh)
        self.assertTrue(ownership.load(self.root)['installed'])
        self.assertEqual(next((self.base / 'diagnostics').glob('failed-*/old.log')).read_text(), 'old evidence')

    def test_keyboard_interrupt_rolls_back(self):
        def interrupt(root, state):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.execute(interrupt)
        self.assertFalse(self.root.exists())

    def test_sigterm_stops_child_and_rolls_back_then_sigkill_recovers(self):
        module_dir = str(Path(transaction.__file__).parent)
        script = '''
import sys,io
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import install_transaction as tx
from installer_ui import Presentation
tx.synchronize=lambda root,state:None
base=Path(sys.argv[2]);ui=Presentation(base/'diagnostics/child.log',stream=io.StringIO())
def work(root,state):
    (root/'partial').write_text('partial')
    ui.run([sys.executable,'-c',"import os,time;from pathlib import Path;Path("+repr(str(base/'ready'))+").write_text(str(os.getpid()));time.sleep(60)"])
try:tx.execute(base/'payload','fixture','test',ui,work)
except KeyboardInterrupt:pass
finally:ui.close()
'''
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=sig):
                case = self.base / str(sig);case.mkdir()
                child = subprocess.Popen([sys.executable, '-c', script, module_dir, str(case)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                worker = None
                try:
                    deadline = time.monotonic() + 8
                    while not (case / 'ready').exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((case / 'ready').exists())
                    worker = int((case / 'ready').read_text())
                    child.send_signal(sig);_, err = child.communicate(timeout=8)
                    if sig == signal.SIGTERM:
                        self.assertEqual(child.returncode, 0, err)
                        self.assertFalse((case / 'payload').exists())
                        with self.assertRaises(ProcessLookupError):os.kill(worker, 0)
                    else:
                        # Simulate the next boot: the kernel has terminated the
                        # child too; recovery uses the durable partial receipt.
                        os.kill(worker, signal.SIGKILL)
                        txroot = case / 'payload'
                        transaction.execute(txroot, 'fixture', 'test', self.ui,
                            lambda root,state: self.assertFalse((root/'partial').exists()))
                        self.assertEqual(ownership.load(txroot)['transaction'], 'committed')
                finally:
                    if child.poll() is None:child.kill()
                    child.wait()
                    if worker is not None:
                        try:os.kill(worker, signal.SIGKILL)
                        except ProcessLookupError:pass
                    child.stdout.close();child.stderr.close()

    def test_rollback_failure_is_explicit_and_recoverable(self):
        with patch.object(uninstall, 'remove_owned', side_effect=PermissionError('fixture denied')):
            with self.assertRaisesRegex(RuntimeError, 'Rollback is incomplete'):self.execute(self.fail)
        self.assertEqual(ownership.load(self.root)['transaction'], 'rollback-failed')
        self.assertFalse(ownership.load(self.root).get('installed', False))
        self.execute(lambda root,state: None)
        self.assertEqual(ownership.load(self.root)['transaction'], 'committed')

    def test_failed_initial_journal_leaves_no_payload(self):
        with patch.object(ownership, 'save', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):self.execute(self.fail)
        self.assertFalse(self.root.exists())

    def test_commit_write_failure_rolls_back_even_after_receipt_replacement(self):
        original=ownership.save
        def fail_commit(root,state):
            original(root,state)
            if state.get('transaction')=='committed':raise OSError('commit fsync failure')
        with patch.object(ownership, 'save', side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, 'commit fsync failure'):
                self.execute(lambda root,state: (root/'file').write_text('payload'))
        self.assertFalse(self.root.exists())

    def test_payload_writeback_failure_rolls_back_before_commit(self):
        self.sync.side_effect=OSError(errno.EIO,'fixture writeback failure')
        with self.assertRaisesRegex(OSError,'writeback failure'):
            self.execute(lambda root,state:(root/'payload').write_text('data'))
        self.assertFalse(self.root.exists())

    def test_full_disk_cannot_prevent_rollback_by_blocking_diagnostic_writes(self):
        original=self.ui.log
        class FullDiskLog:
            def write(self,data):raise OSError(errno.ENOSPC,'fixture disk full')
            def flush(self):raise OSError(errno.ENOSPC,'fixture disk full')
        def fail(root,state):
            self.ui.log=FullDiskLog()
            raise RuntimeError('fixture disk exhaustion')
        try:
            with self.assertRaisesRegex(RuntimeError,'disk exhaustion'):self.execute(fail)
        finally:self.ui.log=original
        self.assertFalse(self.root.exists())

    def test_closed_terminal_cannot_prevent_rollback(self):
        class ClosedTerminal(io.StringIO):
            def write(self,data):raise BrokenPipeError(errno.EPIPE,'fixture terminal closed')
        self.ui.stream=ClosedTerminal()
        with self.assertRaisesRegex(RuntimeError,'injected failure'):self.execute(self.fail)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.ui.terminal_available)

    def test_pending_installation_cannot_launch(self):
        def pending(root, state):
            desktop = root / 'desktop';desktop.mkdir()
            with patch.object(desktop_runtime, 'session_report') as report:
                with self.assertRaisesRegex(RuntimeError, 'not committed'):
                    desktop_runtime.launch_with_feedback(desktop / 'desktop.json', {}, desktop, None)
                report.assert_not_called()
        self.execute(pending)

    def test_preflight_failure_rolls_back_and_keeps_report(self):
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.base / 'state')}), \
             patch.object(install.host_preflight, 'inventory', return_value={'errors':['fixture blocker'], 'warnings':[]}), \
             patch.object(install.os, 'geteuid', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(['--install', '--directory', str(self.root)]), 1)
        self.assertFalse(self.root.exists())
        self.assertTrue(list((self.base / 'state').rglob('host-*.json')))

    def test_failure_after_each_pipeline_command_removes_all_managed_artifacts(self):
        # Real orchestrator + real receipt/launcher/rollback filesystem effects;
        # download/build commands are replaced with small disposable payloads.
        desktop = self.base / 'Desktop';desktop.mkdir()
        apps = self.base / 'applications'
        rules = self.base / 'rule'
        outside = self.base / 'unrelated';outside.write_text('keep')
        commands = []
        args = install.parser().parse_args(['--install', '--directory', str(self.root)])
        fail_at = None
        def run(command, env=None):
            commands.append(list(map(str, command)))
            text = ' '.join(commands[-1])
            if command[0] == 'sudo':
                if '--remove' in command:rules.unlink(missing_ok=True);return
                rules.write_text('managed rule')
            elif 'install-entry' in command:
                (self.root / 'desktop').mkdir(exist_ok=True)
                desktop_runtime.install_entry(self.root / 'desktop/desktop.json', desktop / 'Penguin-LGHUB.desktop', applications=apps)
            elif '--output' in command and 'desktop_runtime.py' in text:
                (self.root / 'desktop').mkdir(exist_ok=True)
            else:
                (self.root / ('artifact-' + str(len(commands)))).write_text('partial')
            if len(commands) == fail_at:raise subprocess.CalledProcessError(1, command)
        self.ui.run = run
        with patch.object(install, 'space_check'), \
             patch.object(install.subprocess, 'check_output', return_value=str(desktop) + '\n'):
            install.pipeline(args, self.ui, {'session':'x11','selinux':'Disabled'})
            total = len(commands)
            uninstall.remove(self.root, self.ui)
            for fail_at in range(1, total + 1):
                commands.clear()
                with self.subTest(command=fail_at), self.assertRaises(subprocess.CalledProcessError):
                    install.pipeline(args, self.ui, {'session':'x11','selinux':'Disabled'})
                self.assertFalse(self.root.exists());self.assertFalse(rules.exists())
                self.assertFalse(list(desktop.glob('*.desktop')))
                self.assertFalse(list(apps.glob('*.desktop')))
                self.assertEqual(outside.read_text(), 'keep')
        self.assertGreaterEqual(total, 20)
        print(f'Rollback fault matrix: {total}/{total} command boundaries leave no managed payload or registrations')
        self.assertFalse(apps.exists())


if __name__ == '__main__':unittest.main()
