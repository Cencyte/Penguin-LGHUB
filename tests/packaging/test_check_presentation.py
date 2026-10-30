import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
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
                    def inventory(progress=None):
                        progress('System', 'Inspect fixture host')
                        progress('Verify', 'Check fixture namespace')
                        return report
                    with patch.dict(os.environ, {'XDG_STATE_HOME': tmp}), \
                         patch.object(install.os, 'geteuid', return_value=1000), \
                         patch.object(install.host_preflight, 'inventory', side_effect=inventory), \
                         patch.object(install, 'pipeline') as pipeline, contextlib.redirect_stdout(output):
                        code = install.main(['--check'] + (['--verbose'] if verbose else []))
                    self.assertEqual(code, int(blocked));pipeline.assert_not_called()
                    log = next(Path(tmp).rglob('*.log')).read_text()
                    for heading in ('[System] Inspect fixture host...', '[Verify] Check fixture namespace...'):
                        self.assertIn(heading, output.getvalue());self.assertIn(heading, log)
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


if __name__ == '__main__':
    unittest.main()
