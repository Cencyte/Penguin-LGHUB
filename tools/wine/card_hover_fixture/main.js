const {app, BrowserWindow} = require('electron');
let window;
app.whenReady().then(async () => {
  window = new BrowserWindow({x:0, y:0, width:1000, height:700, frame:false,
    title:'GHUB Card Hover Regression',
    webPreferences:{contextIsolation:true, nodeIntegration:false}});
  window.webContents.on('console-message', event => console.log(event.message));
  window.webContents.on('render-process-gone', (_, details) => {
    console.error('HOVER_FAILED', JSON.stringify(details)); app.exit(1);
  });
  const layout = process.argv.includes('--hover-list') ? 'list' : 'grid';
  await window.loadFile('index.html', {query:{layout}});
}).catch(error => { console.error('HOVER_FAILED', error); app.exit(1); });
app.on('window-all-closed', () => app.quit());
