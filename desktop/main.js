const { app, BrowserWindow, Menu, shell } = require('electron');
const path = require('path');
const { start: startStub } = require('./stub-server');

app.setName('Lumeri');

let mainWindow = null;
let stubHandle = null;

async function createWindow() {
  const staticRoot = path.join(__dirname, 'app-assets');
  stubHandle = await startStub({ staticRoot });
  const url = `http://127.0.0.1:${stubHandle.port}/`;

  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1080,
    minHeight: 680,
    backgroundColor: '#0c0d10',
    title: 'Lumeri',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });

  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    if (/^https?:\/\//i.test(target)) {
      shell.openExternal(target);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  await mainWindow.loadURL(url);
}

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{
      label: 'Lumeri',
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: 'File',
      submenu: [ isMac ? { role: 'close' } : { role: 'quit' } ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'Visit lumeri.ai',
          click: () => shell.openExternal('https://lumeri.ai'),
        },
        {
          label: 'GitHub',
          click: () => shell.openExternal('https://github.com/Acrabxie/lumeri'),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(async () => {
  buildMenu();
  await createWindow();
  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow();
  });
});

app.on('window-all-closed', () => {
  if (stubHandle?.server) stubHandle.server.close();
  if (process.platform !== 'darwin') app.quit();
});

app.on('web-contents-created', (_, wc) => {
  wc.on('will-navigate', (event, targetUrl) => {
    if (!targetUrl.startsWith('http://127.0.0.1:')) {
      event.preventDefault();
      shell.openExternal(targetUrl);
    }
  });
});
