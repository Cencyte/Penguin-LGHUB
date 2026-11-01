import json
import contextlib
import io
import itertools
import os
from pathlib import Path
import sys
import tempfile
import unittest
import socket
import shlex
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import desktop_runtime as m
import linux_probe
import wayland_session
from windows_media_acquire import digest_file


class DesktopRuntimeTests(unittest.TestCase):
    def test_staging_frontend_preserves_vendor_archive_and_user_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); root = base / 'installed'; root.mkdir()
            candidate = base / 'candidate'; candidate.mkdir()
            receipt = candidate / 'clean-prefix.json'
            receipt.write_text(json.dumps({'color_archive': 'source-hash'}))
            app = root / 'prefix' / m.APP / 'resources/app.asar'
            app.parent.mkdir(parents=True); app.write_bytes(b'original archive')
            settings = root / 'user-settings'; settings.write_bytes(b'user preferences')
            config_path = root / 'desktop.json'
            config_path.write_text(json.dumps({'candidate_manifest_sha256': digest_file(receipt)['sha256'],
                                              'fixed_files': {}}))
            original_config = config_path.read_bytes()
            def prepare(source, output, expected):
                self.assertEqual(expected, 'source-hash')
                output.write_bytes(b'new verified frontend')
                return {'output_sha256': digest_file(output)['sha256']}
            with patch.object(m, 'verify'), patch.object(m, 'verify_prepared'), \
                    patch.object(m, 'prepare_frontend', side_effect=prepare):
                result = m.stage_frontend(candidate, config_path)
            self.assertEqual(app.read_bytes(), b'original archive')
            self.assertEqual(settings.read_bytes(), b'user preferences')
            self.assertEqual(Path(result['previous_config']).read_bytes(), original_config)
            config = json.loads(config_path.read_text())
            self.assertEqual(m.frontend_override(config, root).read_bytes(), b'new verified frontend')

    def test_frontend_overlay_is_verified_and_mounted_after_frozen_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'frontend').mkdir()
            payload = b'verified patched frontend'
            import hashlib
            digest = hashlib.sha256(payload).hexdigest()
            name = 'frontend/' + digest + '.asar'
            (root / name).write_bytes(payload)
            config = {'linux_runtime': '/linux', 'wine': '/wine', 'test_tools': '/tools',
                      'frontend_override': name, 'fixed_files': {name: digest}}
            command = m.container_command(config, root, ['true'])
            at = command.index(str(root / name))
            self.assertEqual(command[at-1], '--ro-bind')
            self.assertEqual(command[at+1], str(root / 'prefix' / m.APP / 'resources/app.asar'))
            self.assertGreater(at, command.index(str(root / 'prefix' / m.DATA)))
            (root / name).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'overlay changed'):
                m.container_command(config, root, ['true'])
            config['frontend_override'] = '../outside.asar'
            with self.assertRaisesRegex(ValueError, 'Invalid verified'):
                m.container_command(config, root, ['true'])

    def test_missing_behavior_gate_or_lab_device_exclusion_is_not_hash_parity(self):
        baseline = json.loads(m.BASELINE.read_text())['environment']
        env = dict(baseline, WINEDLLOVERRIDES=baseline['WINEDLLOVERRIDES'] + ';lghub_software_manager.exe=d')
        config = {'environment': env, 'freeze_updates': True}
        m.verify_environment(config)
        env['WINEDEBUG'] = '-all,err+all'
        m.verify_environment(config)
        del env['WINE_GHUB_CHILD_COMPOSITION']
        with self.assertRaisesRegex(ValueError, 'WINE_GHUB_CHILD_COMPOSITION'):
            m.verify_environment(config)
        env['WINE_GHUB_CHILD_COMPOSITION'] = '1'
        env['WINEDLLOVERRIDES'] += ';winebus.sys=d'
        with self.assertRaisesRegex(ValueError, 'policy'):
            m.verify_environment(config)

    def test_wayland_with_xwayland_is_not_mistaken_for_x11(self):
        self.assertTrue(m.session_errors({'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}))
        self.assertFalse(m.session_errors({'XDG_SESSION_TYPE': 'x11', 'DISPLAY': ':12.0'}))
        self.assertTrue(m.session_errors({'XDG_SESSION_TYPE': 'x11', 'DISPLAY': 'localhost:10.0'}))

    def test_explicit_wayland_mode_requires_matching_local_plasma(self):
        env = {'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':1',
               'WAYLAND_DISPLAY': 'wayland-0', 'XDG_CURRENT_DESKTOP': 'KDE'}
        self.assertTrue(m.session_errors(env))
        self.assertFalse(m.session_errors(env, True))
        for update in ({'DISPLAY': 'localhost:10.0'}, {'XDG_CURRENT_DESKTOP': 'GNOME'},
                       {'WAYLAND_DISPLAY': ''}, {'XDG_SESSION_TYPE': 'x11'}):
            self.assertTrue(m.session_errors(dict(env, **update), True))

    def test_worker_activates_restore_corrections_in_both_desktop_sessions(self):
        """Exercise the actual worker boundary, not just a flag dictionary."""
        build = json.loads(m.linux.LOCK.read_text())['build_id']
        for wayland, implementation in itertools.product((False, True), ('integrated', 'overlay', 'none')):
            with self.subTest(wayland=wayland, implementation=implementation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); (root / 'logs').mkdir()
                config = {'wine': '/wine', 'linux_runtime': '/linux', 'test_tools': '/tools', 'environment': {}}
                if implementation == 'integrated':
                    config['integrated_wine_fixes'] = ['desktop-init', 'x11-restore']
                elif implementation == 'overlay':
                    config['x11_restore_override'] = {'test': True}
                def read(path, *args, **kwargs):
                    return json.dumps({'build_id': build}) if path == m.linux.LOCK else f'BUILD_ID="{build}"'
                observed = {}
                def session(*args):
                    observed.update(os.environ)
                    return 0
                with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-private'}, clear=True), \
                        patch.object(Path, 'read_text', read), patch.object(m, 'verify_server_sync'), \
                        patch.object(m, 'runtime_environment', return_value={'WAYLAND_DISPLAY': 'wayland-private'}), \
                        patch.object(m.linux, 'library_path', return_value='/libs'), \
                        patch.object(m, 'configure_fonts'), patch.object(m.subprocess, 'run') as run, \
                        patch.object(m.desktop, 'session', side_effect=session):
                    self.assertEqual(m.worker(config, root, root / 'session.json', wayland), 0)
                for name in ('PENGUIN_GHUB_REPAINT_ON_RESTORE', 'PENGUIN_GHUB_FIT_WORKAREA'):
                    self.assertEqual(observed[name], '1')
                self.assertEqual(observed.get('WINE_GHUB_RESTORE_STATE'), '1' if implementation != 'none' else None)
                self.assertEqual('WAYLAND_DISPLAY' not in observed, wayland)
                self.assertEqual(observed.get('PENGUIN_GHUB_GRACEFUL_CLOSE'), '1' if wayland else None)
                self.assertEqual(run.call_count, int(wayland))

    def test_automatic_selection_requires_a_known_login_session(self):
        for session in ('', 'tty', 'unspecified'):
            with self.subTest(session=session):
                env = {'XDG_SESSION_TYPE': session, 'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'}
                self.assertTrue(m.session_report(env)['errors'])
                with self.assertRaisesRegex(ValueError, 'Cannot determine'):
                    m.resolve_wayland(env)
        self.assertFalse(m.resolve_wayland({'XDG_SESSION_TYPE': 'x11', 'WAYLAND_DISPLAY': 'stale'}))
        self.assertTrue(m.resolve_wayland({'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}))

    def test_automatic_wayland_keeps_capability_checks_and_never_falls_back(self):
        env = {'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':1',
               'WAYLAND_DISPLAY': 'wayland-0', 'XDG_CURRENT_DESKTOP': 'KDE'}
        with patch.object(wayland_session, 'inspect', return_value={'errors': ['portal missing']}) as inspect:
            self.assertEqual(m.session_report(env)['errors'], ['portal missing'])
            inspect.assert_called_once()
        with patch.object(wayland_session, 'inspect') as inspect:
            self.assertTrue(m.session_report(dict(env, XDG_CURRENT_DESKTOP='GNOME'))['errors'])
            inspect.assert_not_called()

    def test_wayland_socket_must_be_real_and_inside_owned_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); runtime = root / 'run'; runtime.mkdir()
            env = {'XDG_RUNTIME_DIR': str(runtime), 'WAYLAND_DISPLAY': 'wayland-0'}
            self.assertIsNotNone(wayland_session.socket_error(env))
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(runtime / 'wayland-0'))
                self.assertIsNone(wayland_session.socket_error(env))
            (runtime / 'ordinary').write_text('not a socket')
            self.assertIsNotNone(wayland_session.socket_error(dict(env, WAYLAND_DISPLAY='ordinary')))
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(root / 'outside'))
                (runtime / 'escape').symlink_to(root / 'outside')
                self.assertIsNotNone(wayland_session.socket_error(dict(env, WAYLAND_DISPLAY='escape')))

    def test_capabilities_do_not_claim_permission_or_accept_missing_xtest(self):
        def respond(args, env):
            if args[0] == 'xdpyinfo':
                return 0, '    XWAYLAND (opcode: 99)\n    XTEST (opcode: 100)\n'
            if 'introspect' in args:
                return 0, 'interface org.freedesktop.portal.RemoteDesktop { ConnectToEIS(); };'
            return 0, ''
        with patch.object(wayland_session, 'socket_error', return_value=None), \
                patch.object(wayland_session.shutil, 'which', return_value='/usr/bin/tool'), \
                patch.object(wayland_session.xwayland_input, 'inspect', return_value={'errors': [], 'warnings': []}), \
                patch.object(wayland_session, 'command_result', side_effect=respond):
            report = wayland_session.inspect({'PATH': '/usr/bin'})
            self.assertFalse(report['errors'])
            self.assertEqual(report['permission_granted'], 'not measured')
            self.assertFalse(report['end_to_end_qualified'])
        with patch.object(wayland_session, 'socket_error', return_value=None), \
                patch.object(wayland_session.shutil, 'which', return_value='/usr/bin/tool'), \
                patch.object(wayland_session, 'command_result', return_value=(0, '    XWAYLAND\n')):
            self.assertTrue(wayland_session.inspect({'PATH': '/usr/bin'})['errors'])

    def test_wayland_environment_keeps_session_but_discards_injection(self):
        source = {'DISPLAY': ':3', 'WAYLAND_DISPLAY': 'wayland-0',
                  'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE',
                  'XAUTHORITY': '/run/user/1000/xauth', 'LD_PRELOAD': '/bad',
                  'WINEPREFIX': '/old', 'LIBEI_SOCKET': '/private-compositor-fd'}
        env = m.session_environment(source, True)
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XDG_CURRENT_DESKTOP', 'XAUTHORITY'):
            self.assertEqual(env[key], source[key])
        for key in ('LD_PRELOAD', 'WINEPREFIX', 'LIBEI_SOCKET'):
            self.assertNotIn(key, env)

    def test_session_environment_drops_loader_injection_and_retains_cookie(self):
        source = {'DISPLAY': ':4', 'XAUTHORITY': '/run/user/1000/xauth',
                  'XDG_SESSION_TYPE': 'x11', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus',
                  'LD_PRELOAD': '/evil', 'WINESERVER': '/host', 'WINEPREFIX': '/old',
                  'PRESSURE_VESSEL_RUNTIME': '/other', 'WAYLAND_DISPLAY': 'wayland-0'}
        with patch.dict(os.environ, source):
            env = m.session_environment(source)
        for name in ('DISPLAY', 'XAUTHORITY', 'XDG_SESSION_TYPE', 'DBUS_SESSION_BUS_ADDRESS'):
            self.assertEqual(env[name], source[name])
        for name in ('LD_PRELOAD', 'WINESERVER', 'WINEPREFIX', 'PRESSURE_VESSEL_RUNTIME', 'WAYLAND_DISPLAY'):
            self.assertNotIn(name, env)

    def test_container_shares_supervisor_identity_and_freezes_app_after_output(self):
        config = {'linux_runtime': '/private linux', 'wine': '/private wine', 'test_tools': '/tools'}
        cmd = m.container_command(config, Path('/session'), ['true'])
        self.assertIn('--share-pid', cmd)
        self.assertNotIn('--unshare-pid', cmd)
        self.assertIn('--graphics-provider=/', cmd)
        self.assertIn('--filesystem=/private linux:ro', cmd)
        self.assertIn('--dev-bind', cmd)
        self.assertNotIn('--graphics-provider=', cmd)
        self.assertLess(cmd.index('--filesystem=/session:rw'), cmd.index('--ro-bind'))
        self.assertIn('/session/prefix/drive_c/Program Files/LGHUB', cmd)
        self.assertIn('/session/prefix/drive_c/ProgramData/LGHUB', cmd)
        self.assertNotIn('sudo', cmd)

    def test_old_application_only_inventory_cannot_certify_depots(self):
        config = {'fixed_files': {'prefix/' + m.APP + '/lghub.exe': 'hash'}}
        with self.assertRaisesRegex(ValueError, 'verified frozen depot state'):
            m.verify_installation_inventory(config)

    def test_missing_depot_and_reset_manifest_fail_before_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = 'prefix/' + m.DATA + '/'
            payloads = {data + name: b'{}' for name in
                ('current.json', 'version.json', 'installation.json', 'groups.json', 'keys.json', 'periodic_check.json')}
            payloads[data + 'depots/869589/core/manifest.json'] = b'verified depot'
            payloads[data + 'cache/depot-digest'] = b'verified archive'
            config = {'schema': 1, 'kind': 'penguin-desktop-runtime',
                      'installation_state': 'frozen-depots-v1', 'fixed_files': {},
                      'linux_runtime': '/linux', 'test_tools': '/tools', 'wine': '/wine'}
            for name, value in payloads.items():
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(value)
                config['fixed_files'][name] = digest_file(p)['sha256']
            with patch.object(m, 'verify_environment'), patch.object(m.linux, 'verify'), \
                    patch.object(m.linux_probe, 'verify'), patch.object(m.linux_probe, 'matching_runtime'):
                m.verify(config, root)
                depot = root / (data + 'depots/869589/core/manifest.json')
                depot.unlink()
                with self.assertRaisesRegex(ValueError, 'Desktop component changed: .*depots/'):
                    m.verify(config, root)
                depot.write_bytes(b'verified depot')
                (root / (data + 'current.json')).write_text('{"depots":[]}')
                with self.assertRaisesRegex(ValueError, 'Desktop component changed: .*current.json'):
                    m.verify(config, root)

    def test_headless_overlay_preserves_synthetic_device_nodes(self):
        cmd = linux_probe.test_command(Path('/linux'), Path('/tools'), ['true'])
        i = cmd.index('--dev-bind')
        self.assertEqual(cmd[i + 1:i + 3], ['/dev', '/dev'])

    def test_launcher_keeps_paths_with_spaces_and_desktop_field_codes_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'launcher.desktop'
            root = Path(tmp) / 'test space'; root.mkdir()
            m.install_entry(root / '%config.json', dest)
            text = dest.read_text()
            self.assertIn('\nName=Penguin-LGHUB\n', text)
            self.assertNotIn('test','\n'.join(line for line in text.splitlines() if line.startswith(('Name=','Comment='))).lower())
            self.assertIn('/test space/%%config.json" --session auto launch', text)
            self.assertIn('Icon=' + str(root / 'penguin-lghub.png'), text)
            self.assertEqual((root / 'penguin-lghub.png').read_bytes(), m.ICON.read_bytes())
            self.assertIn('[Desktop Action Stop]', text)
            self.assertIn('StartupNotify=true\nStartupWMClass=lghub.exe\n', text)
            with self.assertRaises(FileExistsError):
                m.install_entry(Path('/elsewhere'), dest)

    def test_explicit_wayland_entry_pins_launch_and_check_and_keeps_quit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); entry = root / 'Wayland test.desktop'
            m.install_entry(root / 'desktop.json', entry, True)
            text = entry.read_text()
            self.assertIn('Name=Penguin-LGHUB\n', text)
            self.assertNotIn('Wayland test',text)
            self.assertIn('Name=Open logs\n',text)
            for action in ('launch', 'check --dialog', 'stop', 'help', 'logs'):
                self.assertIn('--session wayland ' + action, text)
            self.assertEqual((root / 'penguin-lghub.png').read_bytes(), m.ICON.read_bytes())

    def test_same_entry_switches_x11_wayland_and_back_with_one_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'desktop';root.mkdir();(root / 'logs').mkdir()
            config_path = root / 'desktop.json'
            config_path.write_text(json.dumps({'linux_runtime': str(root / 'linux')}))
            settings = root / 'settings';settings.write_bytes(b'keep my settings')
            entry = root / 'Penguin-LGHUB.desktop'
            m.install_entry(config_path, entry)
            saved_entry = entry.read_bytes();saved_config = config_path.read_bytes()
            command = shlex.split(next(line[5:] for line in entry.read_text().splitlines() if line.startswith('Exec=')))
            for action in ('launch', 'check --dialog', 'help', 'logs', 'stop'):
                self.assertIn('--session auto ' + action, entry.read_text())
            calls = []
            def run(config_path, config, output, worker, stream, feedback, wayland):
                calls.append((output, worker, wayland, m.session_environment(os.environ, wayland)))
                return 0
            clock = itertools.count()
            with patch.object(m, 'verify'), patch.object(m.startup_feedback, 'Feedback'), \
                 patch.object(m.desktop, 'reopen_or_lock', return_value=False), \
                 patch.object(m.desktop, 'prefix_processes', return_value=[]), \
                 patch.object(m.desktop, 'freeze_updates'), \
                 patch.object(m.socket, 'socket') as sock, \
                 patch.object(m, 'work_lock', side_effect=lambda *a, **kw: contextlib.nullcontext()), \
                 patch.object(m.time, 'strftime', side_effect=lambda fmt: 'launch-'+str(next(clock))), \
                 patch.object(m, 'run_container_session', side_effect=run), \
                 patch.object(wayland_session, 'inspect', return_value={'mode':'experimental-kde-xwayland', 'errors':[]}), \
                 patch.object(m.os, 'geteuid', return_value=1000):
                sock.return_value.__enter__.return_value.connect_ex.return_value = 1
                for session in ('x11', 'wayland', 'x11'):
                    env = {'XDG_SESSION_TYPE': session, 'DISPLAY': ':1', 'WAYLAND_DISPLAY': 'wayland-0',
                           'XDG_CURRENT_DESKTOP': 'KDE'}
                    with self.subTest(session=session), patch.dict(os.environ, env, clear=True), \
                         patch.object(sys, 'argv', command[1:]):
                        self.assertEqual(m.main(), 0)
                        output, worker, wayland, cleaned = calls[-1]
                        self.assertEqual(output, root)
                        self.assertEqual(wayland, session == 'wayland')
                        self.assertEqual(worker[worker.index('--session')+1], session)
                        self.assertEqual('WAYLAND_DISPLAY' in cleaned, session == 'wayland')
                        # Check setup uses the same automatic route as launch.
                        check_command = command[1:-1] + ['check']
                        with patch.object(sys, 'argv', check_command), contextlib.redirect_stdout(io.StringIO()) as report:
                            self.assertEqual(m.main(), 0)
                        expected = 'experimental-kde-xwayland' if wayland else 'x11'
                        self.assertEqual(json.loads(report.getvalue())['session']['mode'], expected)
                        # The container receives an explicit, already-resolved mode.
                        with patch.object(sys, 'argv', worker[1:]), patch.object(m, 'worker', return_value=0) as child:
                            self.assertEqual(m.main(), 0)
                            self.assertEqual(child.call_args.args[-1], wayland)
                        self.assertEqual(entry.read_bytes(), saved_entry)
                        self.assertEqual(config_path.read_bytes(), saved_config)
                        self.assertEqual(settings.read_bytes(), b'keep my settings')
            self.assertEqual([call[2] for call in calls], [False, True, False])

    def test_nonlaunch_actions_do_not_require_an_active_display_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'desktop.json';config.write_text('{}')
            with patch.dict(os.environ, {}, clear=True), patch.object(m.os, 'geteuid', return_value=1000), \
                 patch.object(m.desktop, 'stop') as stop, patch.object(m.subprocess, 'run') as run, \
                 patch.object(m, 'show_message'), patch.object(m, 'resolve_wayland', side_effect=AssertionError('must not probe')):
                for action in ('stop', 'logs', 'help'):
                    with patch.object(sys, 'argv', ['desktop_runtime.py', '--config', str(config), action]), \
                         contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(m.main(), 0)
                stop.assert_called_once();run.assert_called_once_with(['xdg-open', str(config.parent / 'logs')], check=True)

    def test_legacy_wayland_flag_remains_compatible_and_overrides_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'desktop.json';config.write_text('{}')
            with patch.object(sys, 'argv', ['desktop_runtime.py', '--config', str(config), '--experimental-wayland', 'launch']), \
                 patch.object(m.os, 'geteuid', return_value=1000), patch.object(m, 'launch', return_value=0) as launch:
                self.assertEqual(m.main(), 0)
                self.assertIs(launch.call_args.args[-1], True)

    def test_registration_makes_shortcut_discoverable_without_overwriting_other_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); entry = root / 'Wayland test.desktop'; apps = root / 'applications'
            m.install_entry(root / 'desktop.json', entry, True, apps)
            self.assertEqual(entry.read_bytes(), (apps / entry.name).read_bytes())
            m.register_entry(entry, apps)
            (apps / entry.name).write_text('another application')
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                m.register_entry(entry, apps)
            self.assertEqual((apps / entry.name).read_text(), 'another application')

    def test_prepared_frontend_fix_is_activated_and_verified_after_relocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); candidate = root / 'candidate'; output = root / 'desktop'
            source = candidate / 'frontend/color-preview.asar'
            target = output / 'prefix' / m.APP / 'resources/app.asar'
            source.parent.mkdir(parents=True); target.parent.mkdir(parents=True)
            source.write_bytes(b'patched picker'); target.write_bytes(b'vendor original')
            sha = digest_file(source)['sha256']; fixed = {}
            def prepare(src, dst, expected):
                self.assertEqual(src, source)
                self.assertEqual(expected, sha)
                dst.write_bytes(src.read_bytes() + b' plus font initialization')
                return {'output_sha256': digest_file(dst)['sha256']}
            with patch.object(m, 'prepare_frontend', side_effect=prepare):
                m.activate_frontend(candidate, output, {'color_archive': sha}, fixed)
            self.assertEqual(target.read_bytes(), b'patched picker plus font initialization')
            self.assertEqual(fixed[str(target.relative_to(output))], digest_file(target)['sha256'])
            self.assertEqual(source.read_bytes(), b'patched picker')
            with self.assertRaisesRegex(ValueError, 'archive changed'):
                m.activate_frontend(candidate, output, {'color_archive': '0'*64}, fixed)
            self.assertEqual(target.read_bytes(), b'patched picker plus font initialization')

    def test_fixed_file_escape_refused_before_runtime_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'session'; root.mkdir()
            outside = Path(tmp) / 'outside'; outside.write_text('payload')
            (root / 'file').symlink_to(outside)
            config = {'schema': 1, 'kind': 'penguin-desktop-runtime',
                      'fixed_files': {'file': digest_file(outside)['sha256']}}
            with self.assertRaisesRegex(ValueError, 'component changed'):
                m.verify(config, root)

    def test_transferred_probe_requires_matching_source_and_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'probe.exe'; exe.write_bytes(b'fixture')
            receipt = {'source_sha256': digest_file(m.ROOT / 'tools/packaging/clean_prefix_probe.cpp')['sha256'],
                       'exe_sha256': digest_file(exe)['sha256']}
            exe.with_suffix('.json').write_text(json.dumps(receipt))
            linux_probe.verify_activation_probe(exe)
            exe.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                linux_probe.verify_activation_probe(exe)


if __name__ == '__main__':
    unittest.main()
