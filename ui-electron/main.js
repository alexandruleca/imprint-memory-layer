'use strict';

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain, session } = require('electron');
const path = require('node:path');
const http = require('node:http');
const { dashboardURL, sameOrigin, externalURL } = require('./navigation');
const { DesktopService } = require('./service');
const { voicePage, audioPermission, clipboardWritePermission } = require('./voice-permissions');

// `imprint ui open` resolves installation paths and launches the shell first.
// Packaged apps have no script argument; dropping argv[1] loses the first
// option (the Windows shortcut passes --project-dir first).
const argv = process.argv.slice(app.isPackaged ? 1 : 2);
function arg(name) {
  const i = argv.findIndex(a => a === name || a.startsWith(name + '='));
  if (i === -1) return null;
  return argv[i].includes('=') ? argv[i].slice(name.length + 1) : argv[i + 1] ?? null;
}
const UI_URL = arg('--imprint-url') ?? 'http://127.0.0.1:8420';
const UI_ORIGIN = dashboardURL(UI_URL).origin;
const PROJECT_DIR = arg('--project-dir');
const DATA_DIR = arg('--data-dir');
const PYTHON = arg('--python') ?? (PROJECT_DIR && path.join(
  PROJECT_DIR, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
));
const PORT = new URL(UI_URL).port || '80';

if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}

let win = null;
let startupWin = null;
let companionWin = null;
let tray = null;
let service = null; // Set only for a service this Electron instance spawned.
let phase = 'starting';
let message = 'Starting your local service…';
let attempt = 0;
let quitting = false;
let cleanupFinished = false;

const windowOptions = {
  title: 'Imprint',
  icon: path.join(__dirname, 'assets', 'icon.png'),
  backgroundColor: '#09090b',
  frame: false,
  show: false,
  webPreferences: {
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    preload: path.join(__dirname, 'preload.js'),
    webSecurity: true,
  },
};

function ping() {
  return new Promise(resolve => {
    const req = http.get(`${UI_URL.replace(/\/$/, '')}/api/ping`, { timeout: 800 }, res => {
      resolve(res.statusCode >= 200 && res.statusCode < 300);
      res.resume();
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

function startupState() {
  return { phase, message, logPath: service?.logPath ?? '' };
}

function updateStartup() {
  startupWin?.webContents.send('startup-state', startupState());
}

function closeWindow(window) {
  if (phase !== 'ready') {
    app.quit(); // The splash's close button cancels startup.
  } else if (tray) {
    window.hide();
  } else {
    // Some Linux desktops have no tray. Keep a taskbar entry to reopen it.
    window.minimize();
  }
}

function trackWindow(window, onClosed) {
  window.on('close', e => {
    if (!quitting) {
      e.preventDefault();
      closeWindow(window);
    }
  });
  window.on('closed', onClosed);
  window.once('ready-to-show', () => {
    if (!quitting && !window.isDestroyed()) window.show();
  });
}

function showStartup() {
  if (!startupWin) {
    const window = new BrowserWindow({ ...windowOptions, width: 520, height: 360, resizable: false });
    startupWin = window;
    trackWindow(window, () => { if (startupWin === window) startupWin = null; });
    window.loadFile(path.join(__dirname, 'startup.html')).catch(err => console.error(err));
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', e => e.preventDefault());
  }
  updateStartup();
}

function showError(error) {
  if (quitting) return;
  ++attempt; // Cancel readiness/load callbacks from the previous attempt.
  phase = 'error';
  message = error;
  showStartup();
  if (win) win.destroy();
  companionWin?.destroy();
  updateStartup();
}

async function openDashboard(id) {
  phase = 'loading';
  message = 'Loading your dashboard…';
  updateStartup();
  const window = new BrowserWindow({ ...windowOptions, width: 1400, height: 900, minWidth: 800, minHeight: 600 });
  win = window;
  // Keep the splash visible until the dashboard has actually loaded.
  window.on('close', e => {
    if (!quitting) { e.preventDefault(); closeWindow(window); }
  });
  window.on('closed', () => { if (win === window) win = null; });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (sameOrigin(url, UI_ORIGIN)) window.loadURL(url).catch(err => showError(err.message));
    else if (externalURL(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (e, url) => {
    if (!sameOrigin(url, UI_ORIGIN)) {
      e.preventDefault();
      if (externalURL(url)) shell.openExternal(url);
    }
  });
  window.webContents.on('render-process-gone', () => showError('The dashboard stopped responding. Please try again.'));
  await window.loadURL(UI_URL);
  if (quitting || id !== attempt) return;
  phase = 'ready';
  window.show();
  window.focus();
  startupWin?.destroy();
}

async function start() {
  if (quitting) return;
  const id = ++attempt;
  phase = 'starting';
  message = 'Starting your local service…';
  showStartup();
  try {
    // Retry waits for the previous owned service to release its port.
    await service?.stop();
    if (quitting || id !== attempt) return;
    service = null;
    if (!await ping()) {
      if (quitting || id !== attempt) return;
      if (!PROJECT_DIR || !DATA_DIR || !PYTHON) {
        throw new Error('Open Imprint using the installed shortcut or “imprint ui open” so it can locate your runtime.');
      }
      service = new DesktopService({ python: PYTHON, projectDir: PROJECT_DIR, dataDir: DATA_DIR, port: PORT });
      service.start(error => showError(error));
      const deadline = Date.now() + 60000;
      while (!quitting && id === attempt) {
        if (await ping()) break;
        if (Date.now() >= deadline) throw new Error('The local service did not become ready within 60 seconds. Check the startup log, then try again.');
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    }
    if (quitting || id !== attempt) return;
    await openDashboard(id);
  } catch (err) {
    if (!quitting && id === attempt) {
      showError(err.message);
      await service?.stop();
    }
  }
}

function showWindow() {
  if (quitting) return;
  const window = phase === 'ready' ? win : startupWin;
  if (window) {
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  }
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, 'assets', 'tray-icon.png'));
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip('Imprint');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Dashboard', click: showWindow },
    { label: 'Float Echo', click: openCompanion },
    { type: 'separator' },
    { label: 'Quit Imprint', click: () => app.quit() },
  ]));
  tray.on('double-click', showWindow);
}

// Only the current app windows can control their lifecycle.
function appWindow(event) {
  return [win, startupWin, companionWin].some(window => window?.webContents === event.sender);
}
ipcMain.on('win-close', event => {
  if (companionWin?.webContents === event.sender) { companionWin.destroy(); return; }
  if (appWindow(event)) closeWindow(BrowserWindow.fromWebContents(event.sender));
});

function trustedVoiceSender(event) {
  return [win, companionWin].some(window => window?.webContents === event.sender)
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && voicePage(event.sender.getURL(), UI_ORIGIN);
}
async function openCompanion() {
  if (quitting || phase !== 'ready') return;
  if (companionWin) { companionWin.show(); companionWin.focus(); return; }
  const window = new BrowserWindow({ ...windowOptions, title: 'Imprint · Echo', width: 380, height: 610,
    minWidth: 340, minHeight: 560, transparent: true, backgroundColor: '#00000000',
    resizable: false, hasShadow: false, alwaysOnTop: false });
  companionWin = window;
  window.on('closed', () => { if (companionWin === window) companionWin = null; });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (externalURL(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (!voicePage(url, UI_ORIGIN) || new URL(url).pathname.replace(/\/$/, '') !== '/companion') event.preventDefault();
  });
  window.webContents.on('render-process-gone', () => window.destroy());
  try {
    await window.loadURL(`${UI_ORIGIN}/companion/`);
    if (!quitting && !window.isDestroyed()) { window.show(); window.focus(); }
  } catch { window.destroy(); }
}
ipcMain.on('voice-open-companion', event => { if (trustedVoiceSender(event)) void openCompanion(); });
ipcMain.on('voice-close-companion', event => {
  if (companionWin?.webContents === event.sender && trustedVoiceSender(event)) companionWin.destroy();
});
ipcMain.on('voice-pin-companion', (event, pinned) => {
  if (typeof pinned === 'boolean' && companionWin?.webContents === event.sender && trustedVoiceSender(event)) companionWin.setAlwaysOnTop(pinned);
});
ipcMain.on('app-quit', event => { if (appWindow(event)) app.quit(); });
ipcMain.on('win-minimize', event => { if (appWindow(event)) BrowserWindow.fromWebContents(event.sender)?.minimize(); });
ipcMain.on('win-maximize', event => {
  if (win?.webContents !== event.sender) return;
  win.isMaximized() ? win.unmaximize() : win.maximize();
});
ipcMain.handle('startup-state', event => appWindow(event) ? startupState() : null);
ipcMain.on('startup-retry', event => {
  if (startupWin?.webContents === event.sender && phase === 'error') start();
});
ipcMain.on('startup-log', event => {
  if (startupWin?.webContents === event.sender && service) shell.openPath(service.logPath);
});

app.on('second-instance', showWindow);
app.on('activate', showWindow);
// Closing the dashboard hides it; explicit Quit also stops its owned service.
app.on('window-all-closed', () => { if (!quitting) app.quit(); });
app.on('before-quit', event => {
  if (cleanupFinished) return;
  event.preventDefault();
  if (quitting) return;
  quitting = true;
  ++attempt;
  win?.hide();
  startupWin?.hide();
  // Releasing this renderer also closes its mic/socket on a reused daemon.
  companionWin?.destroy();
  Promise.resolve(service?.stop()).finally(() => {
    cleanupFinished = true;
    app.quit();
  });
});

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin])
      || clipboardWritePermission(contents, permission, details, UI_ORIGIN, [win, companionWin]));
  });
  session.defaultSession.setPermissionCheckHandler((contents, permission, requestingOrigin, details) => {
    return sameOrigin(requestingOrigin, UI_ORIGIN)
      && (audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin])
        || clipboardWritePermission(contents, permission, details, UI_ORIGIN, [win, companionWin]));
  });
  // Display the splash before probing or starting Python.
  showStartup();
  try { createTray(); } catch (err) { console.warn('Tray unavailable:', err.message); }
  return start();
});
