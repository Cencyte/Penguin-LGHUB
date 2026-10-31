// Private integration-test client. Mutations are only used on copied settings.
const fs = require('node:fs');
const message = JSON.parse(process.argv[2]);
const ws = new WebSocket('ws://127.0.0.1:9010', 'json');
const deadline = setTimeout(() => process.exit(2), 5000);
ws.onopen = () => ws.send(JSON.stringify({msgId: 'profile-test', origin: 'frontend', ...message}));
ws.onmessage = event => {
    const response = JSON.parse(event.data);
    if (response.msgId !== 'profile-test') return;
    clearTimeout(deadline);
    fs.writeFileSync(1, JSON.stringify(response));
    process.exit(response.result?.code === 'SUCCESS' ? 0 : 1);
};
ws.onerror = () => process.exit(3);
