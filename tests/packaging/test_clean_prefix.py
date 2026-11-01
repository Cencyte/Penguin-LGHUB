import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import prepare_clean_prefix as m


class CleanPrefixTests(unittest.TestCase):
    def test_failed_stage_reports_log_without_exposing_forwarded_environment(self):
        import subprocess
        import traceback
        for failure in (subprocess.CalledProcessError(1, ['env', 'TOKEN=private-fixture']),
                        subprocess.TimeoutExpired(['env', 'TOKEN=private-fixture'], 180)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as tmp, \
                 patch.object(m, 'sandbox', return_value=['env', 'TOKEN=private-fixture']), \
                 patch.object(m.subprocess, 'run', side_effect=failure):
                try:
                    m.run_logged(Path(tmp), 'activation', ['wine', 'fixture'], {})
                except RuntimeError:
                    message = traceback.format_exc()
                else:
                    self.fail('Failed stage did not raise')
                self.assertIn('activation.log', message)
                self.assertNotIn('private-fixture', message)

    def test_metadata_has_no_donor_input_and_uses_windows_paths(self):
        lock = {'application_version': 'fixture', 'build_id': '123',
                'public_access': {'access_group': 'public'}, 'depots': {'core': {
                    'url': 'https://updates.ghub.logitechg.com/fixture', 'sha256': 'a' * 64,
                    'size': 10, 'expected_files': ['manifest.json'], 'depends_on': [], 'required': True}}}
        result = m.metadata(lock, {'extracted': {'core': []}}, {'keys': [], 'lastModified': 1})
        current = result['current.json']
        self.assertEqual(current['depots'][0]['localFolder'], r'C:\ProgramData\LGHUB\depots\123\core')
        self.assertNotIn('signatures', current['depots'][0])
        self.assertEqual(result['groups.json']['buildGroups'], {'123': 'public'})
        self.assertFalse(result['periodic_check.json']['checkPeriodically'])
        self.assertFalse(result['periodic_check.json']['downloadAutomatically'])
        self.assertNotIn('next.json', result)
        self.assertNotIn(str(Path.home()), json.dumps(result))

    def test_artifact_allowlist_excludes_vendor_and_personal_files(self):
        baseline = json.loads(m.BASELINE.read_text())
        files = m.compatibility_files(baseline)
        for name in files:
            self.assertTrue(name.startswith('builds/') or
                            name.startswith('prefix/' + m.APP + '/'))
            self.assertNotIn('system.reg', name)
            self.assertNotIn('d2d1.dll', name)
            self.assertNotIn('ninput.dll', name)
            self.assertNotIn('lghub_agent.exe', name)
            self.assertNotIn('Fonts', name)
        self.assertEqual(len([p for p in files if not p.startswith('builds/')]), 7)

    def test_all_observed_declarations_have_explicit_disposition(self):
        kinds = json.loads((m.ROOT / 'packaging/logitech-depots-verification.json').read_text())['pending_installer_declaration_groups']
        for kind in kinds:
            d = m.disposition({'kind': kind, 'depot': 'fixture'})
            self.assertIn('reason', d)
            if kind in ('extensions', 'registerLibrary', 'conditional_or_external_file'):
                self.assertEqual(d['status'], 'deferred')
        with self.assertRaisesRegex(ValueError, 'Unclassified'):
            m.disposition({'kind': 'new_vendor_behavior'})

    def test_environment_discards_live_connections_and_module_overrides(self):
        inherited = {'PATH': '/usr/bin', 'WINEPREFIX': '/live', 'WINESERVER': '/live/server',
                     'WINEDLLPATH': '/live/modules', 'LD_PRELOAD': '/inject', 'GHUB_TOKEN': 'secret',
                     'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0', 'XAUTHORITY': '/auth',
                     'DBUS_SESSION_BUS_ADDRESS': '/bus', 'PULSE_SERVER': '/live-audio'}
        with patch.dict(os.environ, inherited, clear=True):
            env = m.environment(Path('/new/prefix'))
        self.assertEqual(env['WINEPREFIX'], '/new/prefix')
        self.assertEqual(env['DISPLAY'], '')
        for name in ('WINESERVER','WINEDLLPATH','LD_PRELOAD','GHUB_TOKEN','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS','PULSE_SERVER'):
            self.assertNotIn(name, env)
        self.assertIn('lghub_software_manager.exe=d', env['WINEDLLOVERRIDES'])

    def test_sandbox_only_writes_candidate_and_isolates_network_and_devices(self):
        with patch.object(m.Path, 'is_dir', return_value=True):
            args = m.sandbox(Path('/private/new candidate'), ['wine', 'reg', 'query', r'HKLM\Software'])
        self.assertIn('--unshare-net', args)
        self.assertIn('--unshare-pid', args)
        self.assertEqual(args[args.index('--ro-bind')+1:args.index('--ro-bind')+3], ['/', '/'])
        self.assertEqual(args[args.index('--bind')+1:args.index('--bind')+3], ['/private/new candidate'] * 2)
        self.assertEqual(args[args.index('--dev')+1], '/dev')
        self.assertIn(str(Path('/run/user') / str(os.getuid())), args)
        self.assertEqual(args[-4:], ['wine','reg','query',r'HKLM\Software'])

    def test_registry_has_correct_activation_threading_and_scoped_freeze(self):
        b = json.loads(m.BASELINE.read_text())
        registry = m.registration(b, 'fixture')
        self.assertEqual(registry.count('"DllPath"='), 8)
        self.assertEqual(registry.count('"ThreadingModel"=dword:00000003'), 7)
        self.assertIn('"lghub_software_manager.exe"=""', registry)
        self.assertNotIn('WindowsUpdate', registry)
        self.assertNotIn('Services', registry)  # Registered through SCM, not forged registry state.
        self.assertIn('--disable-gpu', registry)

    def test_copy_integrity_refuses_changed_sources_and_existing_targets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'space_check'):
            root = Path(tmp); src = root/'source'; dst = root/'target'
            src.write_bytes(b'expected'); sha = m.digest_file(src)['sha256']
            m.copy_checked(src, dst, sha)
            self.assertEqual(src.read_bytes(), dst.read_bytes())
            with self.assertRaises(FileExistsError):
                m.copy_checked(src, dst, sha)
            src.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                m.copy_checked(src, root/'new', sha)

    def test_cache_retains_verified_archives_and_rejects_corruption_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'space_check'):
            root = Path(tmp); cache = root / 'downloads'; cache.mkdir()
            archive = cache / 'core.depot'; archive.write_bytes(b'vendor archive')
            sha = m.digest_file(archive)['sha256']
            lock = {'depots': {'core': {'local_name': 'core.depot', 'sha256': sha,
                                        'size': archive.stat().st_size}}}
            report = {'extracted': {'core': []}}
            m.install_depot_cache(cache, root / 'installed', lock, report)
            self.assertEqual((root / 'installed/cache' / sha).read_bytes(), archive.read_bytes())
            archive.write_bytes(b'broken archive')
            with self.assertRaises(ValueError):
                m.install_depot_cache(cache, root / 'bad', lock, report)
            self.assertFalse((root / 'bad').exists())

    def test_existing_output_rejected_before_any_wine_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'new directory'):
                m.prepare(Path('/stage'), Path('/cache'), Path(tmp), Path('/artifacts'), Path('/dlls'))
            run.assert_not_called()

    def test_catalog_seeds_desktop_from_verified_payload_without_user_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'space_check'):
            prefix = Path(tmp)
            source = prefix / m.APP / 'data/applications.json'
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({'applications': [{
                'applicationId': '420fd454-0c36-499d-bde4-146823b16147',
                'name': 'APPLICATION_NAME_DESKTOP'}]}))
            report = {'layout': {'files': [{'destination': 'data/applications.json',
                                            'sha256': m.digest_file(source)['sha256']}]}}
            m.initialize_application_catalog(prefix, report)
            self.assertEqual((prefix / m.CATALOG).read_bytes(), source.read_bytes())
            self.assertFalse((prefix / 'drive_c/users').exists())
            source.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'catalog changed'):
                m.initialize_application_catalog(prefix, report)

    def test_receipt_checks_prefix_modules_and_extra_managed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('prefix/' + m.APP, 'prefix/' + m.DATA, 'frontend', 'builds'):
                (root / folder).mkdir(parents=True)
            files = {}
            for module in m.PREFIX_MODULES:
                name = f'prefix/drive_c/windows/system32/{module}.dll'
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'fixture module')
                files[name] = m.digest_file(target)['sha256']
            receipt = {'schema': 1, 'kind': 'clean-prefix-development-candidate', 'files': files,
                       'depot_files': 0, 'activation_factories_verified': 8}
            (root / 'clean-prefix.json').write_text(json.dumps(receipt))
            self.assertEqual(m.verify_prepared(root)['managed_files_verified'], 4)
            target.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Installed file changed'):
                m.verify_prepared(root)
            target.write_bytes(b'fixture module')
            extra = root / 'frontend/unexpected'
            extra.write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError, 'file set changed'):
                m.verify_prepared(root)
            extra.unlink()
            del receipt['files'][name]
            (root / 'clean-prefix.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'Missing prefix module receipt'):
                m.verify_prepared(root)


if __name__ == '__main__':
    unittest.main()
