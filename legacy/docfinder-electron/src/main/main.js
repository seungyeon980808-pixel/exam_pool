'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const fs = require('fs');
const path = require('path');
const { buildIndex } = require('./indexer');
const { bm25Search } = require('./search');

let mainWindow = null;

// ---- 앱 데이터(색인/설정) 저장 위치: %APPDATA%/docfinder ----
function dataDir() {
  return app.getPath('userData');
}
function configPath() {
  return path.join(dataDir(), 'config.json');
}
function indexPath() {
  return path.join(dataDir(), 'index.json');
}

let INDEX = null; // { folder, builtAt, docs, pages, skipped }
let indexing = false;

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(configPath(), 'utf8'));
  } catch (e) {
    return {};
  }
}
function saveConfig(cfg) {
  try {
    fs.writeFileSync(configPath(), JSON.stringify(cfg, null, 2), 'utf8');
  } catch (e) {
    /* ignore */
  }
}
function loadIndex() {
  try {
    INDEX = JSON.parse(fs.readFileSync(indexPath(), 'utf8'));
  } catch (e) {
    INDEX = null;
  }
}
function saveIndex() {
  try {
    fs.writeFileSync(indexPath(), JSON.stringify(INDEX), 'utf8');
  } catch (e) {
    /* ignore */
  }
}

function currentState() {
  return {
    folder: INDEX ? INDEX.folder : (loadConfig().folder || null),
    docCount: INDEX ? INDEX.docs.length : 0,
    pageCount: INDEX ? INDEX.pages.length : 0,
    skipped: INDEX ? INDEX.skipped : [],
    builtAt: INDEX ? INDEX.builtAt : null,
    indexing,
  };
}

async function runIndex(folder) {
  if (indexing) return currentState();
  indexing = true;
  try {
    const idx = await buildIndex(folder, (p) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('index:progress', p);
      }
    });
    INDEX = idx;
    saveIndex();
    saveConfig({ folder });
  } finally {
    indexing = false;
  }
  return currentState();
}

// ---- IPC 핸들러 ----
ipcMain.handle('state:get', () => currentState());

ipcMain.handle('folder:pick', async () => {
  const ret = await dialog.showOpenDialog(mainWindow, {
    title: '검색할 폴더를 선택하세요',
    properties: ['openDirectory'],
  });
  if (ret.canceled || !ret.filePaths.length) return currentState();
  return await runIndex(ret.filePaths[0]);
});

ipcMain.handle('index:rebuild', async () => {
  const folder = INDEX ? INDEX.folder : loadConfig().folder;
  if (!folder) return currentState();
  return await runIndex(folder);
});

ipcMain.handle('search:run', (e, query) => {
  if (!INDEX || !INDEX.pages.length) return { total: 0, items: [], terms: [] };
  return bm25Search(INDEX.pages, query, 60);
});

ipcMain.handle('file:read', (e, filePath) => {
  const buf = fs.readFileSync(filePath);
  return new Uint8Array(buf); // 렌더러로 전달 (pdf.js 미리보기용)
});

ipcMain.handle('file:open', async (e, filePath) => {
  const err = await shell.openPath(filePath);
  return err || null;
});

// ---- 윈도우 ----
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 600,
    title: 'DocFinder — 규정 빠른검색기',
    backgroundColor: '#ffffff',
    icon: path.join(__dirname, '..', '..', 'build', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
}

app.whenReady().then(() => {
  loadIndex();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
