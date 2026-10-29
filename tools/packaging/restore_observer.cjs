#!/usr/bin/env node
// Bootstrap a disposable Devices page, then request debugger-free restore tests.
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
      text: document.body.innerText, viewport:{width:innerWidth,height:innerHeight,screenX,screenY,visibility:document.visibilityState},
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
      fonts:ui.fonts, missing:ui.missing, visible_text:ui.text, viewport:ui.viewport, buttons:ui.visibleButtons,
      application_count:applications.length, profile_count:profiles.length,
      scope:'Disposable onboarding only; debugger-free restore pixels are checked by the private controller'};
    fs.writeFileSync(output + '/ui-verification.json', JSON.stringify(report, null, 2) + '\n', {mode:0o600});
    if (report.checks.required_files_present && report.checks.bundled_fonts_loaded && report.checks.desktop_and_default_profile && ui.text.includes('NO, THANKS')) break;
    await sleep(1500);
  }
  async function evaluate(expression) {const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true}); if(r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;}
  async function sample(label) {
    fs.writeFileSync(output+'/capture-request',label);
    for(let i=0;i<350;i++) {
      if(fs.existsSync(output+'/capture-ack') && fs.readFileSync(output+'/capture-ack','utf8')===label) return;
      await sleep(100);
    }
    throw Error('Capture timeout');
  }

  console.log('ENV',JSON.stringify(await evaluate('({width:innerWidth,height:innerHeight,screenX,screenY,keys:Object.keys(window.electron)})')));
  const button=await evaluate(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.innerText==='NO, THANKS');const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...button});
  await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...button});
  await sleep(8000);
  for(let step=0; step<4; step++) {
  const skip=await evaluate(`(()=>{const b=[...document.querySelectorAll('*')].filter(b=>b.textContent==='SKIP').pop();if(!b)return null;const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  if(skip) {
    await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...skip});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...skip});
    await sleep(8000);
  }
  }
  async function clickText(text) {
    const point=await evaluate(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.innerText.trim()===${JSON.stringify(text)});if(!b)return null;const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    if(!point)return false;
    await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
    return true;
  }
  const check=await evaluate(`(()=>{const c=document.querySelector('input[type=checkbox]:checked');if(!c)return null;const r=c.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  if(check) {
    await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...check});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...check});
  }
  if(await clickText('CONFIRM')) await sleep(8000);
  console.log('PAGE',await evaluate('document.body.innerText'));
  const screenshot=await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(output+'/before-detach.png',Buffer.from(screenshot.data,'base64'));
  ws.close();
  await sleep(500);

  await sample('relaunch');
  await sample('initial');
  for(let i=1;i<=12;i++) {
    await sample('minimized-'+i);
    await sample('restored-'+i);
  }

  process.exit(report?.passed ? 0 : 1);
}
main().catch(error => {console.error(error); process.exit(1);});
