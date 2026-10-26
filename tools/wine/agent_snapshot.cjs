#!/usr/bin/env node
// Read-only requests to the same local agent API used by the G HUB frontend.
// Requires Node's built-in WebSocket; no packages and no input injection.
const { randomUUID } = require('node:crypto');
const { writeFileSync } = require('node:fs');
const routes = ['/devices/list', '/profiles', '/profile/active',
  '/profiles/auto_switching', '/input/gshift'];
const args = process.argv.slice(2);
if (args.length > 1 || args[0] === '--help') {
  console.log('Usage: node agent_snapshot.cjs [output.json]\nRead-only G HUB agent snapshot from ws://127.0.0.1:9010.');
  process.exit(args[0] === '--help' ? 0 : 2);
}
if (typeof WebSocket === 'undefined') {
  console.error('This script requires Node with built-in WebSocket support.');
  process.exit(2);
}
const report = { recorded_at: new Date().toISOString(), endpoint: 'ws://127.0.0.1:9010',
  read_only: true, responses: {}, failures: {} };
const pending = new Map(routes.map(path => [randomUUID(), path]));
let finished = false;
function finish(error) {
  if (finished) return;
  finished = true;
  clearTimeout(deadline);
  for (const path of pending.values()) report.failures[path] = error || 'No response';
  const text = JSON.stringify(report, null, 2) + '\n';
  if (args[0]) writeFileSync(args[0], text, { mode: 0o600, flag: 'wx' });
  else writeFileSync(1, text);
  const failed = Object.keys(report.failures).length || Object.values(report.responses)
    .some(response => response.result?.code !== 'SUCCESS');
  // close() can wait indefinitely on a wedged Wine agent. All output is written
  // synchronously before exiting, including on connection failure/timeout.
  process.exit(failed ? 1 : 0);
}
const deadline = setTimeout(() => finish('Agent response timeout (10 seconds)'), 10000);
const ws = new WebSocket(report.endpoint, 'json');
ws.onopen = () => {
  for (const [msgId, path] of pending) ws.send(JSON.stringify({ msgId, verb: 'GET', path, origin: 'frontend' }));
};
ws.onmessage = event => {
  let response;
  try { response = JSON.parse(event.data); } catch { return; }
  const path = pending.get(response.msgId);
  if (!path) return;
  report.responses[path] = response;
  pending.delete(response.msgId);
  if (!pending.size) finish();
};
ws.onerror = () => finish('Agent WebSocket connection failed');
ws.onclose = () => finish('Agent closed the connection');
