#!/usr/bin/env node
// Observe first-run startup in the disposable linux_ui_probe namespace only.
const fs = require('node:fs');
const {randomUUID} = require('node:crypto');
const [output] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const desktopId = '420fd454-0c36-499d-bde4-146823b16147';
const deadline = Date.now() + 120000;

async function agentState() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket('ws://127.0.0.1:9010', 'json');
    const pending = new Map(['/applications', '/profiles'].map(path => [randomUUID(), path]));
    const result = {};
    const timer = setTimeout(() => {ws.close(); reject(Error('Agent response timeout'));}, 5000);
    ws.onopen = () => {for (const [msgId, path] of pending) ws.send(JSON.stringify({msgId, path, verb:'GET', origin:'frontend'}));};
    ws.onerror = () => {clearTimeout(timer); reject(Error('Agent unavailable'));};
    ws.onmessage = event => {
      let message;
      try {message = JSON.parse(event.data);} catch {return;}
      const path = pending.get(message.msgId);
      if (!path) return;
      result[path] = message;
      pending.delete(message.msgId);
      if (!pending.size) {clearTimeout(timer); ws.close(); resolve(result);}
    };
  });
}

async function main() {
  if (process.env.PENGUIN_ISOLATED_UI_PROBE !== '1') throw Error('Run through linux_ui_probe.py');
  let page;
  while (Date.now() < deadline) {
    try {
      page = (await (await fetch('http://127.0.0.1:9224/json/list', {signal:AbortSignal.timeout(1000)})).json())
        .find(p => p.type === 'page' && p.url.endsWith('/index.html'));
      if (page) break;
    } catch {}
    await sleep(250);
  }
  if (!page) throw Error('No G HUB renderer');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const pending = new Map();
  let id = 0;
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const key = ++id;
    const timer = setTimeout(() => {pending.delete(key); reject(Error(method + ' timeout'));}, 10000);
    pending.set(key, {resolve, reject, timer});
    ws.send(JSON.stringify({id:key, method, params}));
  });
  ws.onmessage = event => {
    const message = JSON.parse(event.data), call = pending.get(message.id);
    if (!call) return;
    clearTimeout(call.timer); pending.delete(message.id);
    message.error ? call.reject(Error(message.error.message)) : call.resolve(message.result);
  };
  await new Promise((resolve, reject) => {ws.onopen = resolve; ws.onerror = reject;});
  await send('Runtime.enable');
  await send('Page.enable');
  let report;
  while (Date.now() < deadline) {
    const response = await send('Runtime.evaluate', {expression:`(async () => ({
      fonts: window.__penguinFontInitialization || null,
      missing: await window.electron.checkEssentialFiles(),
      text: document.body.innerText,
      visibleButtons: [...document.querySelectorAll('button')].filter(b => {
        const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0;
      }).map(b => b.innerText)
    }))()`, returnByValue:true, awaitPromise:true});
    if (response.exceptionDetails) throw Error(response.exceptionDetails.text);
    const ui = response.result.value;
    let agent = {};
    try {agent = await agentState();} catch {}
    const applications = agent['/applications']?.payload?.applications || [];
    const profiles = agent['/profiles']?.payload?.profiles || [];
    const checks = {
      required_files_present: Array.isArray(ui.missing) && ui.missing.length === 0,
      bundled_fonts_loaded: ui.fonts?.status === 'ready' && ui.fonts.faces.length === 10 && ui.fonts.faces.every(f => f.status === 'loaded'),
      desktop_and_default_profile: applications.some(a => a.applicationId === desktopId && a.name === 'APPLICATION_NAME_DESKTOP') &&
        profiles.some(p => p.applicationId === desktopId && p.name === 'PROFILE_NAME_DEFAULT'),
      first_run_prompt_readable: ui.text.includes('HELP US MAKE G HUB BETTER') && ui.visibleButtons.includes('NO, THANKS') && ui.visibleButtons.includes('YES, PLEASE')
    };
    report = {schema:1, checks, passed:Object.values(checks).every(Boolean),
      fonts:ui.fonts, missing:ui.missing, visible_text:ui.text,
      application_count:applications.length, profile_count:profiles.length,
      scope:'Isolated first-run UI; no input injection, physical devices, tray or host GPU validation'};
    fs.writeFileSync(output + '/ui-verification.json', JSON.stringify(report, null, 2) + '\n', {mode:0o600});
    if (report.passed) break;
    await sleep(1500);
  }
  const screenshot = await send('Page.captureScreenshot', {format:'png'});
  fs.writeFileSync(output + '/first-run.png', Buffer.from(screenshot.data, 'base64'), {mode:0o600});
  ws.close();
  process.exit(report?.passed ? 0 : 1);
}
main().catch(error => {console.error(error); process.exit(1);});
