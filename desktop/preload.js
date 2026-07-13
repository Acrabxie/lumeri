const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('lumeriDesktop', {
  mode: 'preview',
  version: process.env.npm_package_version || '0.1.0',
});
