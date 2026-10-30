import contextlib
import base64
import io
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import host_preflight
import install
import installer_ui
import run_project_tests
import wayland_session


class CheckPresentationTests(unittest.TestCase):
    def test_progress_preserves_host_findings_and_json_consumers(self):
        for session in ('x11', 'wayland'):
            with self.subTest(session=session), tempfile.TemporaryDirectory() as tmp:
                events = []
                env = {'XDG_SESSION_TYPE': session, 'XDG_CURRENT_DESKTOP': 'KDE',
                       'XDG_RUNTIME_DIR': tmp}
                def command(args):
                    return {'status': 1 if args[0] == 'bwrap' else 0, 'output': 'fixture'}
                with patch.object(host_preflight, 'os_identity', return_value={'ID': 'manjaro'}), \
                     patch.object(host_preflight.shutil, 'which', side_effect=lambda n: '/usr/bin/' + n), \
                     patch.object(host_preflight, 'capture', side_effect=command), \
                     patch.object(wayland_session, 'inspect', return_value={'errors': ['portal fixture']}):
                    plain = host_preflight.inventory(env, Path(tmp))
                    presented = host_preflight.inventory(env, Path(tmp), lambda *e: events.append(e))
                self.assertEqual(plain, presented)
                self.assertTrue(any('namespace test failed' in e for e in presented['errors']))
                self.assertEqual(any('portal fixture' == e for e in presented['errors']), session == 'wayland')
                self.assertEqual(len(events), 9 if session == 'wayland' else 8)
                self.assertEqual(events[-1], ('Verify', 'Test an isolated user namespace'))
                self.assertTrue(all(c in installer_ui.STYLE['category_colors'] for c, _ in events))

    def test_check_only_logs_progress_and_keeps_failure_exit_status(self):
        for verbose in (False, True):
            for blocked in (False, True):
                with self.subTest(verbose=verbose, blocked=blocked), tempfile.TemporaryDirectory() as tmp:
                    output = io.StringIO()
                    report = {'errors': ['blocked fixture'] if blocked else [], 'warnings': ['warning fixture'],
                              'diagnostic': 'logged host detail'}
                    def inventory(progress=None,result=None):
                        progress('System', 'Inspect fixture host')
                        result('System', 'Inspect fixture host', 'OK')
                        progress('Verify', 'Check fixture namespace')
                        result('Verify', 'Check fixture namespace', 'FAIL' if blocked else 'OK')
                        return report
                    with patch.dict(os.environ, {'XDG_STATE_HOME': tmp}), \
                         patch.object(install.os, 'geteuid', return_value=1000), \
                         patch.object(install.host_preflight, 'inventory', side_effect=inventory), \
                         patch.object(install, 'pipeline') as pipeline, contextlib.redirect_stdout(output):
                        code = install.main(['--check'] + (['--verbose'] if verbose else []))
                    self.assertEqual(code, int(blocked));pipeline.assert_not_called()
                    log = next(Path(tmp).rglob('*.log')).read_text()
                    for heading in ('[System] Inspect fixture host...', '[Verify] Check fixture namespace...'):
                        import re
                        compact=lambda text: re.sub(r' +', ' ', text).replace(' ...','...')
                        self.assertIn(heading, compact(output.getvalue()));self.assertIn(heading, compact(log))
                    self.assertIn('logged host detail', log)
                    self.assertEqual('logged host detail' in output.getvalue(), verbose)
                    if blocked:self.assertIn('blocked fixture', output.getvalue())
                    self.assertEqual(json.loads(next(Path(tmp).rglob('host-*.json')).read_text()), report)

    def test_runner_records_diagnostics_and_exposes_failures_in_quiet_mode(self):
        class Cases(unittest.TestCase):
            def test_pass(self):
                print('fixture stdout');print('fixture stderr', file=sys.stderr)
            def test_fail(self):self.fail('fixture assertion')
            def test_error(self):raise RuntimeError('fixture exception')
            @unittest.skip('fixture skip')
            def test_skip(self):pass
        for verbose in (False, True):
            with self.subTest(verbose=verbose), tempfile.TemporaryDirectory() as tmp:
                stream = io.StringIO();path = Path(tmp) / 'tests.log'
                ui = installer_ui.Presentation(path, verbose, stream)
                try:code = run_project_tests.run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(Cases), ui)
                finally:ui.close()
                text = stream.getvalue();log = path.read_text()
                self.assertEqual(code, 1)
                self.assertIn('[Verify]', text)
                self.assertIn('4 tests; 1 failures, 1 errors, 1 skipped', text)
                for fragment in ('fixture assertion', 'fixture exception', 'Traceback'):
                    self.assertIn(fragment, text);self.assertIn(fragment, log)
                for fragment in ('fixture stdout', 'fixture stderr'):
                    self.assertIn(fragment, log);self.assertEqual(fragment in text, verbose)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_runner_preserves_expected_failure_and_unexpected_success_semantics(self):
        class Expected(unittest.TestCase):
            @unittest.expectedFailure
            def test_expected(self):self.fail('expected')
        class Unexpected(unittest.TestCase):
            @unittest.expectedFailure
            def test_unexpected(self):pass
        for case, status, marker in ((Expected, 0, '1 expected failures'), (Unexpected, 1, 'UNEXPECTED SUCCESS')):
            with tempfile.TemporaryDirectory() as tmp:
                stream = io.StringIO();ui = installer_ui.Presentation(Path(tmp) / 'log', stream=stream)
                try:code = run_project_tests.run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(case), ui)
                finally:ui.close()
                self.assertEqual(code, status);self.assertIn(marker, stream.getvalue())

    def test_discovery_import_failure_cannot_be_reported_as_success(self):
        suite = unittest.TestLoader().loadTestsFromName('penguin_nonexistent_test_fixture')
        with tempfile.TemporaryDirectory() as tmp:
            stream = io.StringIO();ui = installer_ui.Presentation(Path(tmp) / 'log', stream=stream)
            try:code = run_project_tests.run_suite(suite, ui)
            finally:ui.close()
            self.assertEqual(code, 1)
            self.assertIn('ModuleNotFoundError', stream.getvalue())

    def test_help_has_no_discovery_or_log_side_effect(self):
        for args in ([], ['--help']):
            with patch.object(run_project_tests, 'discover') as discover, \
                 patch.object(run_project_tests, 'Presentation') as presentation, \
                 contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(run_project_tests.main(args), 0)
            discover.assert_not_called();presentation.assert_not_called()
            self.assertTrue(stream.getvalue().startswith('USAGE\n'))
            self.assertEqual('SUMMARY' in stream.getvalue(), bool(args))

    def test_empty_suite_fails_and_existing_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tests.log'
            with patch.object(run_project_tests, 'discover', return_value=unittest.TestSuite()), \
                 contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(run_project_tests.main(['--run', '--log-file', str(path)]), 1)
            self.assertIn('No tests discovered', stream.getvalue())
            data = path.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_project_tests.main(['--run', '--log-file', str(path)]), 1)
            self.assertEqual(path.read_bytes(), data)

    def test_headers_share_fixed_colors_and_plain_logs(self):
        class Terminal(io.StringIO):
            def isatty(self):return True
        for no_color in (False, True):
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
                os.environ.pop('NO_COLOR', None)
                if no_color:os.environ['NO_COLOR'] = '1'
                stream = Terminal();path = Path(tmp) / 'log'
                ui = installer_ui.Presentation(path, stream=stream)
                try:ui.step('Verify', 'Run fixture')
                finally:ui.close()
                self.assertEqual('\033[38;5;75m' in stream.getvalue(), not no_color)
                self.assertNotIn('\033', path.read_text())

    def test_columns_and_results_align_even_when_descriptions_wrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=io.StringIO();path=Path(tmp)/'log'
            ui=installer_ui.Presentation(path,stream=stream)
            ui.description_width=38
            ui.step('System','Check the OS','OK')
            ui.step('Downloads','Verify the archive with a description long enough to wrap','FAIL')
            ui.close()
            rows=stream.getvalue().splitlines()
            self.assertEqual(rows[0].index('Check'),rows[1].index('Verify'))
            complete=[row for row in rows if '...' in row]
            self.assertTrue(all(row[row.index('...')-1]!=' ' for row in complete))
            self.assertEqual(complete[0].index('OK'),complete[1].index('FAIL'))
            self.assertLessEqual(max(map(len,rows)),installer_ui.STYLE['category_colors'].__len__()+80)

    def test_spinner_preserves_verbose_lines_and_pauses_for_authorization(self):
        class Terminal(io.StringIO):
            def __init__(self):
                super().__init__();self.frame=threading.Event()
            def isatty(self):return True
            def write(self,text):
                result=super().write(text)
                if text in ['\r\033[2K'+c for c in '|/-\\']:self.frame.set()
                return result
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'TERM':'xterm-256color','NO_COLOR':'1'}):
            stream=Terminal();path=Path(tmp)/'log'
            ui=installer_ui.Presentation(path,True,stream)
            try:
                with self.assertRaisesRegex(RuntimeError,'fixture failure'),ui.activity():
                    ui.step('Wine','Build fixture')
                    self.assertTrue(stream.frame.wait(2),'quiet command did not animate')
                    self.assertIn('Build fixture...\n\r\033[2K',stream.getvalue())
                    with ui.suspend_activity():
                        stream.frame.clear();before=stream.getvalue()
                        self.assertFalse(stream.frame.wait(.3),'spinner overwrote authorization prompt')
                        self.assertEqual(stream.getvalue(),before)
                    ui.output('partial output')
                    stream.frame.clear()
                    self.assertFalse(stream.frame.wait(.3),'spinner overwrote an unfinished verbose line')
                    ui.output(' completed\n')
                    self.assertTrue(stream.frame.wait(2))
                    raise RuntimeError('fixture failure')
                self.assertIsNone(ui._activity_thread)
                self.assertTrue(stream.getvalue().endswith('\r\033[2K'))
            finally:ui.close()
            self.assertIn('partial output completed\n',path.read_text())
            self.assertNotIn('\033',path.read_text())

    def test_spinner_uses_plain_output_for_redirected_and_dumb_terminals(self):
        class Terminal(io.StringIO):
            def isatty(self):return True
        for stream,term in ((io.StringIO(),'xterm-256color'),(Terminal(),'dumb')):
            with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'TERM':term,'NO_COLOR':'1'}):
                ui=installer_ui.Presentation(Path(tmp)/'log',stream=stream)
                try:
                    with ui.activity():
                        ui.step('Wine','Build fixture')
                        self.assertIsNone(ui._activity_thread)
                finally:ui.close()
                self.assertNotIn('\033',stream.getvalue())

    def test_version_block_separates_all_three_versions_from_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=io.StringIO();ui=installer_ui.Presentation(Path(tmp)/'log',stream=stream)
            try:ui.header()
            finally:ui.close()
            rows=stream.getvalue().splitlines()
            self.assertEqual(rows[:3],[f"Penguin-LGHUB v{installer_ui.STYLE['project_version']}",
                f"Installer version v{installer_ui.STYLE['installer_version']}.",
                f'Logitech G HUB v{installer_ui.GHUB_VERSION}'])
            self.assertEqual(rows[3],'');self.assertTrue(rows[4].startswith('Log file: '))
            self.assertEqual(rows[5],'')

    def test_install_omits_check_result_labels_and_celebrates_only_success(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                stream=io.StringIO();args=install.parser().parse_args(['--install','--directory',str(Path(tmp)/'payload')])
                def inventory(progress=None,result=None):
                    progress('System','Inspect fixture host');result('System','Inspect fixture host','OK')
                    return {'errors':[],'warnings':[]}
                def execute(directory,source,version,ui,operation):
                    operation(directory,{})
                    if fail:raise RuntimeError('fixture commit failure')
                with patch.object(install.host_preflight,'inventory',side_effect=inventory), \
                     patch.object(install,'fingerprint',return_value='fixture'), \
                     patch.object(install,'_pipeline'), \
                     patch.object(install.install_transaction,'execute',side_effect=execute), \
                     contextlib.redirect_stdout(stream):
                    code=install.present_install(args,Path(tmp),'fixture')
                self.assertEqual(code,int(fail))
                self.assertIn('Inspect fixture host...',stream.getvalue())
                self.assertNotIn('OK',stream.getvalue())
                celebration='\n\n🎉 Installation complete! 🎉\n'
                self.assertEqual(celebration in stream.getvalue(),not fail)

    def test_status_records_the_actual_failing_check(self):
        events=[]
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(host_preflight,'os_identity',return_value={'ID':'bazzite'}), \
             patch.object(host_preflight.shutil,'which',side_effect=lambda n:'/bin/'+n), \
             patch.object(host_preflight,'capture',return_value={'status':1,'output':'fixture denial'}):
            report=host_preflight.inventory({'XDG_SESSION_TYPE':'x11'},Path(tmp),result=lambda *row:events.append(row))
        self.assertEqual(events[-1],('Verify','Test an isolated user namespace','FAIL'))
        self.assertTrue(all(row[-1]=='OK' for row in events[:-1]))
        self.assertEqual([row[-1] for row in events],[row['status'] for row in report['checks']])

    def test_konsole_uses_rgba_and_artwork_keeps_native_size_and_transparent_corners(self):
        self.assertEqual(installer_ui.logo_format({'KONSOLE_VERSION':'260801'}),'kitty')
        self.assertEqual(installer_ui.logo_format({'TERM':'foot'}),'sixels')
        self.assertEqual(installer_ui.logo_format({'KONSOLE_VERSION':'260801','TMUX':'yes'}),'symbols')
        data=(installer_ui.ROOT/'assets/terminal/kitty.ansi').read_bytes()
        header=data.split(b'\x1b\\',1)[0]
        self.assertIn(b'f=32,s=200,v=200',header)
        self.assertNotIn(b',c=',header);self.assertNotIn(b',r=',header)
        chunks=re.findall(rb'\x1b_G[^;\x1b]*;([^\x1b]*)\x1b\\',data)
        pixels=b''.join(base64.b64decode(chunk) for chunk in chunks)
        self.assertEqual(len(pixels),200*200*4)
        for index in (0,199,199*200,200*200-1):self.assertEqual(pixels[index*4+3],0)


if __name__ == '__main__':
    unittest.main()
