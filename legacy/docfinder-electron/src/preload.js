'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// 렌더러(화면)와 메인 프로세스 사이의 안전한 통신 창구.
// nodeIntegration 없이 필요한 기능만 노출한다.
contextBridge.exposeInMainWorld('api', {
  getState: () => ipcRenderer.invoke('state:get'),
  pickFolder: () => ipcRenderer.invoke('folder:pick'),
  reindex: () => ipcRenderer.invoke('index:rebuild'),
  search: (query) => ipcRenderer.invoke('search:run', query),
  readFile: (filePath) => ipcRenderer.invoke('file:read', filePath),
  openInSystem: (filePath) => ipcRenderer.invoke('file:open', filePath),
  onProgress: (cb) => {
    const listener = (e, data) => cb(data);
    ipcRenderer.on('index:progress', listener);
    return () => ipcRenderer.removeListener('index:progress', listener);
  },
});
