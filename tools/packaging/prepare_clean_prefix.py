#!/usr/bin/env python3
"""Create and validate a new G HUB prefix from verified acquisition outputs.

Use --runtime for a complete private Wine bundle; --compatibility-root retains
the older development path requiring host Wine 10.20. Both copy only allowlisted
components, never a donor prefix, registry, settings, fonts or vendor binaries.
The resulting candidate is not promoted to the user's Desktop automatically.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from windows_media_acquire import ROOT, check_asset, digest_file, external_path, space_check, work_lock
from logitech_depots import LOCK_PATH, lock_digest, relative_name, verify_stage, staged_files
from logitech_capsule import public_keys
from wine_runtime import verify_runtime, runtime_environment

sys.path.insert(0, str(ROOT / 'tools/wine'))
from prepare_color_picker_fix import prepare as prepare_color
from prepare_tray_background import patch_resources

BASELINE = ROOT / 'packaging/runtime-baseline.json'
APP = 'drive_c/Program Files/LGHUB'
DATA = 'drive_c/ProgramData/LGHUB'
CATALOG = 'drive_c/ProgramData/LGHUBData/applications/applications_db.json'
WIN_APP = r'C:\Program Files\LGHUB'
WIN_DATA = r'C:\ProgramData\LGHUB'
OWN_DLLS = ('api_information_proxy.dll', 'core_application_experiment.dll', 'mfsensorgroup.dll',
            'system_tray/ghub_dispatcher_queue.dll', 'system_tray/CoreMessaging.dll', 'system_tray/dcomp.dll')
FALLBACK_SHA256 = '16e70ea96875b8471d01b3ed8e5dd83b7196d4a64b6629497ae1a08715877237'
PREFIX_MODULES = {'threadpoolwinrt': '--threadpool-build-dir', 'windows.ui': '--windows-ui-build-dir',
                  'windows.storage': '--windows-storage-build-dir', 'shcore': '--shcore-build-dir'}


def write_json(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def copy_checked(source, target, sha256):
    if source.is_symlink() or not source.is_file() or digest_file(source)['sha256'] != sha256:
        raise ValueError(f'Source component changed: {source}')
    target.parent.mkdir(parents=True, exist_ok=True)
    space_check(target.parent, source.stat().st_size)
    with source.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst, 4 * 1024**2)
    target.chmod(0o700 if os.access(source, os.X_OK) else 0o600)
    if digest_file(target)['sha256'] != sha256:
        raise ValueError(f'Copy did not preserve source: {target}')


def compatibility_files(baseline):
    files = {p: h for p, h in baseline['sha256'].items() if p.startswith('builds/')}
    for name in OWN_DLLS:
        relative = 'prefix/' + APP + '/' + name
        files[relative] = baseline['sha256'][relative]
    files['prefix/' + APP + '/system_tray/ghub_wine_coremessaging.dll'] = FALLBACK_SHA256
    return files


def metadata(lock, report, keys_document):
    """Generate new install state; there is deliberately no donor-state input."""
    depots = []
    for name in report['extracted']:
        spec = lock['depots'][name]
        depots.append({'name': name, 'state': 'PRESENT', 'url': spec['url'], 'cipherSuite': '',
                       'iv': '', 'key': '', 'mac': spec['sha256'], 'size': str(spec['size']),
                       'localFolder': WIN_DATA + '\\depots\\' + lock['build_id'] + '\\' + name,
                       'files': spec['expected_files'], 'dependsOn': spec['depends_on'],
                       'required': spec['required']})
    return {
        'current.json': {'version': lock['application_version'], 'buildId': lock['build_id'],
                         'branch': 'staging/2026_6', 'accessGroup': '', 'region': '',
                         'lockdownName': '', 'appId': 'ghub13', 'depots': depots},
        'version.json': {'version': lock['application_version']},
        'groups.json': {'additionalGroups': [], 'applicationGroups': [
            {'uuid': lock['public_access']['access_group'], 'lastModified': keys_document['lastModified']}],
            'buildGroups': {lock['build_id']: lock['public_access']['access_group']}},
        'keys.json': {'accessGroup': '', 'keys': keys_document['keys'], 'lastModified': '0'},
        'periodic_check.json': {'intervalInSeconds': 86400, 'checkPeriodically': False,
                                'downloadAutomatically': False},
    }


def install_depot_cache(cache, data, lock, report):
    """Retain verified vendor archives required by the frontend's integrity check.

    Extracted trees alone are insufficient. The updater uses content-addressed
    archive names; never substitute a marker file for an installed depot.
    """
    for name in report['extracted']:
        spec = lock['depots'][name]
        source = cache / spec['local_name']
        check_asset(source, spec)
    for name in report['extracted']:
        spec = lock['depots'][name]
        copy_checked(cache / spec['local_name'], data / 'cache' / spec['sha256'], spec['sha256'])


def initialize_application_catalog(prefix, report):
    """Seed the agent's current catalog location from its pinned depot payload.

    With apps_updater_enabled, 2026.6 reads this location even on first launch.
    An absent catalog leaves Desktop/default-profile initialization empty when
    the catalog CDN is unavailable. Do not import a user's settings database.
    """
    record = next(item for item in report['layout']['files']
                  if item['destination'] == 'data/applications.json')
    source = prefix / APP / record['destination']
    if digest_file(source)['sha256'] != record['sha256']:
        raise ValueError('Bundled application catalog changed')
    catalog = json.loads(source.read_text())
    if not any(app.get('applicationId') == '420fd454-0c36-499d-bde4-146823b16147'
               and app.get('name') == 'APPLICATION_NAME_DESKTOP'
               for app in catalog['applications']):
        raise ValueError('Bundled catalog has no expected Desktop application')
    copy_checked(source, prefix / CATALOG, record['sha256'])


def disposition(declaration):
    kind = declaration['kind']
    policy = {
        'product': ('configured', 'Product and version are generated in clean installation metadata and registry.'),
        'processControl': ('configured', 'Retained in clean installation metadata.'),
        'launchables': ('partial', 'Updater service is registered stopped/demand-start; tray launch awaits runtime qualification.'),
        'shortcuts': ('deferred', 'Desktop promotion waits for isolated runtime qualification.'),
        'installer': ('replaced', 'This recipe installs verified depot content without invoking the original installer.'),
        'updater': ('frozen', 'Automatic checks/downloads disabled and software-manager loader exclusion set.'),
        'userRepair': ('frozen', 'Vendor repair could replace compatibility resources; not exposed.'),
        'userUninstaller': ('deferred', 'Installer-specific removal has not been implemented.'),
        'uninstaller': ('deferred', 'Installer-specific removal has not been implemented.'),
        'will_connect_to_lus_ipc': ('deferred', 'G HUB processes are not started during preparation.'),
        'conditional_or_external_file': ('deferred', 'Optional integration needs explicit application detection and destination.'),
        'detection': ('deferred', 'Optional integration detection is not run against the host.'),
        'extensions': ('deferred', 'Windows driver/SDK installer requires separate Wine qualification; not marked installed.'),
        'registerLibrary': ('deferred', 'Legacy LED SDK COM registration requires qualification.'),
        'missing_source_file': ('unresolved', 'Vendor core rule names an absent Trueforce SDK; investigate its separate manager.'),
    }
    if kind not in policy:
        raise ValueError(f'Unclassified installer declaration: {kind}')
    status, reason = policy[kind]
    return {**declaration, 'status': status, 'reason': reason}


def registration(baseline, version):
    def quote(text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    lines = ['Windows Registry Editor Version 5.00', '']
    for name, dll in baseline['activation_classes'].items():
        lines += [r'[HKEY_LOCAL_MACHINE\Software\Microsoft\WindowsRuntime\ActivatableClassId' + '\\' + name + ']',
                  '"DllPath"=' + quote(dll)]
        if name != 'Windows.Foundation.Metadata.ApiInformation':
            lines += ['"ThreadingModel"=dword:00000003']
        lines += ['']
    lines += [r'[HKEY_LOCAL_MACHINE\Software\Classes\lghubapp]', '@="URL:lghubapp"', '"URL Protocol"=""', '',
              r'[HKEY_LOCAL_MACHINE\Software\Classes\lghubapp\shell\open\command]',
              '@=' + quote('"' + WIN_APP + r'\lghub.exe" --disable-gpu "%1"'), '',
              r'[HKEY_CURRENT_USER\Software\Wine\AppDefaults\lghub_agent.exe\DllOverrides]',
              '"mfsensorgroup"="native"', '',
              r'[HKEY_CURRENT_USER\Software\Wine\AppDefaults\lghub_system_tray.exe]', '"Version"="win11"', '',
              r'[HKEY_CURRENT_USER\Software\Wine\Fonts\Replacements]', '"Segoe UI"="DejaVu Sans"', '',
              r'[HKEY_CURRENT_USER\Software\Wine\DllOverrides]',
              '"lghub_software_manager.exe"=""', '',
              r'[HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall\Penguin-LGHUB]',
              '"DisplayName"="Penguin-LGHUB experimental candidate"', '"DisplayVersion"=' + quote(version),
              '"InstallLocation"=' + quote(WIN_APP), '"NoModify"=dword:00000001', '"NoRepair"=dword:00000001', '']
    return '\n'.join(lines)


def environment(prefix):
    # No inherited Wine server/module overrides or live desktop connections.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WINE', 'WINEDLL', 'GHUB_', 'LD_'))
           and k not in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER')}
    env.update(WINEPREFIX=str(prefix), WINEARCH='win64', DISPLAY='', WAYLAND_DISPLAY='', WINEDEBUG='-all',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;mscoree,mshtml=;lghub_software_manager.exe=d')
    return env


def sandbox(output, command, hide_host_wine=False, freeze_app=False):
    # Private /tmp owns its wineserver sockets; network and physical devices are
    # absent. Only this new candidate is writable, even through Wine's Z: drive.
    args = ['bwrap', '--ro-bind', '/', '/', '--bind', str(output), str(output),
            '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--unshare-net', '--unshare-pid',
            '--die-with-parent']
    # Read-only mounts do not prevent connecting to Unix-domain sockets. Mask
    # the live session's bus, audio and Wayland sockets, as well as /tmp X11.
    runtime = Path('/run/user') / str(os.getuid())
    if runtime.is_dir():
        args += ['--tmpfs', str(runtime)]
    if hide_host_wine:
        for directory in ('/usr/lib/wine', '/usr/lib64/wine', '/usr/share/wine', '/usr/local/lib/wine', '/usr/local/share/wine'):
            if Path(directory).is_dir():
                args += ['--tmpfs', directory]
        for directory in ('/usr/bin', '/usr/local/bin'):
            for name in ('wine', 'wine64', 'wineserver', 'wine-preloader', 'wine64-preloader'):
                target = Path(directory) / name
                if target.exists():
                    args += ['--ro-bind', '/dev/null', str(target)]
    if freeze_app:
        for relative in (APP, DATA):
            path = output / 'prefix' / relative
            args += ['--ro-bind', str(path), str(path)]
    return args + ['--', *map(str, command)]


def run_logged(output, name, command, env, private=None, runtime=None, freeze_app=False, linux_runtime=None):
    # Flush registry changes and stop this namespace's server before its PID
    # namespace disappears. No cached PID or host-wide process matching is used.
    supervisor = ('import subprocess,sys\n'
                  'try:\n'
                  ' result=subprocess.run(sys.argv[1:],timeout=150)\n'
                  'finally:\n'
                  ' subprocess.run(["wineserver","-k"],timeout=10)\n'
                  ' subprocess.run(["wineserver","-w"],timeout=10)\n'
                  'sys.exit(result.returncode)\n')
    if runtime:
        if private:
            raise ValueError('A packaged runtime cannot use host Wine overlays')
        env = runtime_environment(runtime, output / 'prefix', env)
    argv = sandbox(output, [sys.executable, '-c', supervisor, *command], bool(runtime), freeze_app)
    if linux_runtime:
        import linux_runtime as linux
        env['LD_LIBRARY_PATH'] = linux.library_path(linux_runtime, runtime)
        argv = linux.isolated_command(linux_runtime, output,
            ['/usr/bin/env', *(f'{k}={v}' for k,v in env.items()),
             '/usr/bin/python3', '-c', supervisor, *command],
            freeze_app=freeze_app, read_only_paths=(runtime, linux_runtime))
    if private:
        argv = private + argv
    with (output / (name + '.log')).open('xb') as log:
        try:
            subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            # argv contains forwarded environment values. Report the stage/log,
            # never dump the entire environment into the installer traceback.
            code = getattr(error, 'returncode', 'timeout')
            raise RuntimeError(f'{name} failed ({code}); inspect {log.name}') from None


def initialize_installation(output, lock):
    app = output / 'prefix' / APP
    folders = {WIN_APP}
    files = []
    for path in sorted(app.rglob('*')):
        if path.is_file():
            files.append({'path': WIN_APP + '\\' + str(path.relative_to(app)).replace('/', '\\'),
                          'registerLibrary': False, 'sha256': digest_file(path)['sha256']})
        elif path.is_dir():
            folders.add(WIN_APP + '\\' + str(path.relative_to(app)).replace('/', '\\'))
    manifest = json.loads((output / 'prefix' / DATA / 'depots' / lock['build_id'] / 'core/manifest.json').read_text())['installer']
    product = {**manifest['product'], 'version': lock['application_version']}
    install = {'autostartables': [], 'drivers': [], 'extensions': {}, 'files': files,
               'folders': [{'path': p} for p in sorted(folders)], 'processControl': manifest['processControl'],
               'product': product, 'renamedItems': [], 'services': [
                   {'asConsoleUser': False, 'command': {'arguments': ['--run-as-service'],
                    'executable': WIN_APP + r'\lghub_updater.exe', 'isSystem': False}, 'service': 'LGHUBUpdaterService'}],
               'shortcuts': [], 'uninstallCommands': [], 'urlSchemes': []}
    write_json(output / 'prefix' / DATA / 'installation.json', install)


def verify_prepared(output):
    report = json.loads((output / 'clean-prefix.json').read_text())
    if report['schema'] != 1 or report['kind'] != 'clean-prefix-development-candidate':
        raise ValueError('Not a clean-prefix candidate')
    for module in PREFIX_MODULES:
        if f'prefix/drive_c/windows/system32/{module}.dll' not in report['files']:
            raise ValueError(f'Missing prefix module receipt: {module}')
    for name, sha in report['files'].items():
        relative_name(name)
        target = output / name
        if not target.resolve().is_relative_to(output) or target.is_symlink() or digest_file(target)['sha256'] != sha:
            raise ValueError(f'Installed file changed: {name}')
    for directory in ('prefix/' + APP, 'prefix/' + DATA, 'frontend', 'builds'):
        actual = {directory + '/' + n for n in staged_files(output / directory)}
        expected = {n for n in report['files'] if n.startswith(directory + '/')}
        if actual != expected:
            raise ValueError(f'Installed file set changed: {directory}')
    return {'managed_files_verified': len(report['files']), 'depot_files': report['depot_files'],
            'activation_factories_verified_at_install': report['activation_factories_verified'],
            'public_installer_ready': False}


def prepare(stage, cache, output, compatibility, windows_dlls, runtime=None, linux_runtime=None):
    if os.geteuid() == 0:
        raise ValueError('Run as your normal user; the private runtime runner handles its own sudo')
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Output must be a new directory with an existing parent')
    for source in (stage, cache, runtime or compatibility, windows_dlls):
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError('Output must be separate from acquisition and compatibility inputs')
    lock, baseline = json.loads(LOCK_PATH.read_text()), json.loads(BASELINE.read_text())
    verified = verify_stage(stage, lock)
    report = json.loads((stage / 'extraction.json').read_text())
    if report['selection'] != 'tested_setup':
        raise ValueError('This first prefix recipe requires the verified tested_setup selection')
    actions = [disposition(d) for d in report['layout']['pending_installer_actions']]
    public_keys(cache, lock['public_access'])
    keys_document = json.loads((cache / 'vendor-public-access.json').read_text())
    bundle = verify_runtime(runtime) if runtime else None
    components = {} if runtime else compatibility_files(baseline)
    for name, sha in components.items():
        path = compatibility / name
        if not path.resolve().is_relative_to(compatibility) or path.is_symlink() or digest_file(path)['sha256'] != sha:
            raise ValueError(f'Compatibility artifact changed: {name}')
    media = json.loads((ROOT / 'packaging/windows-acquisition.lock.json').read_text())
    for name in ('d2d1.dll', 'ninput.dll'):
        check_asset(windows_dlls / name, media['files'][name])
    bundled_probe = runtime / 'tools/clean_prefix_probe.exe' if runtime else None
    has_probe = bool(bundle and 'tools/clean_prefix_probe.exe' in bundle['files'])
    required = ['bwrap'] + ([] if has_probe else ['x86_64-w64-mingw32-g++']) + ([] if runtime else ['wine', 'wineserver', 'sudo'])
    for tool in required:
        if not shutil.which(tool):
            raise ValueError(f'Required existing tool missing: {tool}')
    env = environment(output / 'prefix')
    if runtime:
        env = runtime_environment(runtime, output / 'prefix', env)
    if not linux_runtime and subprocess.check_output(['wine', '--version'], env=env, text=True).strip() != 'wine-10.20':
        raise ValueError('Recipe requires the pinned Wine 10.20 runtime')
    if not runtime:
        subprocess.run(['sudo', '-n', 'true'], check=True, timeout=15)
    subprocess.run(['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                    '--tmpfs', '/tmp', '--unshare-net', '--unshare-pid', '--', '/usr/bin/true'], check=True)
    space_check(output.parent, 5 * 1024**3)
    output.mkdir(mode=0o700)
    (output / 'home').mkdir()
    probe = output / 'clean_prefix_probe.exe'
    if has_probe:
        copy_checked(bundled_probe, probe, bundle['files']['tools/clean_prefix_probe.exe']['sha256'])
    else:
        subprocess.run(['x86_64-w64-mingw32-g++', '-O2', '-Wall', '-Wextra', '-Werror', '-static',
                        str(ROOT / 'tools/packaging/clean_prefix_probe.cpp'), '-o', str(probe),
                        '-lruntimeobject', '-lole32', '-luuid', '-ladvapi32'], check=True, timeout=120)
    print('Initializing a new headless Wine prefix with no network or physical devices...', flush=True)
    run_logged(output, 'wineboot', ['wine', 'wineboot', '-i'], env, runtime=runtime, linux_runtime=linux_runtime)
    prefix, app, data = output / 'prefix', output / 'prefix' / APP, output / 'prefix' / DATA
    # Fresh Wine creates convenience links to host documents. Replace only those
    # links in the new candidate with empty directories; copy no personal files.
    for path in (prefix / 'drive_c/users').glob('*/*'):
        if path.is_symlink():
            path.unlink()
            path.mkdir()
    print('Installing verified application files and 30 depot trees...', flush=True)
    for item in report['layout']['files']:
        copy_checked(stage / 'application/LGHUB' / item['destination'], app / item['destination'], item['sha256'])
    for name, records in report['extracted'].items():
        for item in records:
            copy_checked(stage / 'depots' / name / item['path'], data / 'depots' / lock['build_id'] / name / item['path'], item['sha256'])
    install_depot_cache(cache, data, lock, report)
    initialize_application_catalog(prefix, report)
    for name, value in metadata(lock, report, keys_document).items():
        write_json(data / name, value)
    print('Installing only pinned compatibility components and acquired Windows DLLs...', flush=True)
    for name, sha in components.items():
        copy_checked(compatibility / name, output / name, sha)
    if bundle:
        (output / 'builds').mkdir()  # Empty: runtime components remain in their separate bundle.
        for spec in bundle['supplements'].values():
            copy_checked(runtime / spec['target'], app / spec['application_path'], spec['sha256'])
    for name in ('d2d1.dll', 'ninput.dll'):
        copy_checked(windows_dlls / name, app / 'system_tray' / name, media['files'][name]['sha256'])
    copy_checked(app / 'd3dcompiler_47.dll', app / 'system_tray/D3DCompiler_47.dll',
                 baseline['sha256']['prefix/' + APP + '/system_tray/D3DCompiler_47.dll'])
    # Prefix fallbacks are used by several Wine activation paths even when the
    # corresponding system modules are overlaid in the private runtime.
    for module, option in PREFIX_MODULES.items():
        source = (runtime / f'usr/lib/wine/x86_64-windows/{module}.dll' if runtime else
                  output / baseline['build_groups'][option] / f'dlls/{module}/x86_64-windows/{module}.dll')
        target = prefix / f'drive_c/windows/system32/{module}.dll'
        target.unlink(missing_ok=True)
        copy_checked(source, target, digest_file(source)['sha256'])
    (output / 'frontend').mkdir()
    color = prepare_color(app / 'resources/app.asar', output / 'frontend/color-preview.asar')
    xbf, pri = patch_resources((app / 'system_tray/MainWindow.xbf').read_bytes(), (app / 'system_tray/resources.pri').read_bytes())
    (app / 'system_tray/MainWindow.xbf').write_bytes(xbf)
    (app / 'system_tray/resources.pri').write_bytes(pri)
    for name in ('frontend/color-preview.asar', 'prefix/' + APP + '/system_tray/MainWindow.xbf',
                 'prefix/' + APP + '/system_tray/resources.pri'):
        if digest_file(output / name)['sha256'] != baseline['sha256'][name]:
            raise ValueError('Resource transformation differs from working baseline')
    initialize_installation(output, lock)
    write_json(output / 'installer-actions.json', actions)
    reg = prefix / 'drive_c/penguin-install.reg'
    reg.write_text(registration(baseline, lock['application_version']), encoding='utf-16')
    run_logged(output, 'registry', ['wine', 'reg', 'import', r'C:\penguin-install.reg'], env, runtime=runtime, linux_runtime=linux_runtime)
    run_logged(output, 'service', ['wine', 'sc', 'create', 'LGHUBUpdaterService', 'binPath=',
               '"' + WIN_APP + r'\lghub_updater.exe" --run-as-service', 'start=', 'demand',
               'DisplayName=', 'LGHUB Updater Service'], env, runtime=runtime, linux_runtime=linux_runtime)
    reg.unlink()
    private = None
    if not runtime:
        private = [sys.executable, str(ROOT / 'tools/wine/run_private_build.py'), '--prefix', str(prefix)]
        for option, directory in baseline['build_groups'].items():
            private += [option, str(output / directory)]
    # run_private_build deliberately filters its environment: pass feature flags
    # explicitly to the sandboxed child, never inherit them from a live session.
    runtime_env = {**baseline['environment'], 'WINEDEBUG': '-all',
                   'WINEDLLOVERRIDES': baseline['environment']['WINEDLLOVERRIDES'] + ';lghub_software_manager.exe=d'}
    print('Probing all eight WinRT factories and the stopped service in the private runtime...', flush=True)
    run_logged(output, 'activation-probe', ['env', *(f'{k}={v}' for k,v in runtime_env.items()),
                                          'wine', str(probe)], env, private, runtime=runtime, linux_runtime=linux_runtime)
    probe.unlink()
    installed = {str(p.relative_to(output)): digest_file(p)['sha256'] for folder in (app, data, output / 'frontend', output / 'builds')
                 for p in folder.rglob('*') if p.is_file()}
    for module in PREFIX_MODULES:
        name = f'prefix/drive_c/windows/system32/{module}.dll'
        installed[name] = digest_file(output / name)['sha256']
    installed['prefix/' + CATALOG] = digest_file(prefix / CATALOG)['sha256']
    result = {'schema': 1, 'kind': 'clean-prefix-development-candidate', 'application_version': lock['application_version'],
              'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'depot_lock': lock_digest(lock),
              'depot_files': verified['depot_files_verified'], 'cached_depots': len(report['extracted']),
              'application_layout_files': verified['application_files_verified'],
              'donor_prefix_cloned': False, 'personal_state_copied': False, 'live_desktop_used': False,
              'vendor_applications_started': False, 'service_start': 'demand; stopped',
              'activation_factories_verified': 8, 'registry_routes_verified': 4, 'color_archive': color['output_sha256'],
              'installer_dispositions': dict(Counter(x['status'] for x in actions)),
              'runtime_reference': str(runtime or compatibility), 'runtime_builds': {} if runtime else baseline['build_groups'],
              'packaged_runtime': {'path': str(runtime), 'manifest_sha256': digest_file(runtime / 'runtime.json')['sha256']} if runtime else None,
              'host_wine_hidden': bool(runtime),
              'runtime_environment': runtime_env, 'files': installed,
              'remaining': ['Unix-library portability and runtime source-release audit' if runtime else 'Complete private Wine runtime packaging', 'Fonts acquisition and UI qualification',
                            'Full agent/UI/device validation in the new prefix', 'Deferred installer actions and Trueforce investigation',
                            'Desktop launcher promotion and service startup policy']}
    write_json(output / 'clean-prefix.json', result)
    write_json(output / 'verification.json', verify_prepared(output))
    print(json.dumps({k:v for k,v in result.items() if k not in ('files','runtime_environment','runtime_builds')}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--compatibility-root', type=Path)
    group.add_argument('--runtime', type=Path)
    parser.add_argument('--windows-dlls', type=Path)
    parser.add_argument('--linux-runtime', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps(verify_prepared(external_path(args.output)), indent=2))
        return
    if not all((args.stage, args.cache, args.runtime or args.compatibility_root, args.windows_dlls)):
        parser.error('Preparation requires --stage, --cache, --windows-dlls and either --runtime or --compatibility-root')
    cache = external_path(args.cache)
    with work_lock(cache):
        prepare(external_path(args.stage), cache, external_path(args.output),
                args.compatibility_root.resolve(strict=True) if args.compatibility_root else None,
                args.windows_dlls.resolve(strict=True), args.runtime.resolve(strict=True) if args.runtime else None,
                args.linux_runtime.resolve(strict=True) if args.linux_runtime else None)


if __name__ == '__main__':
    os.umask(0o077)
    main()
