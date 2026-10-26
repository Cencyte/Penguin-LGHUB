// Runs in a disposable copy of G HUB's Electron runtime, never in its app.asar.
const {app, BrowserWindow} = require('electron');

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    x:80, y:80, width:1024, height:768, minWidth:800, minHeight:600,
    frame:false, show:false, backgroundColor:'#000', titleBarStyle:'hidden',
    title:'GHUB Window Regression',
    webPreferences:{contextIsolation:true, nodeIntegration:false}
  });
  const html = `<!doctype html><html><head><title>GHUB Window Regression</title>
  <style>
  html,body{margin:0;width:100%;height:100%;background:#202020;}
  header{height:70px;background:#00cc44;-webkit-app-region:drag;user-select:none;}
  #red{position:absolute;left:100px;top:180px;width:200px;height:160px;background:#ee2244;}
  #blue{position:absolute;left:450px;top:400px;width:260px;height:180px;background:#2255ee;}
  #flash{position:absolute;left:750px;top:240px;width:100px;height:100px;background:#ffff00;}
  </style></head><body><header></header><div id="red"></div><div id="blue"></div>
  <div id="flash"></div><script>
  setInterval(()=>{const e=document.getElementById('flash');
    e.style.backgroundColor=e.style.backgroundColor==='rgb(255, 255, 0)'?'#00ffff':'#ffff00';},1000);
  </script></body></html>`;
  window.webContents.on('render-process-gone', (_, details) => {
    console.error('FIXTURE_RENDERER_GONE', JSON.stringify(details)); app.exit(1);
  });
  window.show();
  await window.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
  console.log('FIXTURE_READY', JSON.stringify(process.versions));
});
app.on('window-all-closed', () => app.quit());
