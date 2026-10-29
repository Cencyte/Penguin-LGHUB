import json
from pathlib import Path
import struct
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/wine'))
import prepare_frontend_startup as m


def archive(files):
    header, offset = {'files': {}}, 0
    for name, data in files.items():
        header['files'][name] = {'offset': str(offset), 'size': len(data), 'integrity': {
            'algorithm': 'SHA256', 'blockSize': 32, 'hash': m.sha(data),
            'blocks': [m.sha(data[i:i+32]) for i in range(0, len(data), 32)]}}
        offset += len(data)
    encoded = json.dumps(header).encode()
    padded = (len(encoded) + 3) & ~3
    return struct.pack('<4I', 4, 8+padded, 4+padded, len(encoded)) + encoded + bytes(padded-len(encoded)) + b''.join(files.values())


class FrontendStartupTests(unittest.TestCase):
    def test_readiness_is_atomic_and_only_reported_after_window_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / 'frontend-ready.json')
            script = '''
const assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const fs = require('fs');
console.error = () => {};
const Z = new EventEmitter();
process.env.PENGUIN_GHUB_READY_FILE = ''' + json.dumps(output) + ''';
process.env.PENGUIN_GHUB_STARTUP_TOKEN = 'current-session';
''' + m.STARTUP_READY + ''';
assert.equal(fs.existsSync(process.env.PENGUIN_GHUB_READY_FILE), false);
Z.emit('ready-to-show');
assert.deepEqual(JSON.parse(fs.readFileSync(process.env.PENGUIN_GHUB_READY_FILE)), {token:'current-session',ready:true});
assert.equal(fs.existsSync(process.env.PENGUIN_GHUB_READY_FILE+'.new'), false);
'''
            subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_window_close_hides_before_renderer_cleanup_and_preserves_prevention(self):
        script = '''
const assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const Z = new EventEmitter();
console.error = () => {};
let events=[], prevented=false;
Z.isDestroyed=()=>false; Z.hide=()=>events.push('hide');
Z.on('close', event=>{events.push('renderer-cleanup');event.preventDefault();});
process.env.PENGUIN_GHUB_GRACEFUL_CLOSE='1';
''' + m.CLOSE_HIDE + ''';
Z.emit('close',{preventDefault:()=>prevented=true});
assert.deepEqual(events,['hide','renderer-cleanup']); assert.equal(prevented,true);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_orderly_close_hides_before_disconnect_without_destroying_window(self):
        script = '''
const assert = require('node:assert/strict');
console.error = () => {};
let events=[], destroyed=false;
const win={isDestroyed:()=>destroyed,hide:()=>events.push('hide'),setOpacity:x=>events.push(['opacity',x])};
const hide=(''' + m.HIDE_FOR_QUIT + ''');
delete process.env.PENGUIN_GHUB_GRACEFUL_CLOSE;
hide(win); assert.deepEqual(events,[]);
process.env.PENGUIN_GHUB_GRACEFUL_CLOSE='1';
hide(win,'quit-request'); events.push('disconnect');
assert.deepEqual(events,[['opacity',0],'hide','disconnect']);
destroyed=true; hide(win); hide(null);
assert.equal(events.length,3);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_native_window_fits_small_workarea_and_negative_monitor_coordinates(self):
        script = '''
const assert = require('node:assert/strict');
console.error = () => {};
const fit = (''' + m.FIT_OPTIONS + ''');
const original = {x:-1100, y:40, width:1024, height:768, minWidth:1024, minHeight:768, frame:false};
let area = {x:-1280,y:0,width:1280,height:743};
const screen = {getDisplayMatching: () => ({workArea:area})};
delete process.env.PENGUIN_GHUB_FIT_WORKAREA;
assert.deepEqual(fit({...original},screen), original);
process.env.PENGUIN_GHUB_FIT_WORKAREA='1';
const small=fit({...original},screen);
assert.equal(small.height,743); assert.equal(small.minHeight,743);
assert.equal(small.y,0); assert.equal(small.x,-1100); assert.equal(small.frame,false);
area={x:0,y:0,width:800,height:600};
const tiny=fit({...original},screen);
assert.deepEqual([tiny.x,tiny.y,tiny.width,tiny.height,tiny.minWidth,tiny.minHeight],[0,0,800,600,800,600]);
area={x:-1280,y:0,width:1280,height:1024};
assert.deepEqual(fit({...original},screen),original);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_content_scaling_preserves_full_logical_ui_and_skips_minimized_geometry(self):
        script = '''
const assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const Z = new EventEmitter();
let minimized=false, destroyed=false, size=[1024,743], zoom=1, changes=0;
Z.isMinimized=()=>minimized; Z.isDestroyed=()=>destroyed; Z.getContentSize=()=>size;
Z.webContents={isDestroyed:()=>destroyed,getZoomFactor:()=>zoom,setZoomFactor:x=>{zoom=x;changes++;}};
process.env.PENGUIN_GHUB_FIT_WORKAREA='1';
''' + m.FIT_CONTENT + ''';
assert.equal(zoom,743/768); assert.equal(size[1]/zoom,768);
Z.emit('resize'); assert.equal(changes,1);
minimized=true; size=[160,31]; Z.emit('resize'); assert.equal(changes,1);
minimized=false; size=[1280,900]; Z.emit('restore'); assert.equal(zoom,1);
destroyed=true; size=[800,600]; Z.emit('resize'); assert.equal(changes,2);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_repaint_hook_is_inert_without_wayland_opt_in(self):
        script = '''
const assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const Z = new EventEmitter();
delete process.env.PENGUIN_GHUB_REPAINT_ON_RESTORE;
''' + m.RESTORE_REPAINT + ''';
assert.deepEqual(Z.eventNames(), []);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_restore_requests_bounded_repaints_and_cancels_hidden_windows(self):
        script = '''
const assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const Z = new EventEmitter();
let minimized = false, visible = true, destroyed = false, paints = 0;
Z.isMinimized = () => minimized; Z.isVisible = () => visible;
Z.isDestroyed = () => destroyed;
Z.webContents = {isDestroyed: () => destroyed, invalidate: () => paints++};
process.env.PENGUIN_GHUB_REPAINT_ON_RESTORE = '1';
console.error = () => {};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
''' + m.RESTORE_REPAINT + ''';
(async () => {
  Z.emit('restore'); Z.emit('show');
  await sleep(300); assert.equal(paints, 2);
  await sleep(300); assert.equal(paints, 2); // no perpetual timer
  Z.emit('restore'); minimized = true; Z.emit('minimize');
  await sleep(300); assert.equal(paints, 2);
  minimized = false; Z.emit('restore'); visible = false; Z.emit('hide');
  await sleep(300); assert.equal(paints, 2);
  visible = true; Z.emit('show'); destroyed = true; Z.emit('closed');
  await sleep(300); assert.equal(paints, 2);
})().catch(e => {console.error(e); process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_tray_logging_keeps_one_vendor_launch_and_reports_exit(self):
        # Execute the transformed call: do not merely check for a matching
        # substring while accidentally changing arguments or spawning twice.
        script = '''
const assert = require('node:assert/strict');
const EventEmitter = require('node:events');
let calls = 0, reports = [], child = new EventEmitter();
const e = 'native-tray.exe', t = ['--background'];
const e7 = {spawn: (exe, args, options) => {
  calls++;
  assert.equal(exe, e); assert.deepEqual(args, t);
  assert.equal(options.detached, true); assert.equal(options.windowsHide, true);
  assert.deepEqual(options.stdio, ['ignore','inherit','inherit']);
  assert.equal(options.env.WINEDEBUG, '-all,err+all');
  assert.equal(options.env.PENGUIN_SENTINEL, 'preserved');
  return child;
}};
const nh = () => {};
console.error = (...args) => reports.push(args);
process.env.PENGUIN_SENTINEL = 'preserved';
''' + m.TRAY_LOGGED_SPAWN + ''';
child.emit('exit', 42, null);
assert.equal(calls, 1);
assert.deepEqual(reports, [['Penguin LGHUB native tray exited', {code:42,signal:null}]]);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, timeout=10)

    def test_font_and_tray_changes_preserve_other_entries_and_integrity(self):
        files = {'index.html': b'<head><script defer src="app.min.js"></script></head>',
                 'app.min.css': b'fixture stylesheet', 'font.otf': bytes(range(255)),
                 'app.min.js': b'previously patched picker',
                 'main.js': (m.TRAY_SPAWN + ',' + m.MINIMIZE_HOOK + ';' + m.WINDOW_START + m.WINDOW_END + ';' + m.READY_MINIMUM + ';' + m.QUIT_START + m.BACKEND_SHUTDOWN).encode()}
        original = archive(files)
        with patch.object(m, 'INDEX_SHA256', m.sha(files['index.html'])), patch.object(m, 'CSS_SHA256', m.sha(files['app.min.css'])), patch.object(m, 'MAIN_SHA256', m.sha(files['main.js'])):
            result, count = m.transform(original)
        self.assertEqual(count, 5)
        _, size, _, length = struct.unpack('<4I', result[:16])
        header = json.loads(result[16:16+length]); body = result[8+size:]
        for name, item in m.entries(header):
            data = body[int(item['offset']):int(item['offset'])+item['size']]
            self.assertEqual(m.sha(data), item['integrity']['hash'])
            self.assertEqual(item['integrity']['blocks'], [m.sha(data[i:i+32]) for i in range(0,len(data),32)])
            if name == 'main.js':
                self.assertIn((m.TRAY_LOGGED_SPAWN + ',' + m.MINIMIZE_HOOK + ',' + m.FIT_CONTENT + ',' + m.RESTORE_REPAINT).encode(), data)
                self.assertIn(b'"inherit","inherit"', data)
                self.assertIn(b'native tray exited', data)
            elif name != 'index.html':
                self.assertEqual(data, files[name])
            else:
                self.assertIn(b"window.addEventListener('load'", data)
                self.assertIn(b'await face.load()', data)
                self.assertIn(b"app.src = 'app.min.js'", data)
                self.assertNotIn(b'<script defer src=', data)
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            m.transform(original)

    def test_changed_source_is_refused_without_writing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'source', Path(tmp)/'output'
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'archive changed'):
                m.prepare(source, output, '0'*64)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
