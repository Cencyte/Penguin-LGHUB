import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/packaging'))
import install
import installer_ui as ui
import toolchain
import session_probe
from test_linux_runtime import deb_bytes


class InstallerTests(unittest.TestCase):
    def test_tool_archive_preserves_compiler_alias_and_rejects_escape(self):
        for target in ('usr/bin/compiler','../../outside'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);stream=io.BytesIO()
                with tarfile.open(fileobj=stream,mode='w:xz') as tar:
                    item=tarfile.TarInfo('usr/bin/compiler');item.size=7
                    tar.addfile(item,io.BytesIO(b'fixture'))
                    link=tarfile.TarInfo('usr/bin/cc');link.type=tarfile.LNKTYPE
                    link.linkname=target;tar.addfile(link)
                archive=root/'tools.deb';archive.write_bytes(deb_bytes(stream.getvalue()))
                if target.startswith('..'):
                    with self.assertRaises((ValueError,tarfile.TarError)):
                        toolchain.extract_tools(archive,root/'out')
                else:
                    toolchain.extract_tools(archive,root/'out')
                    self.assertEqual((root/'out/usr/bin/cc').read_bytes(),b'fixture')

    def test_help_never_probes_or_installs(self):
        for args in (['--help'],['--install','--help']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),patch.object(install.host_preflight,'inventory') as inventory,\
                 patch.object(install,'present_install') as present:
                self.assertEqual(install.main(args),0)
                inventory.assert_not_called()
                present.assert_not_called()
            text=out.getvalue();self.assertTrue(text.startswith('USAGE\n'))
            for heading in ('SUMMARY','FLAGS','EXAMPLES'):self.assertIn('\n'+heading+'\n',text)

    def test_default_and_explicit_install_route_to_the_same_pipeline(self):
        for argv in ([],['--install'],['--verbose','--jobs','2','--directory','/tmp/example']):
            with self.subTest(argv=argv),patch.object(install.os,'geteuid',return_value=1000),\
                 patch.object(install,'present_install',return_value=0) as present:
                self.assertEqual(install.main(argv),0)
                args=present.call_args.args[0]
                self.assertTrue(args.install)
                self.assertFalse(args.check or args.uninstall)
                if '--verbose' in argv:
                    self.assertTrue(args.verbose)
                    self.assertEqual(args.jobs,2)
                    self.assertEqual(args.directory,Path('/tmp/example'))

    def test_explicit_check_and_uninstall_do_not_default_to_install(self):
        with patch.object(install.os,'geteuid',return_value=1000),\
             patch.object(install,'present_install',return_value=0) as present,\
             patch('uninstall.run',return_value=0) as remove:
            self.assertEqual(install.main(['--check']),0)
            args=present.call_args.args[0]
            self.assertTrue(args.check)
            self.assertFalse(args.install or args.uninstall)
            remove.assert_not_called()
            present.reset_mock()
            self.assertEqual(install.main(['--uninstall','--directory','/tmp/example']),0)
            present.assert_not_called()
            remove.assert_called_once_with(Path('/tmp/example'),False)

    def test_invalid_or_conflicting_options_never_start_installation(self):
        for argv in (['--instal'],['--install','--check'],['--uninstall','--check']):
            with self.subTest(argv=argv),contextlib.redirect_stderr(io.StringIO()),\
                 patch.object(install,'present_install') as present:
                with self.assertRaises(SystemExit) as error:install.main(argv)
                self.assertEqual(error.exception.code,2)
                present.assert_not_called()

    def test_quiet_and_verbose_both_keep_stdout_stderr_and_failure(self):
        for verbose in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                log=Path(tmp)/'install.log';out=io.StringIO();view=ui.Presentation(log,verbose,out)
                view.header();view.step('Wine','Compile fixture')
                with self.assertRaises(subprocess.CalledProcessError):
                    view.run([sys.executable,'-c',"import sys;print('child stdout');print('child stderr',file=sys.stderr);sys.exit(7)"])
                view.close();saved=log.read_text()
                for line in ('child stdout','child stderr','[exit 7]'):self.assertIn(line,saved)
                self.assertEqual('child stdout' in out.getvalue(),verbose)
                self.assertRegex(out.getvalue(),r'\[Wine\] +Compile fixture\.\.\.')
                self.assertEqual(log.stat().st_mode & 0o777,0o600)

    def test_sudo_retains_controlling_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            view=ui.Presentation(Path(tmp)/'log',stream=io.StringIO())
            child=MagicMock();child.stdout=io.StringIO('authorized\n');child.wait.return_value=0
            def start(*args,**kwargs):
                self.assertEqual(view._spinner_suspended,1)
                return child
            with patch.object(ui.subprocess,'Popen',side_effect=start) as popen, \
                 patch.object(view,'wait_for_sudo') as authorize:
                view.run(['sudo','python3','scoped-helper'])
                authorize.assert_called_once_with(None)
                self.assertFalse(popen.call_args.kwargs['start_new_session'])
                self.assertNotIn('stdin',popen.call_args.kwargs)
                self.assertEqual(view._spinner_suspended,0)
            view.close()

    def test_graphics_fallback_avoids_escape_protocol_in_unknown_terminal(self):
        self.assertEqual(ui.logo_format({'TERM':'xterm-256color'}),'symbols')
        self.assertEqual(ui.logo_format({'TERM':'xterm-kitty'}),'kitty')
        self.assertEqual(ui.logo_format({'KONSOLE_VERSION':'260801'}),'kitty')
        self.assertEqual(ui.logo_format({'TERM':'xterm-kitty','TMUX':'yes'}),'symbols')

    def test_acquisition_engines_hide_desktop_network_and_keep_user_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'sdk';work=Path(tmp)/'work'
            (work/'source/tools/packaging').mkdir(parents=True)
            bubble=toolchain.command(root,work,['true'])
            self.assertIn('--unshare-all',bubble);self.assertIn('--ro-bind',bubble)
            podman=toolchain.command(root,work,['true'],'podman')
            for item in ('--network=none','--userns=keep-id','--read-only','--cap-drop=ALL',
                         '--security-opt=no-new-privileges','--tmpfs=/tmp:rw,nosuid,nodev,mode=1777'):
                self.assertIn(item,podman)
            self.assertNotIn('--privileged',podman)
            self.assertNotIn('label=disable',' '.join(podman))
            self.assertNotIn('DISPLAY=',' '.join(podman))

    def test_session_probe_records_failures_without_claiming_device_qualification(self):
        with patch.dict(os.environ),patch.object(session_probe,'egl_probe',side_effect=RuntimeError('no driver')),\
             patch.object(session_probe,'audio_probe',return_value={'pulse_connected':False}):
            result=session_probe.worker({'linux_runtime':'/linux','wine':'/wine'})
        self.assertFalse(result['window_created']);self.assertFalse(result['input_injected'])
        self.assertFalse(result['end_to_end_qualified']);self.assertGreaterEqual(len(result['warnings']),2)

    def test_pipeline_orders_verification_before_privileged_and_launcher_steps(self):
        for session in ('x11', 'wayland'):
            with self.subTest(session=session):
                with tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp)/'installation';commands=[]
                    args=install.parser().parse_args(['--install','--directory',str(root)])
                    view=ui.Presentation(Path(tmp)/'transaction.log',stream=io.StringIO());self.addCleanup(view.close)
                    view.run=MagicMock();view.run.side_effect=lambda command:commands.append(list(map(str,command)))
                    with patch.object(install,'space_check'),patch.object(install.install_transaction,'synchronize'),patch.object(install.subprocess,'check_output',return_value=str(Path(tmp)/'Desktop')+'\n'):
                        install.pipeline(args,view,{'session':session,'selinux':'Enforcing'})
                    flattened=[' '.join(c) for c in commands]
                    ui_at=next(i for i,c in enumerate(flattened) if 'linux_ui_probe.py' in c)
                    profile_at=next(i for i,c in enumerate(flattened) if 'profile_support.py' in c)
                    sudo_at=next(i for i,c in enumerate(commands) if c[0]=='sudo')
                    launcher_at=next(i for i,c in enumerate(flattened) if 'install-entry' in c)
                    self.assertLess(ui_at,sudo_at);self.assertLess(sudo_at,launcher_at)
                    self.assertLess(profile_at,sudo_at)
                    self.assertNotIn('--experimental-wayland',commands[launcher_at])
                    self.assertEqual(commands[launcher_at][commands[launcher_at].index('--session')+1],'auto')
                    self.assertEqual(Path(commands[launcher_at][commands[launcher_at].index('--destination')+1]).name,'Penguin-LGHUB.desktop')
                    self.assertTrue(any('windows_media_acquire.py fetch' in c and '--decoder' in c for c in flattened))
                    self.assertTrue((root/'source/LICENSE').is_file())
                    state=json.loads((root/'installation.json').read_text())
                    self.assertTrue(state['installed']);self.assertTrue(state['owner_acceptance_pending'])


if __name__=='__main__':unittest.main()
