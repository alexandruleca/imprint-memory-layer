'use strict';

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain } = require('electron');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

// ── CLI args ──────────────────────────────────────────────────
// Passed by `imprint ui open` in Go:
//   --imprint-url http://127.0.0.1:8420
//   --project-dir /path/to/imprint
//   --data-dir    /path/to/imprint/data

const argv = process.argv.slice(2);

function arg(name) {
  const i = argv.findIndex(a => a === name || a.startsWith(name + '='));
  if (i === -1) return null;
  if (argv[i].includes('=')) return argv[i].slice(name.length + 1);
  return argv[i + 1] ?? null;
}

const UI_URL      = arg('--imprint-url') ?? 'http://127.0.0.1:8420';
const PROJECT_DIR = arg('--project-dir');
const DATA_DIR    = arg('--data-dir');
const PORT        = new URL(UI_URL).port || '8420';

// ── Single-instance lock ──────────────────────────────────────
// Second `imprint ui open` call focuses existing window.

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  process.exitCode = 0;
}

// ── State ──────────────────────────────────────────────────────

let win        = null;
let tray       = null;
let serverProc = null;
let serverOwned = false;

// ── Server health ─────────────────────────────────────────────

function ping(url) {
  return new Promise(resolve => {
    const pingUrl = url.replace(/\/$/, '') + '/api/ping';
    try {
      const req = http.get(pingUrl, { timeout: 800 }, res => {
        resolve(res.statusCode >= 200 && res.statusCode < 300);
        res.resume();
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    } catch {
      resolve(false);
    }
  });
}

function waitReady(url, timeoutMs = 20000) {
  return new Promise(resolve => {
    const deadline = Date.now() + timeoutMs;
    const check = async () => {
      if (await ping(url)) { resolve(true); return; }
      if (Date.now() >= deadline) { resolve(false); return; }
      setTimeout(check, 300);
    };
    check();
  });
}

// ── Server spawn ──────────────────────────────────────────────
// Only used when Electron is launched standalone (not via `imprint ui open`).
// When launched from Go, the server is already running.

function spawnServer() {
  if (!PROJECT_DIR) return;

  const isWin = process.platform === 'win32';
  const venvPython = isWin
    ? path.join(PROJECT_DIR, '.venv', 'Scripts', 'python.exe')
    : path.join(PROJECT_DIR, '.venv', 'bin', 'python');

  const env = { ...process.env, PYTHONPATH: PROJECT_DIR };
  if (DATA_DIR) env.IMPRINT_DATA_DIR = DATA_DIR;

  serverProc = spawn(
    venvPython,
    ['-m', 'imprint.api', '--port', PORT, '--no-browser'],
    { env, detached: false, stdio: 'ignore' },
  );
  serverOwned = true;
  serverProc.on('error', err => console.error('[imprint-ui] server spawn error:', err.message));
  serverProc.on('exit', code => {
    if (code !== 0) console.warn('[imprint-ui] server exited with code', code);
  });
}

// ── Window ────────────────────────────────────────────────────

function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: 'Imprint',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    backgroundColor: '#09090b',
    frame: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      preload: path.join(__dirname, 'preload.js'),
      webSecurity: true,
    },
    show: false,
  });

  win.loadURL(UI_URL);

  win.once('ready-to-show', () => {
    win.show();
    win.focus();
  });

  // Hide to tray instead of closing (keeps server alive)
  win.on('close', e => {
    if (!app.isQuitting) {
      e.preventDefault();
      win.hide();
    }
  });

  win.on('closed', () => { win = null; });

  // External links open in the OS browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1') || url.startsWith('http://localhost')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Prevent navigation away from the local server
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith('http://127.0.0.1') && !url.startsWith('http://localhost')) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });
}

function showWindow() {
  if (!win) {
    createWindow();
  } else {
    win.show();
    win.focus();
  }
}

// ── Loading screen ────────────────────────────────────────────

function showLoadingWindow() {
  win = new BrowserWindow({
    width: 480,
    height: 300,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: '#09090b',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    show: false,
    webPreferences: {
      sandbox: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
    <!doctype html>
    <html>
    <head><meta charset="utf-8"><style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body {
        background: #09090b;
        color: #a1a1aa;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        height: 100vh; gap: 20px;
        -webkit-app-region: drag;
      }
      .logo { font-size: 2rem; font-weight: 700; color: #e4e4e7;
              background: linear-gradient(135deg, #4f46e5, #8b5cf6, #06b6d4);
              -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
      .dots span {
        display: inline-block; width: 8px; height: 8px; border-radius: 50%;
        background: #4f46e5; margin: 0 3px;
        animation: bounce 1.2s infinite ease-in-out;
      }
      .dots span:nth-child(2) { animation-delay: 0.2s; }
      .dots span:nth-child(3) { animation-delay: 0.4s; }
      @keyframes bounce {
        0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
        40% { transform: scale(1); opacity: 1; }
      }
      .status { font-size: 0.8rem; color: #52525b; }
    </style></head>
    <body>
      <div class="logo">Imprint</div>
      <div class="dots"><span></span><span></span><span></span></div>
      <div class="status">Starting server…</div>
    </body>
    </html>
  `)}`);

  win.once('ready-to-show', () => win.show());
  win.on('closed', () => { win = null; });
}

function showErrorWindow(message) {
  if (win) { win.close(); win = null; }
  win = new BrowserWindow({
    width: 560,
    height: 320,
    title: 'Imprint — Error',
    backgroundColor: '#09090b',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: { sandbox: true },
  });
  win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
    <!doctype html><html><head><meta charset="utf-8"><style>
      body { background:#09090b; color:#f87171; font-family:-apple-system,sans-serif;
             display:flex; flex-direction:column; align-items:center; justify-content:center;
             height:100vh; gap:16px; padding:2em; text-align:center; }
      h2 { color:#fca5a5; }
      pre { background:#18181b; color:#a1a1aa; padding:1em; border-radius:8px;
            font-size:0.8rem; white-space:pre-wrap; width:100%; }
    </style></head><body>
      <h2>Could not reach Imprint server</h2>
      <pre>${message}</pre>
      <p style="color:#52525b;font-size:0.8rem">Run <code style="color:#8b5cf6">imprint ui start</code> and try again.</p>
    </body></html>
  `)}`);
  win.on('closed', () => { win = null; });
}

// ── Tray ──────────────────────────────────────────────────────

function createTray() {
  const iconPath = path.join(__dirname, 'assets', 'tray-icon.png');
  const icon = nativeImage.createFromPath(iconPath);
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip('Imprint');

  const menu = Menu.buildFromTemplate([
    { label: 'Open Dashboard', click: showWindow },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(menu);
  tray.on('double-click', showWindow);
}

// ── Window control IPC ────────────────────────────────────────
// Triggered by the custom traffic-light buttons in the React sidebar.

ipcMain.on('win-close',    () => win?.hide());
ipcMain.on('win-minimize', () => win?.minimize());
ipcMain.on('win-maximize', () => win?.isMaximized() ? win.unmaximize() : win.maximize());

// ── App lifecycle ─────────────────────────────────────────────

app.on('second-instance', () => showWindow());

// Keep app alive when all windows are closed (tray keeps it running)
app.on('window-all-closed', () => {});

// macOS dock click
app.on('activate', () => showWindow());

app.on('before-quit', () => {
  app.isQuitting = true;
  if (serverOwned && serverProc) {
    serverProc.kill('SIGTERM');
  }
});

app.whenReady().then(async () => {
  createTray();

  const alreadyUp = await ping(UI_URL);

  if (!alreadyUp) {
    spawnServer();
    showLoadingWindow();

    const ready = await waitReady(UI_URL, 20000);
    if (win) { win.destroy(); win = null; }

    if (!ready) {
      showErrorWindow(`${UI_URL}/api/ping did not respond within 20s`);
      return;
    }
  }

  createWindow();
});
