// Headless smoke: boot the same stub server + load the UI in an offscreen
// BrowserWindow, wait for paint, capture a PNG, dump console errors.
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');
const { start } = require('../stub-server');

const OUT = process.argv[2] || '/tmp/lumeri-smoke.png';

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const staticRoot = path.join(__dirname, '..', 'app-assets');
  const { server, port } = await start({ staticRoot });
  const errors = [];

  const win = new BrowserWindow({
    width: 1360, height: 860, show: false,
    webPreferences: { offscreen: true, preload: path.join(__dirname, '..', 'preload.js') },
  });

  win.webContents.on('console-message', (_e, level, message) => {
    if (level >= 2) errors.push(message); // 2=warning,3=error
  });
  win.webContents.on('did-fail-load', (_e, code, desc, url) => {
    errors.push(`did-fail-load ${code} ${desc} ${url}`);
  });

  await win.loadURL(`http://127.0.0.1:${port}/`);
  await new Promise((r) => setTimeout(r, 2500));

  const img = await win.webContents.capturePage();
  fs.writeFileSync(OUT, img.toPNG());

  const title = await win.webContents.executeJavaScript('document.title');
  const brandText = await win.webContents.executeJavaScript(
    "document.querySelector('.title-block h1')?.textContent || ''");
  const hasHeader = await win.webContents.executeJavaScript(
    "!!document.querySelector('.app-header')");

  console.log(JSON.stringify({ ok: true, port, title, brandText, hasHeader, errors }, null, 2));

  server.close();
  setTimeout(() => app.quit(), 200);
}).catch((e) => { console.error('SMOKE FAIL', e); app.exit(1); });
