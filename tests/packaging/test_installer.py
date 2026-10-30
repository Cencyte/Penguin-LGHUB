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

    def test_help_and_no_arguments_never_probe_or_install(self):
        for args in ([],['--help']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),patch.object(install.host_preflight,'inventory') as inventory:
                self.assertEqual(install.main(args),0)
                inventory.assert_not_called()
            text=out.getvalue();self.assertTrue(text.startswith('USAGE\n'))
            self.assertEqual('SUMMARY' in text,bool(args))
            if args:
                for heading in ('SUMMARY','FLAGS','EXAMPLES'):self.assertIn('\n'+heading+'\n',text)

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
                self.assertIn('[Wine] Compile fixture...',out.getvalue())
                self.assertEqual(log.stat().st_mode & 0o777,0o600)

    def test_sudo_retains_controlling_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            view=ui.Presentation(Path(tmp)/'log',stream=io.StringIO())
            child=MagicMock();child.stdout=io.StringIO('authorized\n');child.wait.return_value=0
            with patch.object(ui.subprocess,'Popen',return_value=child) as popen:
                view.run(['sudo','python3','scoped-helper'])
                self.assertFalse(popen.call_args.kwargs['start_new_session'])
                self.assertNotIn('stdin',popen.call_args.kwargs)
            view.close()

    def test_graphics_fallback_avoids_escape_protocol_in_unknown_terminal(self):
        self.assertEqual(ui.logo_format({'TERM':'xterm-256color'}),'symbols')
        self.assertEqual(ui.logo_format({'TERM':'xterm-kitty'}),'kitty')
        self.assertEqual(ui.logo_format({'KONSOLE_VERSION':'260801'}),'sixels')
        self.assertEqual(ui.logo_format({'TERM':'xterm-kitty','TMUX':'yes'}),'symbols')

    def test_retry_preserves_partial_and_refuses_backup_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            item=Path(tmp)/'candidate';item.mkdir();(item/'log').write_text('diagnostic')
            install.preserve_partial((item,),1)
            self.assertEqual((Path(tmp)/'candidate.incomplete-1/log').read_text(),'diagnostic')
            item.mkdir()
            with self.assertRaises(RuntimeError):install.preserve_partial((item,),1)

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
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'installation';commands=[]
            args=install.parser().parse_args(['--install','--directory',str(root)])
            view=MagicMock();view.run.side_effect=lambda command:commands.append(list(map(str,command)))
            with patch.object(install,'space_check'),patch.object(install.subprocess,'check_output',return_value=str(Path(tmp)/'Desktop')+'\n'):
                install.pipeline(args,view,{'session':'wayland','selinux':'Enforcing'})
            flattened=[' '.join(c) for c in commands]
            ui_at=next(i for i,c in enumerate(flattened) if 'linux_ui_probe.py' in c)
            sudo_at=next(i for i,c in enumerate(commands) if c[0]=='sudo')
            launcher_at=next(i for i,c in enumerate(flattened) if 'install-entry' in c)
            self.assertLess(ui_at,sudo_at);self.assertLess(sudo_at,launcher_at)
            self.assertIn('--experimental-wayland',commands[launcher_at])
            self.assertTrue(any('windows_media_acquire.py fetch' in c and '--decoder' in c for c in flattened))
            self.assertTrue((root/'source/LICENSE').is_file())
            state=json.loads((root/'installation.json').read_text())
            self.assertTrue(state['installed']);self.assertTrue(state['owner_acceptance_pending'])

if __name__=='__main__':unittest.main()
