"""Local, minimized support report. Never uploads or changes the installation."""
import base64
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import struct
import subprocess
import time
import uuid

import device_compatibility
from host_policy import os_identity, package_query
from installer_ui import Presentation, STYLE
from windows_media_acquire import ROOT


class Sanitizer:
    """SoS-style consistent identities, plus minimization before collection."""
    def __init__(self):
        self.mapping = {}
        self.identities = {str(Path.home()): '<HOME>', socket.gethostname(): '<HOST>',
                           os.environ.get('USER', ''): '<USER>'}

    def alias(self, kind, value):
        key = (kind, value)
        if key not in self.mapping:
            self.mapping[key] = f'<{kind}-{1+sum(k[0]==kind for k in self.mapping)}>'
        return self.mapping[key]

    def text(self, value):
        text = re.sub(r'\x1b\].*?(?:\x07|\x1b\\)', '', str(value), flags=re.S)
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
        text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)
        for original, replacement in sorted(self.identities.items(), key=lambda p: -len(p[0])):
            if original:
                text = re.sub(r'(?<![\w])'+re.escape(original)+r'(?![\w])', lambda _: replacement, text)
        text = re.sub(r'(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b', lambda m:self.alias('MAC',m[0]), text)
        def address(match):
            try:
                ipaddress.ip_address(match[0])
                return self.alias('IP', match[0])
            except ValueError:
                return match[0]
        text = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', address, text)
        text = re.sub(r'(?<![\w:])[0-9a-fA-F]*:[0-9a-fA-F:]+(?:%\w+)?', address, text)
        text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '<EMAIL>', text)
        text = re.sub(r'(?i)(bearer\s+)[\w.+=/-]+', r'\1<SECRET>', text)
        text = re.sub(r'(?i)((?:password|token|secret|serial(?:number)?|cookie|authorization|clientid)["\s]*[:=]["\s]*)[^\s,;}]+', r'\1<REDACTED>', text)
        text = re.sub(r'/home/[^/\s]+', '/home/<USER>', text)
        return text

    def clean(self, value):
        if isinstance(value, dict):
            return {k: self.clean(v) for k,v in value.items()
                    if not re.search(r'(?i)serial|token|password|secret|cookie|authorization|hostname|username|clientid',k)}
        if isinstance(value, list):return [self.clean(v) for v in value]
        if isinstance(value, str):return self.text(value)
        return value


def bounded_json(path, limit=16*1024**2):
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError('Unsafe or oversized diagnostic input')
    return json.loads(path.read_text())


def owned_agent(directory, proc=Path('/proc')):
    """Verify a same-user agent and listener in this exact prefix.

    Wine keeps the listening socket in wineserver, not necessarily the PE
    agent's Unix process. Both processes must belong to the requested prefix.
    """
    prefix = directory / 'desktop/prefix'
    listeners = set()
    for name in ('net/tcp', 'net/tcp6'):
        try:
            for line in (proc/name).read_text().splitlines()[1:]:
                cols=line.split()
                if cols[1].split(':')[-1]=='2332' and cols[3]=='0A':listeners.add(cols[9])
        except OSError:pass
    agent = None
    owned_listener = False
    for path in proc.glob('[0-9]*'):
        try:
            if path.stat().st_uid!=os.getuid():continue
            command=(path/'cmdline').read_bytes().lower()
            is_agent=b'lghub_agent.exe' in command
            is_server=Path(os.readlink(path/'exe')).name=='wineserver'
            if not (is_agent or is_server):continue
            env=(path/'environ').read_bytes().split(b'\0')
            if os.fsencode('WINEPREFIX='+str(prefix)) not in env:continue
            if is_agent:agent=int(path.name)
            if any(os.readlink(fd) in {'socket:['+n+']' for n in listeners} for fd in (path/'fd').iterdir()):
                owned_listener=True
        except (OSError,ValueError):continue
    return agent if owned_listener else None


def device_response():
    """One bounded read-only request, no profiles, applications or input routes."""
    def exact(stream,size):
        result=b''
        while len(result)<size:
            chunk=stream.read(size-len(result))
            if not chunk:raise RuntimeError('Agent closed the connection')
            result+=chunk
        return result
    nonce=base64.b64encode(os.urandom(16)).decode()
    message_id=str(uuid.uuid4())
    with socket.create_connection(('127.0.0.1',9010),timeout=5) as connection:
        connection.sendall((f'GET / HTTP/1.1\r\nHost: 127.0.0.1:9010\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: json\r\nSec-WebSocket-Key: {nonce}\r\n\r\n').encode())
        with connection.makefile('rb') as stream:
            header=b''
            while not header.endswith(b'\r\n\r\n'):
                header+=exact(stream,1)
                if len(header)>8192:raise ValueError('Oversized WebSocket handshake')
            expected=base64.b64encode(hashlib.sha1((nonce+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            if not header.startswith(b'HTTP/1.1 101 ') or expected not in header:
                raise ValueError('Invalid agent WebSocket handshake')
            def send(op,data):
                mask=os.urandom(4);length=len(data)
                frame=bytes([0x80|op,0x80|length]) if length<126 else bytes([0x80|op,0xfe])+struct.pack('!H',length)
                connection.sendall(frame+mask+bytes(v^mask[i%4] for i,v in enumerate(data)))
            send(1,json.dumps({'msgId':message_id,'verb':'GET','path':'/devices/list','origin':'frontend'}).encode())
            payload=b'';deadline=time.monotonic()+8
            for _ in range(64):
                if time.monotonic()>deadline:raise TimeoutError('Agent response timeout')
                connection.settimeout(max(.1,deadline-time.monotonic()))
                first,second=exact(stream,2);length=second&127
                if second&128:raise ValueError('Unexpected masked server frame')
                if length==126:length=struct.unpack('!H',exact(stream,2))[0]
                elif length==127:length=struct.unpack('!Q',exact(stream,8))[0]
                if length+len(payload)>1024**2:raise ValueError('Oversized agent response')
                data=exact(stream,length);op=first&15
                if op==9:
                    if length>125:raise ValueError('Oversized ping')
                    send(10,data);continue
                if op==8:raise RuntimeError('Agent closed the connection')
                if op not in (0,1):continue
                payload+=data
                if first&128:
                    result=json.loads(payload);payload=b''
                    if result.get('msgId')==message_id:return result
            raise TimeoutError('No matching agent response')


def minimal_devices(response):
    # Only the top-level device records are relevant. Nested capabilities,
    # configurations, names, identifiers and active interfaces can be personal.
    payload=response.get('payload',{})
    records=payload.get('deviceInfos') if isinstance(payload,dict) else None
    if not isinstance(records,list):raise ValueError('Unexpected device list format')
    allowed={'displayName','extendedDisplayName','deviceModel','deviceBaseModel',
             'deviceType','deviceFamily','state','resourcesAvailable',
             'connectionType','displayConnectionType','hasWirelessInterface','onboardMode'}
    return [{k:v for k,v in record.items() if k in allowed and isinstance(v,(str,int,bool))}
            for record in records[:256] if isinstance(record,dict)]


def error_summary(directory):
    counts={};files=0
    folder=directory/'desktop/logs'
    if folder.is_symlink():raise ValueError('Symlinked logs directory')
    state=Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'penguin-lghub'
    installation_logs=[] if state.is_symlink() else sorted(state.glob('install-*.log'),key=lambda p:p.stat().st_mtime,reverse=True)[:2]
    candidates=sorted(folder.glob('*desktop.log'), key=lambda p:p.stat().st_mtime, reverse=True)[:3]+installation_logs
    for path in candidates:
        if path.is_symlink():continue
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-65536));text=stream.read(65536).decode(errors='replace')
        files+=1
        # Canonical excerpts exclude arbitrary application messages and paths.
        for match in re.findall(r'PermissionError|FileNotFoundError|TimeoutError|ConnectionRefusedError|\[Errno \d+\]|err:[a-zA-Z0-9_]+|STATUS_ACCESS_DENIED|DEVICE_NOT_CONNECTED',text):
            counts[match]=counts.get(match,0)+1
    return {'recent_logs_inspected':files,'bounded_error_excerpts':counts,'raw_logs_included':False}


def run(directory, verbose=False, project=ROOT):
    directory=Path(directory).expanduser().absolute()
    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    target=project/f'Penguin-LGHUB-diagnostics-{stamp}.txt.gz'
    running=owned_agent(directory)
    sanitizer=Sanitizer();report={'schema':1,'installer':STYLE['installer_version'],'checks':[]}
    # The single gzip is the only diagnostic artifact. Presentation's log is
    # private temporary output; sanitized content is added to the final report.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='penguin-diag-') as temporary:
        ui=Presentation(Path(temporary)/'steps.log',verbose)
        def step(category,description,operation):
            try:
                result=operation();status='OK'
            except (OSError,ValueError,RuntimeError,KeyError,TypeError,subprocess.SubprocessError) as e:
                result={'error':sanitizer.text(str(e))};status='FAIL'
            report['checks'].append({'step':description,'status':status,'result':sanitizer.clean(result)})
            ui.step(category,description,status)
            ui.output(json.dumps(sanitizer.clean(result),indent=2)+'\n')
            return result
        try:
            if not running:ui.notice('Open Penguin-LGHUB first to include live application diagnostics. Live checks will report FAIL while it is not running.',error=True)
            ui.header(report_path=target)
            with ui.activity():
                step('System','Record operating system and desktop versions',lambda:{'os':os_identity(),'kernel':platform.release(),'architecture':platform.machine(),'session':os.environ.get('XDG_SESSION_TYPE','unknown')})
                def packages():
                    command=package_query(os_identity())
                    if not command:return {'package_inventory':'No package query defined for this OS'}
                    p=subprocess.run(command,capture_output=True,text=True,timeout=12)
                    return {'versions':p.stdout[:16384],'query_exit':p.returncode}
                step('Tools','Record relevant installed packages',packages)
                devices=step('System','Inspect Logitech USB and HID access',lambda:[x for x in device_compatibility.discover() if x['id'].startswith('046d:') or device_compatibility.G_NAME.search(x['name'])])
                def installation():
                    import installation_state
                    state=installation_state.load(directory)
                    return {k:state.get(k) for k in ('project_version','installed','completed','transaction')}
                step('Verify','Inspect installation ownership and completion',installation)
                def resources():
                    current=bounded_json(directory/'desktop/prefix/drive_c/ProgramData/LGHUB/current.json')
                    names=[d['name'] for d in current['depots'] if d.get('state')=='PRESENT']
                    result=device_compatibility.inspect(devices=devices if isinstance(devices,list) else [],available=names)
                    if result['errors']:raise ValueError('; '.join(result['errors']))
                    return {'installed_depot_count':len(names),'coverage':result}
                step('Verify','Check installed device-resource coverage',resources)
                def integrity():
                    config=bounded_json(directory/'desktop/desktop.json')
                    import desktop_runtime
                    desktop_runtime.verify(config,directory/'desktop')
                    return {'private_runtime_and_files':'verified'}
                step('Wine','Verify the installed private runtime',integrity)
                def live():
                    if not owned_agent(directory):raise RuntimeError('Penguin-LGHUB is not running with a verified owned agent')
                    response=device_response()
                    if response.get('result',{}).get('code')!='SUCCESS':raise RuntimeError('G HUB device query did not succeed')
                    return minimal_devices(response)
                step('G HUB','Query running G HUB device recognition',live)
                def live_access():
                    if not owned_agent(directory):raise RuntimeError('Penguin-LGHUB is not running with a verified owned agent')
                    if not isinstance(devices,list):raise RuntimeError('Host device discovery failed')
                    denied=[x['node'] for d in devices if isinstance(d,dict) for x in d.get('interfaces',[]) if not(x['readable'] and x['writable'])]
                    if denied:raise RuntimeError('Device interfaces lack read/write access: '+', '.join(denied))
                    return {'accessible_interfaces':sum(len(d.get('interfaces',[])) for d in devices if isinstance(d,dict))}
                step('G HUB','Check live session device access',live_access)
                step('Verify','Summarize recent application errors',lambda:error_summary(directory))
                ui.step('Tools','Sanitize and compress the local support report')
                report['privacy']='Local report only. No upload, serial numbers, profiles, application lists, input events or raw logs. Review before sharing; compression is not encryption.'
                text='Penguin-LGHUB support report\n'+json.dumps(sanitizer.clean(report),indent=2,ensure_ascii=False)+'\n'
                with target.open('xb') as out:
                    try:
                        os.chmod(target,0o600)
                        with gzip.GzipFile(filename='Penguin-LGHUB-diagnostics.txt',mode='wb',fileobj=out,mtime=0) as archive:archive.write(text.encode())
                    except BaseException:
                        target.unlink(missing_ok=True)
                        raise
            ui.notice('Report saved: '+str(target))
            ui.notice('Review the decompressed text before sharing. Nothing was uploaded.')
            return int(any(c['status']=='FAIL' for c in report['checks']))
        finally:ui.close()
