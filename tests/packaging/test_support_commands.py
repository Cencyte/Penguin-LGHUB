import contextlib
import tarfile
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import socket
import threading
import base64
import shutil
import subprocess
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/packaging'))
import device_compatibility as devices
import diagnostics as diag
import installer_update as update
import install
from installer_ui import Presentation


def write_source(root,version='old'):
    root.mkdir(exist_ok=True)
    (root/'packaging').mkdir(exist_ok=True)
    (root/'install.sh').write_text(version)
    (root/'install.sh').chmod(0o755)
    record={'path':'install.sh','mode':'100755','bytes':len(version),'sha256':hashlib.sha256(version.encode()).hexdigest()}
    (root/update.MANIFEST).write_text(json.dumps({'repository':'https://github.com/Cencyte/Penguin-LGHUB','files':[record]}))
    return update.manifest(root)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.catalog={'devices':[{'name':'Logitech G502 HERO','identities':['046d:c08b'],'depots':['hero']}]}
        self.lock={'selections':{'all_devices':['hero','firmware']},'depots':{'hero':{'depends_on':['firmware']},'firmware':{'depends_on':[]}}}
        self.mouse={'id':'046d:c08b','name':'G502 HERO','interfaces':[]}

    def test_missing_dependency_is_a_failure(self):
        report=devices.inspect([self.mouse],self.catalog,self.lock,available=['hero'])
        self.assertEqual(report['status'],'FAIL');self.assertEqual(report['devices'][0]['missing_depots'],['firmware'])

    def test_all_resources_and_no_peripherals(self):
        self.assertEqual(devices.inspect([self.mouse],self.catalog,self.lock)['status'],'OK')
        report=devices.inspect([],self.catalog,self.lock)
        self.assertEqual(report['devices'],[]);self.assertIn('not certified',report['basis'])

    def test_unknown_gaming_device_fails_but_office_logitech_is_not_misclassified(self):
        gaming={'id':'046d:ffff','name':'Logitech G999 Gaming Mouse'}
        office={'id':'046d:ffff','name':'Logitech M185'}
        self.assertEqual(devices.inspect([gaming],self.catalog,self.lock)['status'],'FAIL')
        self.assertEqual(devices.inspect([office],self.catalog,self.lock)['status'],'OK')
        self.assertTrue(devices.inspect([office],self.catalog,self.lock)['warnings'])

    def test_category_and_result_in_install_presentation(self):
        with tempfile.TemporaryDirectory() as d:
            stream=io.StringIO();ui=Presentation(Path(d)/'log',stream=stream)
            devices.present(devices.inspect([self.mouse],self.catalog,self.lock),ui);ui.close()
            self.assertIn('Checking device compatibility...',stream.getvalue())
            self.assertIn('OK',stream.getvalue());self.assertIn('Logitech G502 HERO is compatible.',stream.getvalue())

    def test_catalog_includes_classic_g502_and_wheels(self):
        data=json.loads((devices.ROOT/'packaging/device-catalog.json').read_text())
        names={x['model'] for x in data['devices']}
        self.assertTrue({'g502_core','g502_hero','g502_spectrum','g502_wireless','g29'}<=names)

    def test_hidio_and_subdevice_ids(self):
        from build_device_catalog import identities
        self.assertEqual(identities([{'id':'HID_046d_c24a_ff800080'}, {'id':'046d_c276_MPO12'}]),{'046d:c24a','046d:c276'})

    def test_duplicate_vendor_blobs_share_one_verified_cache_entry(self):
        from prepare_clean_prefix import install_depot_cache
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'cache';cache.mkdir();data=root/'data';data.mkdir()
            payload=b'fixture';sha=hashlib.sha256(payload).hexdigest()
            lock={'depots':{}}
            for name in ('first','second'):
                (cache/name).write_bytes(payload)
                lock['depots'][name]={'local_name':name,'sha256':sha,'size':len(payload)}
            install_depot_cache(cache,data,lock,{'extracted':['first','second']})
            self.assertEqual(list((data/'cache').iterdir()),[data/'cache'/sha])
            (data/'cache'/sha).write_bytes(b'corrupt')
            with self.assertRaises((ValueError,RuntimeError)):
                install_depot_cache(cache,data,lock,{'extracted':['first','second']})


class DiagnosticTests(unittest.TestCase):
    def test_wineserver_listener_requires_agent_and_exact_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);proc=root/'proc';(proc/'net').mkdir(parents=True)
            (proc/'net/tcp').write_text('header\n0: 0100007F:2332 00000000:0000 0A 0 0 0 1000 0 55\n')
            for pid,command in ((12,'wine lghub_agent.exe'),(13,'wineserver')):
                p=proc/str(pid);(p/'fd').mkdir(parents=True)
                (p/'cmdline').write_bytes(command.encode())
                (p/'environ').write_bytes(os.fsencode('WINEPREFIX='+str(root/'desktop/prefix'))+b'\0')
                (p/'exe').symlink_to('/private/'+('wine' if pid==12 else 'wineserver'))
            (proc/'13/fd/4').symlink_to('socket:[55]')
            self.assertEqual(diag.owned_agent(root,proc),12)
            (proc/'13/environ').write_bytes(b'WINEPREFIX=/some/other/prefix\0')
            self.assertIsNone(diag.owned_agent(root,proc))

    def test_websocket_issues_only_read_only_device_request(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
            address=listener.getsockname();received=[];errors=[]
            def server():
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(5);stream=conn.makefile('rb');header=b''
                        while not header.endswith(b'\r\n\r\n'):header+=stream.read(1)
                        nonce=next(line.split(b': ',1)[1] for line in header.split(b'\r\n') if line.startswith(b'Sec-WebSocket-Key:'))
                        accept=base64.b64encode(hashlib.sha1(nonce+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                        conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                        first,second=stream.read(2);length=second&127
                        if length==126:length=int.from_bytes(stream.read(2),'big')
                        mask=stream.read(4);raw=stream.read(length)
                        request=json.loads(bytes(v^mask[i%4] for i,v in enumerate(raw)));received.append(request)
                        response=json.dumps({'msgId':request['msgId'],'payload':{'deviceInfos':[]}}).encode()
                        conn.sendall(bytes([0x81,len(response)])+response);stream.close()
                except Exception as e:errors.append(e)
            thread=threading.Thread(target=server);thread.start()
            connect=socket.create_connection
            with patch.object(diag.socket,'create_connection',side_effect=lambda _,timeout:connect(address,timeout=timeout)):
                result=diag.device_response()
            thread.join(6);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            self.assertEqual(diag.minimal_devices(result),[])
            self.assertEqual([(r['verb'],r['path']) for r in received],[('GET','/devices/list')])

    def test_sanitizer_masks_sensitive_data_and_preserves_device_identity(self):
        s=diag.Sanitizer()
        text=s.text('046d:c08b 192.168.68.1 192.168.68.1 2001:db8::1 aa:bb:cc:dd:ee:ff a@b.example token=canary_secret /home/another/private')
        for secret in ('192.168.68.1','2001:db8::1','aa:bb:cc:dd:ee:ff','a@b.example','canary_secret','another'):
            self.assertNotIn(secret,text)
        self.assertIn('046d:c08b',text);self.assertEqual(text.count('<IP-1>'),2)
        self.assertNotIn('serialNumber',s.clean({'name':'G502','serialNumber':'private'}))

    def test_agent_export_never_keeps_arbitrary_fields(self):
        result=diag.minimal_devices({'payload':{'deviceInfos':[{'displayName':'G502','serial':'private','givenName':'my mouse','deviceModel':'g502_hero','state':'ACTIVE','configurations':[{'name':'private profile'}]}]},'account':{'token':'private'}})
        self.assertEqual(result,[{'displayName':'G502','deviceModel':'g502_hero','state':'ACTIVE'}])

    def test_log_excerpts_are_canonical_not_arbitrary_messages(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'desktop/logs';p.mkdir(parents=True)
            (p/'one-desktop.log').write_text('PermissionError: password=canary secret /home/other\nerr:hid: arbitrary private text\n[Errno 13]\n')
            result=diag.error_summary(root)
            self.assertNotIn('canary',str(result));self.assertEqual(result['bounded_error_excerpts']['PermissionError'],1)

    def test_non_running_diag_exports_one_tar_gzip_with_private_text_and_live_failures(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'source';project.mkdir()
            out=io.StringIO()
            with patch.object(diag,'owned_agent',return_value=None),patch.object(diag,'package_query',return_value=[]),patch.object(devices,'discover',return_value=[]),contextlib.redirect_stdout(out):
                result=diag.run(root/'missing',project=project)
            self.assertEqual(result,1)
            files=list(project.iterdir());self.assertEqual(len(files),1);self.assertTrue(files[0].name.endswith('.tar.gz'))
            self.assertTrue(out.getvalue().startswith('Open Penguin-LGHUB first'))
            with tarfile.open(files[0],'r:gz') as archive:
                members=archive.getmembers();self.assertEqual(len(members),1)
                member=members[0];self.assertEqual(member.name,'Penguin-LGHUB-diagnostics.txt')
                self.assertTrue(member.isfile());self.assertEqual(member.mode,0o600)
                self.assertEqual((member.uid,member.gid,member.uname,member.gname,member.mtime),(0,0,'','',0))
                report=json.loads(archive.extractfile(member).read().decode().split('\n',1)[1])
            self.assertEqual(files[0].stat().st_mode&0o777,0o600)
            live=[c for c in report['checks'] if c['step'] in ('Query running G HUB device recognition','Check live session device access')]
            self.assertEqual(len(live),2);self.assertTrue(all(x['status']=='FAIL' for x in live))

    def test_running_diag_omits_open_warning(self):
        with tempfile.TemporaryDirectory() as d,patch.object(diag,'owned_agent',return_value=123),patch.object(diag,'package_query',return_value=[]),patch.object(devices,'discover',return_value=[]),patch.object(diag,'device_response',return_value={'result':{'code':'SUCCESS'},'payload':{'deviceInfos':[{'displayName':'G502'}]}}):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):diag.run(Path(d)/'missing',project=Path(d))
            self.assertNotIn('Open Penguin-LGHUB first',out.getvalue())


class SourceUpdateTests(unittest.TestCase):
    def test_source_update_end_to_end_for_zip_and_git(self):
        for git_mode in (False,True):
            with self.subTest(git=git_mode),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);upstream=base/'upstream';root=base/'source'
                write_source(upstream)
                def git(*args,cwd=upstream):
                    return subprocess.check_output(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],cwd=cwd,stderr=subprocess.DEVNULL,text=True).strip()
                if git_mode:
                    git('init','-b','release');git('add','.');git('commit','-m','old')
                    git('clone',str(upstream),str(root),cwd=base)
                else:shutil.copytree(upstream,root)
                write_source(upstream,'new')
                if git_mode:
                    git('add','.');git('commit','-m','new');revision=git('rev-parse','HEAD')
                else:revision='a'*40
                archive=base/'fixture.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    for p in (upstream/'install.sh',upstream/update.MANIFEST):z.write(p,'repo/'+str(p.relative_to(upstream)))
                def download(url,target,maximum):
                    if url==update.API:target.write_text(json.dumps({'sha':revision}))
                    else:shutil.copyfile(archive,target)
                with patch.object(update,'download',side_effect=download),patch.object(update,'REPOSITORY',str(upstream)),patch.dict(os.environ,{'XDG_STATE_HOME':str(base/'state')}),contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(update.run(project=root),0)
                self.assertEqual((root/'install.sh').read_text(),'new');update.manifest(root)
                self.assertFalse((root/update.JOURNAL).exists())
                if git_mode:self.assertEqual(git('rev-parse','HEAD',cwd=root),revision)

    def test_interrupted_update_journal_restores_exact_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_source(root)
            journal=root/update.JOURNAL;(journal/'backup').mkdir(parents=True)
            os.link(root/'install.sh',journal/'backup/install.sh')
            (root/'new').write_text('new');os.replace(root/'new',root/'install.sh')
            (journal/'state.json').write_text(json.dumps({'status':'preparing','entries':[['install.sh',True],['added.py',False]]}))
            (root/'added.py').write_text('partial')
            update.recover(root)
            self.assertEqual((root/'install.sh').read_text(),'old');self.assertFalse((root/'added.py').exists());update.manifest(root)

    def test_update_preserves_untracked_files_and_never_touches_installed_payload(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';old=write_source(root)
            staged=Path(d)/'stage';new=write_source(staged,'new')
            (root/'report.txt.gz').write_bytes(b'private')
            installed=Path(d)/'installed';installed.mkdir();(installed/'settings').write_text('keep')
            update.replace_sources(root,staged,old,new)
            self.assertEqual((root/'install.sh').read_text(),'new')
            self.assertEqual((root/'report.txt.gz').read_bytes(),b'private')
            self.assertEqual((installed/'settings').read_text(),'keep')

    def test_failed_source_replace_restores_previous_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';old=write_source(root);before=(root/update.MANIFEST).read_bytes()
            staged=Path(d)/'stage';new=write_source(staged,'new')
            original=update.shutil.copy2
            def fail(source,target):
                if str(target).endswith('source-publication.json'):raise OSError('injected interruption')
                return original(source,target)
            with patch.object(update.shutil,'copy2',side_effect=fail),self.assertRaises(OSError):update.replace_sources(root,staged,old,new)
            self.assertEqual((root/'install.sh').read_text(),'old');self.assertEqual((root/update.MANIFEST).read_bytes(),before)
            self.assertFalse((root/update.JOURNAL).exists())

    def test_dirty_source_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);write_source(root)
            (root/'install.sh').write_text('edited')
            with self.assertRaises(ValueError):update.manifest(root)
            (root/'install.sh').unlink();(root/'install.sh').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):update.manifest(root)

    def test_archive_traversal_rejected(self):
        for name in ('repo/../../escape','repo/.git/config','/repo/absolute'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                with zipfile.ZipFile(root/'source.zip','w') as z:z.writestr(name,'evil')
                with self.assertRaises(ValueError):update.unpack(root/'source.zip',root/'out')

    def test_archive_manifest_and_modes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);write_source(root/'fixture')
            with zipfile.ZipFile(root/'source.zip','w') as z:
                for p in (root/'fixture').rglob('*'):
                    if p.is_file():z.write(p,'repo/'+str(p.relative_to(root/'fixture')))
            update.unpack(root/'source.zip',root/'out')
            self.assertTrue(os.access(root/'out/install.sh',os.X_OK))

    def test_new_commands_do_not_default_to_install(self):
        with patch.object(install,'present_install') as present,patch.object(diag,'run',return_value=0) as diagnose,patch.object(update,'run',return_value=0) as refresh:
            self.assertEqual(install.main(['--diag']),0);diagnose.assert_called_once()
            self.assertEqual(install.main(['--update']),0);refresh.assert_called_once();present.assert_not_called()


if __name__=='__main__':unittest.main()
