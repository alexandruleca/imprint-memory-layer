'use strict';

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain, session, systemPreferences, globalShortcut, screen } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const { execFile } = require('node:child_process');
const { dashboardURL, sameOrigin, externalURL } = require('./navigation');
const { DesktopService } = require('./service');
const { voicePage, audioPermission, clipboardWritePermission, macMicrophoneConsent,
  requestMicrophoneAccess, microphoneSettingsURL } = require('./voice-permissions');
const { SHORTCUT, fitBounds, fitLauncher, point, dragBounds, clientPoint, trayImage, dockCompanion, clampFloatingGroup } = require('./companion');
const { desktopRuntime } = require('./runtime');
const { readActivity, describeActivity } = require('./activity');

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
const NAMED_RUNTIME = { projectDir: arg('--project-dir'), dataDir: arg('--data-dir'), python: arg('--python') };
const PORT = new URL(UI_URL).port || '80';
const PROFILE_DIR = arg('--user-data-dir');
if (PROFILE_DIR) app.setPath('userData', path.resolve(PROFILE_DIR));
if (process.platform === 'linux') app.commandLine?.appendSwitch('enable-features', 'GlobalShortcutsPortal');

if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}

let win = null;
// Where the running job reports its progress (ADR 0089); known once the
// runtime is.
let dataDir = NAMED_RUNTIME.dataDir;
let startupWin = null;
let companionWin = null;
let launcherWin = null;
let launcherDrag = null;
let hidingDashboard = false;
let wantLauncher = false;
let tray = null;
let service = null; // Set only for a service this Electron instance spawned.
let phase = 'starting';
let message = 'Starting your local service…';
let attempt = 0;
let quitting = false;
let cleanupFinished = false;
let companionDrag = null;
// The user's pin choice. While the dashboard is hidden the companion is grouped
// with the launcher and stays on top regardless (ADR 0070).
let companionPinned = false;
let shortcutAvailable = false;

function savedCompanionBounds(name = 'echo-window.json') {
  try { return JSON.parse(fs.readFileSync(path.join(app.getPath('userData'), name), 'utf8')); }
  catch { return null; }
}
function saveCompanionBounds(window = companionWin, name = 'echo-window.json', bounds = null) {
  if (!window || window.isDestroyed()) return;
  try {
    const directory = app.getPath('userData');
    fs.mkdirSync(directory, { recursive: true });
    const file = path.join(directory, name);
    fs.writeFileSync(file + '.tmp', JSON.stringify(bounds || window.getBounds()), { mode: 0o600 });
    fs.renameSync(file + '.tmp', file);
  } catch { /* Position persistence must not stop voice or window recovery. */ }
}
function companionArea(bounds = null) {
  if (bounds && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(bounds[key])) && bounds.width > 0 && bounds.height > 0) {
    return screen.getDisplayMatching(bounds).workArea;
  }
  return screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
}
function recoverCompanion() { return openCompanion(true); }

// While the dashboard is hidden, the companion docks beside the launcher and
// either window's drag moves both (ADR 0070).
function floatingGroup() {
  return wantLauncher && !!companionWin && !!launcherWin && !companionWin.isDestroyed() && !launcherWin.isDestroyed();
}
function placeFloatingGroup(group) {
  companionWin.setBounds(group.companion); launcherWin.setBounds(group.launcher);
  saveCompanionBounds(companionWin, 'echo-window.json', group.companion);
  saveCompanionBounds(launcherWin, 'launcher-window.json', group.launcher);
}
function dockFloatingGroup() {
  if (!floatingGroup()) return;
  const bounds = launcherWin.getBounds();
  placeFloatingGroup(dockCompanion(companionWin.getBounds(), bounds, companionArea(bounds)));
  companionWin.setAlwaysOnTop(true);
}
function finishFloatingGroup(targets) {
  if (!floatingGroup()) return false;
  // Native position reports can lag setPosition independently for each window;
  // clamp and save the positions the drag intended, never a half-updated pair.
  const companion = targets?.companion || companionWin.getBounds();
  placeFloatingGroup(clampFloatingGroup(companion, targets?.launcher || launcherWin.getBounds(), companionArea(companion)));
  return true;
}

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
  } else if (window === win) {
    void hideDashboard();
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
  window.on('minimize', () => { if (!quitting && phase === 'ready') void hideDashboard(); });
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
      const runtime = await desktopRuntime(NAMED_RUNTIME,
        { platform: process.platform, execPath: process.execPath, env: process.env, execFile });
      if (quitting || id !== attempt) return;
      dataDir = runtime.dataDir;
      service = new DesktopService({ ...runtime, port: PORT });
      service.start(error => showError(error));
      let deadline = Date.now() + 60000;
      while (!quitting && id === attempt) {
        if (await ping()) break;
        const busy = readActivity(dataDir);
        if (busy) {
          message = `Waiting for ${describeActivity(busy)}…`;
          updateStartup();
          // Setup holds the service back while it changes the runtime
          // (ADR 0084); it is not late while setup works.
          if (busy.command === 'setup') deadline = Date.now() + 60000;
        }
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

// macOS shows a regular app in the Dock even with every window hidden. Like
// the Windows taskbar button, the Dock icon is there only while the dashboard
// or the splash is; hidden to the tray, Imprint lives in the menu bar and the
// floating launcher.
function showInDock(visible) {
  if (!app.dock) return;
  if (visible) app.dock.show();
  else app.dock.hide();
}

function showWindow() {
  if (quitting) return;
  wantLauncher = false;
  const window = phase === 'ready' ? win : startupWin;
  if (window) {
    showInDock(true);
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
    launcherWin?.hide();
    companionWin?.setAlwaysOnTop(companionPinned);
  }
}

async function hideDashboard() {
  if (hidingDashboard || quitting || !win) return;
  hidingDashboard = true;
  wantLauncher = true;
  try {
    const opening = showLauncher();
    if (tray) win.hide();
    await opening;
    if (!wantLauncher || quitting) return;
    if (tray || launcherWin?.isVisible()) win.hide();
    else win.minimize();
    if (tray) showInDock(false);
  } finally { hidingDashboard = false; }
}
async function showLauncher() {
  if (quitting || phase !== 'ready') return;
  if (launcherWin) { launcherWin.showInactive(); dockFloatingGroup(); return; }
  const saved = savedCompanionBounds('launcher-window.json');
  const window = new BrowserWindow({ ...windowOptions, ...fitLauncher(saved, companionArea(saved)),
    title: 'Imprint launcher', transparent: true, backgroundColor: '#00000000', resizable: false,
    hasShadow: false, alwaysOnTop: true, skipTaskbar: true, minWidth: 48, minHeight: 48 });
  launcherWin = window;
  window.on('closed', () => { if (launcherWin === window) launcherWin = null; launcherDrag = null; });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    if (url !== `${UI_ORIGIN}/launcher/`) event.preventDefault();
  });
  try {
    await window.loadURL(`${UI_ORIGIN}/launcher/`);
    if (!quitting && wantLauncher && !window.isDestroyed()) { window.showInactive(); dockFloatingGroup(); }
  } catch { if (!window.isDestroyed()) window.destroy(); }
}
function trustedLauncher(event) {
  return launcherWin?.webContents === event.sender
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && event.sender.getURL() === `${UI_ORIGIN}/launcher/`;
}
ipcMain.on('launcher-open', event => { if (trustedLauncher(event)) showWindow(); });
ipcMain.on('launcher-menu', event => {
  if (!trustedLauncher(event)) return;
  Menu.buildFromTemplate([{ label: 'Open Dashboard', click: showWindow },
    { label: 'Show Echo', accelerator: SHORTCUT, click: recoverCompanion },
    { type: 'separator' }, { label: 'Quit Imprint', click: () => app.quit() }]).popup({ window: launcherWin });
});
ipcMain.on('launcher-drag-start', (event, origin) => {
  if (trustedLauncher(event) && point(origin)) launcherDrag = { bounds: launcherWin.getBounds(), origin,
    companion: floatingGroup() ? companionWin.getBounds() : null };
});
ipcMain.on('launcher-drag-move', (event, current) => {
  if (!trustedLauncher(event) || !launcherDrag) return;
  const bounds = dragBounds(launcherDrag.bounds, launcherDrag.origin, current);
  if (!bounds) return;
  launcherWin.setPosition(bounds.x, bounds.y);
  if (launcherDrag.companion && floatingGroup()) {
    const head = dragBounds(launcherDrag.companion, launcherDrag.origin, current);
    launcherDrag.targets = { companion: head, launcher: bounds };
    companionWin.setPosition(head.x, head.y);
  }
});
ipcMain.on('launcher-drag-end', event => {
  if (!trustedLauncher(event) || !launcherDrag) return;
  const { targets } = launcherDrag;
  launcherDrag = null;
  if (finishFloatingGroup(targets)) return;
  launcherWin.setBounds(fitLauncher(launcherWin.getBounds(), screen.getDisplayMatching(launcherWin.getBounds()).workArea));
  saveCompanionBounds(launcherWin, 'launcher-window.json');
});

// The tray says what the running job is doing (ADR 0089): in its tooltip, as
// the first line of its menu, which Linux trays show when they skip tooltips,
// and on macOS as the percentage beside the menu bar icon.
let trayActivity = null;

function trayMenu(activity) {
  return Menu.buildFromTemplate([
    ...(activity ? [{ label: activity, enabled: false }, { type: 'separator' }] : []),
    { label: 'Open Dashboard', click: showWindow },
    { label: 'Float Echo', accelerator: SHORTCUT, click: recoverCompanion },
    { type: 'separator' },
    { label: 'Quit Imprint', click: () => app.quit() },
  ]);
}

function refreshTray() {
  if (!tray) return;
  const record = readActivity(dataDir);
  const activity = record ? describeActivity(record) : '';
  if (activity === trayActivity) return;
  trayActivity = activity;
  tray.setToolTip(activity ? `Imprint · ${activity}` : 'Imprint');
  tray.setContextMenu(trayMenu(activity));
  if (process.platform === 'darwin') {
    tray.setTitle(record?.total > 0 ? ` ${Math.floor((100 * Math.min(record.processed, record.total)) / record.total)}%` : '');
  }
}

function createTray() {
  const icon = trayImage(nativeImage.createFromPath(path.join(__dirname, 'assets', 'tray-icon.png')), process.platform);
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  refreshTray();
  setInterval(refreshTray, 2000);
  tray.on('double-click', showWindow);
}

// Only the current app windows can control their lifecycle.
function appWindow(event) {
  return [win, startupWin, companionWin].some(window => window?.webContents === event.sender);
}
ipcMain.on('win-close', event => {
  if (companionWin?.webContents === event.sender) { saveCompanionBounds(); companionWin.destroy(); return; }
  if (appWindow(event)) closeWindow(BrowserWindow.fromWebContents(event.sender));
});

function trustedVoiceSender(event) {
  return [win, companionWin].some(window => window?.webContents === event.sender)
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && voicePage(event.sender.getURL(), UI_ORIGIN);
}
function trustedDashboardSender(event) {
  return win?.webContents === event.sender
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && sameOrigin(event.sender.getURL(), UI_ORIGIN);
}
// Enable microphone asks the OS first (ADR 0069). Consent can take as long as the
// user leaves the dialog open, so the sender is checked again afterwards.
ipcMain.handle('voice-microphone-access', async event => {
  if (!trustedVoiceSender(event)) return { status: 'denied' };
  const result = await requestMicrophoneAccess(process.platform, systemPreferences);
  return trustedVoiceSender(event) ? result : { status: 'denied' };
});
ipcMain.handle('voice-microphone-settings', async event => {
  const url = microphoneSettingsURL(process.platform);
  if (!trustedVoiceSender(event) || !url) return false;
  try { await shell.openExternal(url); return true; } catch { return false; }
});
function companionState() {
  return { open: !!companionWin, pinned: companionPinned, shortcut: SHORTCUT, shortcutAvailable };
}
function notifyCompanion() {
  const state = companionState();
  for (const window of [win, companionWin]) {
    if (window && !window.isDestroyed()) window.webContents.send('voice-companion-state', state);
  }
}
async function openCompanion(recover = false) {
  if (quitting || phase !== 'ready') return;
  if (companionWin) {
    companionWin.setBounds(fitBounds(companionWin.getBounds(), companionArea(recover ? null : companionWin.getBounds()), recover));
    if (companionWin.isMinimized()) companionWin.restore();
    if (recover && process.platform === 'darwin') app.focus?.({ steal: true });
    companionWin.show(); companionWin.moveTop(); companionWin.focus();
    saveCompanionBounds(); dockFloatingGroup(); return;
  }
  const saved = savedCompanionBounds();
  const bounds = fitBounds(saved, companionArea(recover ? null : saved), recover);
  // acceptFirstMouse (macOS): a click on the floating window acts at once
  // instead of only activating it, so pin and the controls answer the first click.
  const window = new BrowserWindow({ ...windowOptions, ...bounds, title: 'Imprint · Echo',
    minWidth: 280, minHeight: 360, transparent: true, backgroundColor: '#00000000',
    resizable: false, hasShadow: false, alwaysOnTop: false, acceptFirstMouse: true, skipTaskbar: true });
  companionWin = window;
  companionPinned = false;
  notifyCompanion();
  window.on('close', () => saveCompanionBounds(window));
  window.on('closed', () => { if (companionWin === window) companionWin = null; companionDrag = null; notifyCompanion(); });
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
    if (!quitting && !window.isDestroyed()) { window.show(); window.focus(); dockFloatingGroup(); }
  } catch { if (!window.isDestroyed()) window.destroy(); }
}
ipcMain.handle('voice-companion-state', event => trustedDashboardSender(event)
  || (companionWin?.webContents === event.sender && trustedVoiceSender(event)) ? companionState() : null);
ipcMain.handle('voice-set-companion', async (event, enabled) => {
  if (!trustedDashboardSender(event) || typeof enabled !== 'boolean') return null;
  if (enabled) await openCompanion();
  else { saveCompanionBounds(); companionWin?.destroy(); }
  notifyCompanion();
  return companionState();
});
ipcMain.on('voice-recover-companion', event => {
  if (trustedDashboardSender(event) || (companionWin?.webContents === event.sender && trustedVoiceSender(event))) void recoverCompanion();
});
ipcMain.on('voice-drag-start', (event, origin) => {
  if (companionWin?.webContents !== event.sender || !trustedVoiceSender(event) || !point(origin)) return;
  companionDrag = { bounds: companionWin.getBounds(), origin,
    launcher: floatingGroup() ? launcherWin.getBounds() : null };
});
ipcMain.on('voice-drag-move', (event, current) => {
  if (!companionDrag || companionWin?.webContents !== event.sender || !trustedVoiceSender(event)) return;
  const bounds = dragBounds(companionDrag.bounds, companionDrag.origin, current);
  if (!bounds) return;
  companionWin.setPosition(bounds.x, bounds.y);
  if (companionDrag.launcher && floatingGroup()) {
    const icon = dragBounds(companionDrag.launcher, companionDrag.origin, current);
    companionDrag.targets = { companion: bounds, launcher: icon };
    launcherWin.setPosition(icon.x, icon.y);
  }
});
ipcMain.on('voice-drag-end', event => {
  if (!companionDrag || companionWin?.webContents !== event.sender || !trustedVoiceSender(event)) return;
  const { targets } = companionDrag;
  companionDrag = null;
  if (finishFloatingGroup(targets)) return;
  const bounds = companionWin.getBounds();
  companionWin.setBounds(fitBounds(bounds, screen.getDisplayMatching(bounds).workArea));
  saveCompanionBounds();
});
ipcMain.on('voice-open-companion', event => { if (trustedVoiceSender(event)) void openCompanion(); });
ipcMain.on('voice-close-companion', event => {
  if (companionWin?.webContents === event.sender && trustedVoiceSender(event)) { saveCompanionBounds(); companionWin.destroy(); }
});
ipcMain.on('voice-pin-companion', (event, pinned) => {
  if (typeof pinned === 'boolean' && companionWin?.webContents === event.sender && trustedVoiceSender(event)) {
    companionPinned = pinned;
    companionWin.setAlwaysOnTop(pinned || floatingGroup()); notifyCompanion();
  }
});
// Echo looks toward the pointer anywhere on screen. While a voice page watches,
// the cursor is sent in that page's coordinates whenever it moves relative to it.
const cursorWatchers = new Map();
let cursorTimer = null;
function sendCursor() {
  const cursor = screen.getCursorScreenPoint();
  for (const [contents, watch] of cursorWatchers) {
    const window = BrowserWindow.fromWebContents(contents);
    if (!window || window.isDestroyed() || !window.isVisible()) continue;
    const at = clientPoint(cursor, window.getContentBounds(), contents.getZoomFactor());
    if (watch.last?.x === at.x && watch.last?.y === at.y) continue;
    watch.last = at;
    contents.send('voice-cursor', at);
  }
}
function unwatchCursor(contents) {
  const watch = cursorWatchers.get(contents);
  if (!watch) return;
  contents.removeListener('destroyed', watch.end);
  contents.removeListener('did-navigate', watch.end);
  cursorWatchers.delete(contents);
  if (!cursorWatchers.size) { clearInterval(cursorTimer); cursorTimer = null; }
}
ipcMain.on('voice-cursor-watch', (event, watching) => {
  if (typeof watching !== 'boolean' || !trustedVoiceSender(event)) return;
  const contents = event.sender, watch = cursorWatchers.get(contents);
  if (!watching) { if (watch && --watch.count === 0) unwatchCursor(contents); return; }
  if (watch) { watch.count++; return; }
  // A page that reloads or closes stops watching without saying so.
  const end = () => unwatchCursor(contents);
  cursorWatchers.set(contents, { count: 1, last: null, end });
  contents.once('destroyed', end);
  contents.once('did-navigate', end);
  cursorTimer ??= setInterval(sendCursor, 33);
});
ipcMain.on('app-quit', event => { if (appWindow(event)) app.quit(); });
ipcMain.on('win-minimize', event => {
  if (!appWindow(event)) return;
  if (event.sender === win?.webContents) void hideDashboard();
  else BrowserWindow.fromWebContents(event.sender)?.minimize();
});
ipcMain.on('win-maximize', event => {
  if (win?.webContents !== event.sender) return;
  win.isMaximized() ? win.unmaximize() : win.maximize();
});
ipcMain.handle('startup-state', event => appWindow(event) ? startupState() : null);
// What the dashboard shows while its server is down, which it cannot ask.
ipcMain.handle('activity', event => trustedDashboardSender(event) ? readActivity(dataDir) : null);
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
  globalShortcut.unregisterAll();
  ++attempt;
  win?.hide();
  startupWin?.hide();
  // Releasing this renderer also closes its mic/socket on a reused daemon.
  saveCompanionBounds(); companionWin?.destroy();
  saveCompanionBounds(launcherWin, 'launcher-window.json'); launcherWin?.destroy();
  Promise.resolve(service?.stop()).finally(() => {
    cleanupFinished = true;
    app.quit();
  });
});

app.whenReady().then(() => {
  try { shortcutAvailable = globalShortcut.register(SHORTCUT, () => { void recoverCompanion(); }); }
  catch { shortcutAvailable = false; }
  screen.on('display-removed', () => {
    if (companionWin) void recoverCompanion();
    if (launcherWin) launcherWin.setBounds(fitLauncher(null, companionArea()));
    dockFloatingGroup();
  });
  screen.on('display-metrics-changed', () => {
    if (launcherWin) launcherWin.setBounds(fitLauncher(launcherWin.getBounds(), screen.getDisplayMatching(launcherWin.getBounds()).workArea));
    if (companionWin) {
      const bounds = companionWin.getBounds();
      companionWin.setBounds(fitBounds(bounds, screen.getDisplayMatching(bounds).workArea));
    }
    dockFloatingGroup();
  });
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => {
    if (clipboardWritePermission(contents, permission, details, UI_ORIGIN, [win, companionWin])) { callback(true); return; }
    if (!audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin])) { callback(false); return; }
    if (process.platform !== 'darwin') { callback(true); return; }
    void macMicrophoneConsent(systemPreferences).then(granted => {
      callback(granted && audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin]));
    }).catch(() => callback(false));
  });
  session.defaultSession.setPermissionCheckHandler((contents, permission, requestingOrigin, details) => {
    if (!sameOrigin(requestingOrigin, UI_ORIGIN)) return false;
    if (clipboardWritePermission(contents, permission, details, UI_ORIGIN, [win, companionWin])) return true;
    return audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin])
      && (process.platform !== 'darwin' || systemPreferences.getMediaAccessStatus('microphone') === 'granted');
  });
  // Display the splash before probing or starting Python.
  showStartup();
  try { createTray(); } catch (err) { console.warn('Tray unavailable:', err.message); }
  return start();
});
