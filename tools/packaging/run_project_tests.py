#!/usr/bin/env python3
"""Run source regression tests with the installer presentation and full logs."""
import argparse
import contextlib
import datetime
import os
from pathlib import Path
import sys
import traceback
import unittest

from installer_ui import Presentation, ROOT

HELP = '''USAGE
  python3 tools/packaging/run_project_tests.py --run [--verbose] [--log-file <path>]

SUMMARY
  Run packaging and device-policy regression tests with colored step headers.
  Keep complete test output in a private log and show failures in the terminal.
  Run from a source checkout; Python 3.12 or newer is required.

FLAGS
  --run                    Run both project test suites.
  --verbose                Show individual tests and captured output as they run.
  --log-file <path>         Create a new log at this path instead of the state directory.
  --help                   Show this help and exit.

EXAMPLES
  python3 tools/packaging/run_project_tests.py --run
  python3 tools/packaging/run_project_tests.py --run --verbose
  python3 tools/packaging/run_project_tests.py --run --log-file /tmp/penguin-tests.log
'''

# Use the installer's existing palette; discovery still includes newly added
# test modules even before a friendly label is assigned here.
STEPS = {
    'test_clean_prefix': ('Wine', 'Test clean-prefix configuration and integrity'),
    'test_desktop_runtime': ('Desktop', 'Test launcher and container configuration'),
    'test_desktop_shutdown': ('Desktop', 'Test graceful application shutdown'),
    'test_desktop_startup': ('Desktop', 'Test application startup and readiness'),
    'test_dockur_acquire': ('Windows', 'Test acquisition configuration and boundaries'),
    'test_frontend_startup': ('G HUB', 'Test frontend resource transformations'),
    'test_install_transaction': ('System', 'Test installation commit, rollback and recovery'),
    'test_installer': ('Verify', 'Test installer steps, logging and retry behavior'),
    'test_linux_runtime': ('Tools', 'Test private Linux libraries and archive handling'),
    'test_linux_sdk': ('Tools', 'Test private SDK preparation'),
    'test_logitech_depots': ('Downloads', 'Test depot integrity, extraction and encryption'),
    'test_server_sync': ('Wine', 'Test per-application synchronization settings'),
    'test_tray_registration': ('Desktop', 'Test tray registration handling'),
    'test_windows_media_acquire': ('Windows', 'Test official-media acquisition rules'),
    'test_windows_media_reconstruct': ('Windows', 'Test Windows-resource reconstruction rules'),
    'test_wine_runtime': ('Wine', 'Test runtime inventories and integrity checks'),
    'test_wine_x11_restore': ('Desktop', 'Test window restore patch coverage'),
    'test_desktop_device_policy': ('System', 'Test scoped desktop device-access policy'),
    'test_uninstall': ('System', 'Test installation removal and distribution policy'),
    'test_check_presentation': ('Verify', 'Test readiness progress and test-result reporting'),
}


def discover():
    suite = unittest.TestSuite()
    for directory, pattern in ((ROOT / 'tests/packaging', 'test_*.py'),
                               (ROOT / 'tools/wine', 'test_desktop_device_policy.py')):
        # Each directory has its own discovery root and helper imports.
        suite.addTests(unittest.TestLoader().discover(str(directory), pattern=pattern))
    return suite


class LoggedOutput:
    def __init__(self, presentation):
        self.presentation = presentation

    def write(self, text):
        self.presentation.output(text)
        return len(text)

    def flush(self):
        self.presentation.log.flush()
        if self.presentation.verbose:
            self.presentation.stream.flush()


class StepResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity, presentation):
        super().__init__(stream, descriptions, verbosity)
        self.presentation = presentation
        self.last_module = None

    def startTest(self, test):
        module = test.__class__.__module__.rsplit('.', 1)[-1]
        if module != self.last_module:
            category, description = STEPS.get(module, ('Verify', 'Run ' + module))
            self.presentation.step(category, description)
            self.last_module = module
        super().startTest(test)


def run_suite(suite, ui):
    if not suite.countTestCases():
        raise RuntimeError('No tests discovered; check that this is a complete source checkout.')
    output = LoggedOutput(ui)
    runner = unittest.TextTestRunner(
        stream=output, verbosity=2,
        resultclass=lambda *args: StepResult(*args, presentation=ui))
    # Test diagnostics, including stdout/stderr, always reach the log. Headers
    # use the original stream retained by Presentation, so remain visible.
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = runner.run(suite)
    if not ui.verbose:
        for kind, failures in (('FAIL', result.failures), ('ERROR', result.errors)):
            for test, detail in failures:
                ui.write(f'{kind}: {test.id()}\n{detail}')
        for test in result.unexpectedSuccesses:
            ui.write('UNEXPECTED SUCCESS: ' + test.id())
    status = 'OK' if result.wasSuccessful() else 'FAILED'
    summary = (f'{status}: {result.testsRun} tests; {len(result.failures)} failures, '
               f'{len(result.errors)} errors, {len(result.skipped)} skipped, '
               f'{len(result.expectedFailures)} expected failures, '
               f'{len(result.unexpectedSuccesses)} unexpected successes.')
    ui.write(summary)
    ui.log.write(summary + '\n');ui.log.flush()
    return 0 if result.wasSuccessful() else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(HELP.split('\nSUMMARY')[0]);return 0
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--log-file', type=Path)
    parser.add_argument('--help', action='store_true')
    args = parser.parse_args(argv)
    if args.help:
        print(HELP);return 0
    if not args.run:
        parser.error('Choose --run')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + str(os.getpid())
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'penguin-lghub'
    log = args.log_file or state / ('project-tests-' + stamp + '.log')
    try:
        ui = Presentation(log, args.verbose)
    except OSError as error:
        print('Cannot create test log: ' + str(error), file=sys.stderr);return 1
    try:
        ui.header();ui.step('Verify', 'Discover packaging and device-policy tests')
        output = LoggedOutput(ui)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            suite = discover()
        return run_suite(suite, ui)
    except KeyboardInterrupt:
        ui.write('Tests interrupted. Log: ' + str(ui.log_path));return 130
    except Exception as error:
        traceback.print_exc(file=ui.log);ui.log.flush()
        ui.write('Tests stopped: ' + str(error));ui.write('Details: ' + str(ui.log_path));return 1
    finally:
        ui.close()


if __name__ == '__main__':
    raise SystemExit(main())
