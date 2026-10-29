#!/usr/bin/env python3
"""Initialize the pinned UI's own fonts before its first layout under Wine.

Initialize fonts in index.html and preserve native tray errors in main.js.
Fonts stay in the locally acquired vendor archive; this tool does not ship them.
"""
import hashlib
import json
from pathlib import Path
import struct

from prepare_color_picker_fix import entries

INDEX_SHA256 = 'bc585c6b39b90d55aa3b427750fe6e5709f57e84aff3af99dce8e47e39e082ff'
CSS_SHA256 = '2fd55758c1703e8523f3a37df9b91c60e7135eac5d6e48d8e2381245f5442371'
MAIN_SHA256 = '30f8f8a7e355d3df941e6236fc184cf536165f1580f6bf87850b0e85e67834ab'
TRAY_SPAWN = ('(0,e7.spawn)(e,t,{detached:!0,windowsHide:!0,stdio:"ignore"})'
              '.once("error",function(e){nh(e,function(e){return e.setContext("startProcessMessage",'
              '{message:"Error on starting systray (process-level error)"}),e})})')
# Preserve the vendor's launch arguments and lifecycle. In particular, do not
# spawn a competing tray or restart it repeatedly after an initialization crash.
TRAY_LOGGED_SPAWN = TRAY_SPAWN.replace(
    'stdio:"ignore"',
    'stdio:["ignore","inherit","inherit"],env:Object.assign({},process.env,{WINEDEBUG:"-all,err+all"})'
) + '.once("exit",function(code,signal){console.error("Penguin LGHUB native tray exited",{code:code,signal:signal})})'
MINIMIZE_HOOK = 'Z.on("minimize",function(){Z.webContents.send(eb)})'
WINDOW_START = '(Z=new en.BrowserWindow({'
WINDOW_END = 'webSecurity:!eg()}})).on'
READY_MINIMUM = '(r<1024||n<768)&&Z.setSize(1024,768)'
QUIT_START = 'oT=function(){'
BACKEND_SHUTDOWN = 'r.logger("info","Backend shutdown message received"),r.isShuttingDown=!0'
# Hide only on an explicit quit or the agent's orderly REMOVE / notification.
# Ordinary socket failures must continue to reach the visible error UI.
HIDE_FOR_QUIT = '''function(win, reason) {
  if (process.env.PENGUIN_GHUB_GRACEFUL_CLOSE !== '1') return;
  if (win && !win.isDestroyed()) {
    // A full quit never needs this window again. Make it transparent before
    // the WM's asynchronous hide/withdraw animation can display new geometry.
    // Ordinary close-to-tray keeps its opacity so the same window can reopen.
    if (reason === 'quit-request' || reason === 'backend-shutdown') win.setOpacity(0);
    win.hide();
    console.error('Penguin LGHUB window hidden for orderly shutdown', reason);
  }
}'''
CLOSE_HIDE = '''(function(win) {
      if (process.env.PENGUIN_GHUB_GRACEFUL_CLOSE === '1')
        win.prependListener('close', () => (''' + HIDE_FOR_QUIT + ''')(win, 'window-close'));
    })(Z)'''
# A process existing is not proof that Electron reached its window. The parent
# supplies a fresh session token; stale files cannot acknowledge a later launch.
STARTUP_READY = '''(function(win) {
  const file = process.env.PENGUIN_GHUB_READY_FILE;
  const token = process.env.PENGUIN_GHUB_STARTUP_TOKEN;
  if (!file || !token) return;
  win.once('ready-to-show', () => {
    try {
      const fs = require('fs');
      fs.writeFileSync(file + '.new', JSON.stringify({token, ready: true}));
      fs.renameSync(file + '.new', file);
      console.error('Penguin LGHUB frontend ready');
    } catch (error) { console.error('Penguin LGHUB readiness report failed', error); }
  });
})(Z)'''
# Keep Chromium's native minimum inside the monitor work area. Otherwise KWin
# can constrain the window below that minimum, leaving Wine waiting for size
# acknowledgement and refusing to propagate the WM's restore to Win32.
FIT_OPTIONS = '''function(options, screen) {
  if (process.env.PENGUIN_GHUB_FIT_WORKAREA !== '1') return options;
  const area = screen.getDisplayMatching({x: options.x ?? 0, y: options.y ?? 0,
    width: options.width || 1024, height: options.height || 768}).workArea;
  if (!(area.width > 0 && area.height > 0)) return options;
  options.minWidth = Math.min(1024, area.width);
  options.minHeight = Math.min(768, area.height);
  options.width = Math.min(area.width, Math.max(options.width || 1024, options.minWidth));
  options.height = Math.min(area.height, Math.max(options.height || 768, options.minHeight));
  options.x = Math.max(area.x, Math.min(options.x ?? area.x, area.x + area.width - options.width));
  options.y = Math.max(area.y, Math.min(options.y ?? area.y, area.y + area.height - options.height));
  console.error('Penguin LGHUB work-area fit', {area, width: options.width, height: options.height});
  return options;
}'''
FIT_CONTENT = '''(function(win) {
  if (process.env.PENGUIN_GHUB_FIT_WORKAREA !== '1') return;
  const fit = () => {
    if (win.isDestroyed() || win.isMinimized() || win.webContents.isDestroyed()) return;
    const [width, height] = win.getContentSize();
    if (!(width > 0 && height > 0)) return;
    const scale = Math.min(1, width / 1024, height / 768);
    if (Math.abs(win.webContents.getZoomFactor() - scale) > 0.0001)
      win.webContents.setZoomFactor(scale);
  };
  win.on('resize', fit);
  win.on('ready-to-show', fit);
  win.on('restore', fit);
  fit();
})(Z)'''
# Electron 39's onscreen invalidate() schedules a full widget repaint. A
# restored Xwayland drawable can lose the foreign-process software output
# while Chromium still considers its existing frame current. Request damage
# after restoring/showing, including one bounded retry after remapping settles.
# Never reload the page, resize the window, or run a continuous repaint timer.
RESTORE_REPAINT = '''(function(win) {
  if (process.env.PENGUIN_GHUB_REPAINT_ON_RESTORE !== '1') return;
  let immediate = null, settled = null;
  const cancel = () => {
    clearImmediate(immediate); clearTimeout(settled);
    immediate = settled = null;
  };
  const paint = () => {
    if (win.isDestroyed() || !win.isVisible() || win.isMinimized() || win.webContents.isDestroyed()) return;
    win.webContents.invalidate();
    console.error('Penguin LGHUB restore repaint');
  };
  const restore = () => {
    cancel();
    immediate = setImmediate(() => { immediate = null; paint(); });
    settled = setTimeout(() => { settled = null; paint(); }, 250);
  };
  win.on('restore', restore);
  win.on('show', restore);
  win.on('minimize', cancel);
  win.on('hide', cancel);
  win.on('closed', cancel);
})(Z)'''
BOOTSTRAP = '''<script>
window.addEventListener('load', async () => {
  const results = [];
  try {
    const sheet = [...document.styleSheets].find(s => s.href && s.href.endsWith('/app.min.css'));
    if (!sheet) throw new Error('G HUB stylesheet is unavailable');
    const rules = [...sheet.cssRules].filter(r => r.type === CSSRule.FONT_FACE_RULE);
    if (rules.length !== 10) throw new Error('Unexpected G HUB font declarations');
    await Promise.all(rules.map(async rule => {
      const face = new FontFace(rule.style.getPropertyValue('font-family').replaceAll('"', ''),
        rule.style.getPropertyValue('src'), {weight: rule.style.getPropertyValue('font-weight') || 'normal'});
      await face.load();
      document.fonts.add(face);
      results.push({family: face.family, weight: face.weight, status: face.status});
    }));
    window.__penguinFontInitialization = {status: 'ready', faces: results};
  } catch (error) {
    window.__penguinFontInitialization = {status: 'failed', error: String(error), faces: results};
    console.error('Penguin LGHUB bundled-font initialization failed', error);
  } finally {
    const app = document.createElement('script');
    app.src = 'app.min.js';
    document.head.appendChild(app);
  }
}, {once: true});
</script>'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(original):
    _, size, _, length = struct.unpack('<4I', original[:16])
    header = json.loads(original[16:16 + length])
    payload = original[8 + size:]
    packed = list(entries(header))
    contents = {name: payload[int(item['offset']):int(item['offset']) + item['size']]
                for name, item in packed}
    for name, expected in [('index.html', INDEX_SHA256), ('app.min.css', CSS_SHA256),
                           ('main.js', MAIN_SHA256)]:
        if sha(contents[name]) != expected:
            raise ValueError('Unrecognized pinned frontend entry: ' + name)
    before = {name: sha(data) for name, data in contents.items()}
    index = contents['index.html'].decode('utf-8')
    marker = '<script defer src="app.min.js"></script>'
    if index.count(marker) != 1:
        raise ValueError('Expected exactly one deferred application script')
    contents['index.html'] = index.replace(marker, BOOTSTRAP).encode('utf-8')
    main = contents['main.js'].decode('utf-8')
    if main.count(TRAY_SPAWN) != 1:
        raise ValueError('Expected exactly one pinned native tray spawn site')
    if main.count(MINIMIZE_HOOK) != 1:
        raise ValueError('Expected exactly one pinned main-window minimize hook')
    for marker in (WINDOW_START, WINDOW_END, READY_MINIMUM, QUIT_START, BACKEND_SHUTDOWN):
        if main.count(marker) != 1:
            raise ValueError('Expected exactly one pinned main-window lifecycle marker')
    main = main.replace(TRAY_SPAWN, TRAY_LOGGED_SPAWN)
    main = main.replace(WINDOW_START, '(Z=new en.BrowserWindow((' + FIT_OPTIONS + ')({')
    main = main.replace(WINDOW_END, 'webSecurity:!eg()}},en.screen))).on')
    main = main.replace(READY_MINIMUM, '(process.env.PENGUIN_GHUB_FIT_WORKAREA!=="1"&&(' + READY_MINIMUM + '))')
    main = main.replace(QUIT_START, QUIT_START + '(' + HIDE_FOR_QUIT + ')(pH(),"quit-request");')
    main = main.replace(BACKEND_SHUTDOWN, BACKEND_SHUTDOWN +
                        ',r.connectionAgent===oj.GHUB&&(' + HIDE_FOR_QUIT + ')(r.mainWindow,"backend-shutdown")')
    contents['main.js'] = main.replace(MINIMIZE_HOOK, MINIMIZE_HOOK + ',' + FIT_CONTENT + ',' + RESTORE_REPAINT + ',' + CLOSE_HIDE + ',' + STARTUP_READY).encode('utf-8')
    changed = {'index.html', 'main.js'}
    output, offset = [], 0
    for name, item in packed:
        data = contents[name]
        item.update(offset=str(offset), size=len(data))
        if name in changed and (integrity := item.get('integrity')):
            if integrity['algorithm'] != 'SHA256' or integrity['blockSize'] <= 0:
                raise ValueError('Unsupported ASAR integrity format')
            block = integrity['blockSize']
            integrity.update(hash=sha(data), blocks=[sha(data[i:i + block]) for i in range(0, len(data), block)])
        output.append(data)
        offset += len(data)
    encoded = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    padded = (len(encoded) + 3) & ~3
    body = b''.join(output)
    for name, item in packed:
        data = body[int(item['offset']):int(item['offset']) + item['size']]
        if sha(data) != (sha(contents[name]) if name in changed else before[name]):
            raise ValueError('Unexpected ASAR content change: ' + name)
    return (struct.pack('<4I', 4, 8 + padded, 4 + padded, len(encoded)) + encoded +
            bytes(padded - len(encoded)) + body), len(packed)


def prepare(source: Path, output: Path, expected_sha256: str):
    original = source.read_bytes()
    if source.is_symlink() or sha(original) != expected_sha256:
        raise ValueError('Frontend source archive changed')
    result, count = transform(original)
    with output.open('xb') as stream:
        stream.write(result)
    return {'source_sha256': sha(original), 'output_sha256': sha(result),
            'changed_entries': ['index.html', 'main.js'], 'packed_entries_verified': count,
            'startup_protocol': 1}
