#!/usr/bin/env node
// Passive, user-paced recording of G HUB's actual renderer. No input injection,
// text/keystroke capture, screenshots, device writes or remote network traffic.
const fs = require('node:fs');
const args = process.argv.slice(2);
if (args.length !== 3 || !/^\d+$/.test(args[0]) || typeof WebSocket === 'undefined') {
  console.error('Usage (Node 22+): color_picker_monitor.cjs PORT PREFIX NEW_REPORT.jsonl');
  process.exit(2);
}
const [port, prefix, output] = args;
const fd = fs.openSync(output, 'wx', 0o600);
const write = value => fs.writeSync(fd, JSON.stringify({recorded_at:new Date().toISOString(), ...value}) + '\n');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function owned() {
  return fs.readdirSync('/proc').filter(p=>/^\d+$/.test(p)).some(pid => {
    try {
      const env=fs.readFileSync(`/proc/${pid}/environ`).toString().split('\0');
      const cmd=fs.readFileSync(`/proc/${pid}/cmdline`).toString().split('\0');
      return env.includes(`WINEPREFIX=${prefix}`) && cmd.some(a=>a===`--remote-debugging-port=${port}`) && /lghub\.exe$/i.test(cmd[0].trim());
    } catch { return false; }
  });
}
const observer = `(() => {
  if (window.__ghubDragMetricsV2) return;
  window.__ghubDragMetricsV2=true;
  let active=null, previous=null, frame=null;
  const append=(list,value)=>{if(list.length<4096)list.push(value)};
  const stats=list=>{const s=[...list].sort((a,b)=>a-b);return {n:s.length,median:s[Math.floor(s.length*.5)]??null,p95:s[Math.floor(s.length*.95)]??null,max:s.at(-1)??null}};
  function picker(target) {
    const white=target.closest?.('.saturation-white') || target.closest?.('.saturation-container')?.querySelector('.saturation-white');
    const hue=target.closest?.('.hue-vertical') || target.closest?.('.hue-container')?.querySelector('.hue-vertical');
    const area=white || hue;
    const dot=white?[...white.children].find(e=>!e.classList.contains('saturation-black')&&e.style.position==='absolute'):
      hue?[...hue.children].find(e=>e.style.position==='absolute'):null;
    if(!area || !dot)return null;
    return {element:dot,rect:area.getBoundingClientRect(),kind:white?'saturation':'hue',
      updates:0,changes:[],last:null,lastChange:performance.now(),lastMove:0,maxStationary:0,
      expected:null,maxError:0};
  }
  function position(p) {
    const s=p.element.style;
    if(!s.top.endsWith('%') || (p.kind==='saturation'&&!s.left.endsWith('%')))return null;
    return {x:p.kind==='saturation'?parseFloat(s.left):null,y:parseFloat(s.top)};
  }
  function sampleDot(t) {
    const p=active?.picker;if(!p)return;
    const now=position(p);if(!now)return;
    if(p.last&&(p.last.x!==now.x||p.last.y!==now.y)){
      p.updates++;append(p.changes,t-p.lastChange);p.lastChange=t;
    }
    p.last=now;
    if(p.expected&&t-p.lastMove<100){
      const error=Math.hypot(p.kind==='saturation'?now.x-p.expected.x:0,now.y-p.expected.y);
      p.maxError=Math.max(p.maxError,error);
      if(error>1)p.maxStationary=Math.max(p.maxStationary,t-p.lastChange);
    }
  }
  const tick=t=>{if(!active)return;if(previous!==null)append(active.frames,t-previous);previous=t;sampleDot(t);frame=requestAnimationFrame(tick)};
  new PerformanceObserver(list=>{if(active)for(const e of list.getEntries())append(active.tasks,e.duration)}).observe({type:'longtask'});
  const finish=reason=>{if(!active)return;sampleDot(performance.now());const a=active,p=a.picker;active=null;cancelAnimationFrame(frame);
    window.__ghubDragReportV2(JSON.stringify({kind:'drag',reason,duration_ms:performance.now()-a.start,
    target:a.target,pointer_events:a.count,pointer_event_age_ms:stats(a.ages),frame_intervals_ms:stats(a.frames),long_tasks_ms:stats(a.tasks),
    picker:p?{kind:p.kind,dot_updates:p.updates,dot_change_intervals_ms:stats(p.changes),
      longest_stationary_while_pointer_moving_ms:p.maxStationary,max_pointer_dot_distance_percent:p.maxError,
      final_dot:p.last,last_pointer_position:p.expected}:null,
    note:'Frame callbacks measure scheduling, not final display scanout or peripheral lighting.'}));};
  document.addEventListener('pointerdown',e=>{if(e.button!==0)return;finish('next-drag');
    active={start:performance.now(),count:0,ages:[],frames:[],tasks:[],picker:picker(e.target),target:{tag:e.target.tagName,id:e.target.id||null}};
    previous=null;frame=requestAnimationFrame(tick)},true);
  document.addEventListener('pointermove',e=>{if(active){active.count++;append(active.ages,performance.now()-e.timeStamp);
    const p=active.picker;if(p){const clamp=v=>Math.min(100,Math.max(0,v));p.lastMove=performance.now();
      p.expected={x:clamp(100*(e.clientX-p.rect.left)/p.rect.width),y:clamp(100*(e.clientY-p.rect.top)/p.rect.height)};
    }}},true);
  document.addEventListener('pointerup',e=>{if(e.button===0)finish('release')},true);
  window.addEventListener('blur',()=>finish('blur'));
  window.addEventListener('pagehide',()=>finish('pagehide'));
})()`;
let stopping = false;
for (const signal of ['SIGTERM','SIGINT']) process.on(signal,()=>{stopping=true});
async function main() {
  write({kind:'start',port:Number(port),prefix,passive:true});
  const attached = new Map();
  let everOwned=false, misses=0;
  try {
    while(!stopping) {
      if (!owned()) {
        if (everOwned || ++misses>120) break;
        await sleep(500); continue;
      }
      everOwned=true;
      let pages;
      try { pages=await (await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(2000)})).json() }
      catch { await sleep(500); continue; }
      for(const page of pages.filter(p=>p.type==='page' && p.webSocketDebuggerUrl)) {
        if(attached.has(page.id)) continue;
        const url=new URL(page.webSocketDebuggerUrl);
        if(url.hostname!=='127.0.0.1' && url.hostname!=='localhost') throw Error('Non-loopback debugger endpoint');
        if(url.port!==port) throw Error('Debugger port mismatch');
        const ws=new WebSocket(url);
        attached.set(page.id,ws);
        let id=0;const pending=new Map();
        const send=(method,params={})=>new Promise((resolve,reject)=>{
          const key=++id;
          const timer=setTimeout(()=>{pending.delete(key);reject(Error('CDP timeout: '+method))},5000);
          pending.set(key,{resolve,reject,timer});ws.send(JSON.stringify({id:key,method,params}));
        });
        ws.onmessage=e=>{
          const m=JSON.parse(e.data);
          if(m.id && pending.has(m.id)) {const p=pending.get(m.id);pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)}
          if(m.method==='Runtime.bindingCalled' && m.params.name==='__ghubDragReportV2') {
            try {write({...JSON.parse(m.params.payload),page:page.id})} catch(error) {write({kind:'error',error:String(error)})}
          }
        };
        ws.onopen=async()=>{
          try {
            await send('Runtime.enable'); await send('Page.enable');
            await send('Runtime.addBinding',{name:'__ghubDragReportV2'});
            await send('Page.addScriptToEvaluateOnNewDocument',{source:observer});
            const result=await send('Runtime.evaluate',{expression:observer});
            if(result.exceptionDetails) throw Error('Renderer observer failed');
            write({kind:'attached',page:page.id});
          } catch(error) {write({kind:'error',error:String(error)});ws.close()}
        };
        ws.onclose=()=>{attached.delete(page.id);for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('CDP closed'))}pending.clear()};
        ws.onerror=()=>write({kind:'connection_error',page:page.id});
      }
      await sleep(1000);
    }
  } finally {for(const ws of attached.values())ws.close();write({kind:'stopped'});fs.closeSync(fd)}
}
main().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});
