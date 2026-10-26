#!/usr/bin/env node
// Uses the native API behind G HUB's automatic-update checkbox. No update,
// download, install, reset, purge, device or profile routes are invoked.
const {randomUUID} = require('node:crypto');
const {writeFileSync} = require('node:fs');
const args = process.argv.slice(2);
const disable = args[0] === '--disable';
const output = disable ? args[1] : args[0];
if (!output || args.length !== (disable ? 2 : 1) || typeof WebSocket === 'undefined') {
  console.error('Usage (Node 22+): node update_policy.cjs [--disable] NEW_REPORT.json');
  process.exit(2);
}
const report = {recorded_at:new Date().toISOString(), endpoint:'ws://127.0.0.1:9010',
  route:'/updates/periodic_check', disable_requested:disable};
// Reserve the evidence file before changing preferences; never overwrite it.
writeFileSync(output, JSON.stringify(report, null, 2) + '\n', {flag:'wx', mode:0o600});
const ws = new WebSocket(report.endpoint, 'json');
const pending = new Map();
ws.onmessage = event => {
  let reply;
  try { reply = JSON.parse(event.data); } catch { return; }
  const resolve = pending.get(reply.msgId);
  if (resolve) { pending.delete(reply.msgId); resolve(reply); }
};
function request(verb, payload) {
  const msgId = randomUUID();
  return new Promise(resolve => {
    pending.set(msgId, resolve);
    ws.send(JSON.stringify({msgId, verb, path:report.route, origin:'frontend', ...(payload ? {payload} : {})}));
  });
}
function payload(reply) {
  if (reply.result?.code !== 'SUCCESS' || typeof reply.payload?.checkPeriodically !== 'boolean' ||
      typeof reply.payload?.downloadAutomatically !== 'boolean') throw Error('Unexpected native policy response');
  return reply.payload;
}
function finish(code, error) {
  if (error) report.error = String(error);
  writeFileSync(output, JSON.stringify(report, null, 2) + '\n', {mode:0o600});
  console.log(JSON.stringify(report, null, 2));
  process.exit(code);
}
setTimeout(() => finish(1, 'Timed out; inspect the saved before/after evidence'), 15000);
ws.onerror = () => finish(1, 'Agent connection failed');
ws.onopen = async () => {
  try {
    report.before = payload(await request('GET'));
    writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
    if (disable) {
      const desired = {...report.before, checkPeriodically:false, downloadAutomatically:false};
      const set = await request('SET', desired);
      report.set_result = set.result;
      if (set.result?.code !== 'SUCCESS') throw Error('Native policy change rejected');
      report.after = payload(await request('GET'));
      // The native SET handler accepts downloadAutomatically but leaves an
      // existing checkPeriodically value unchanged. The stopped-prefix launch
      // guard sets both fields before the service reads its configuration.
      report.periodic_checks_disabled = !report.after.checkPeriodically;
      if (report.after.downloadAutomatically) throw Error('Automatic downloads remain enabled');
    }
    finish(0);
  } catch (error) { finish(1, error); }
};
