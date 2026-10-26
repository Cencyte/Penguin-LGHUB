const {app, BrowserWindow} = require('electron');
app.whenReady().then(async () => {
  const window = new BrowserWindow({x:0,y:0,width:1000,height:700,frame:false,
    title:'GHUB Color Benchmark', webPreferences:{contextIsolation:true,nodeIntegration:false}});
  window.webContents.on('console-message', (event) => console.log(event.message));
  window.webContents.on('render-process-gone', (_, details) => {
    console.error('BENCHMARK_FAILED', JSON.stringify(details)); app.exit(1);
  });
  await window.loadFile('index.html');
});
app.on('window-all-closed', () => app.quit());
