'use strict';

const { app, BrowserWindow, Tray, Menu, Notification, nativeImage, nativeTheme, shell, ipcMain, session, systemPreferences, globalShortcut, screen, dialog, powerMonitor } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const { execFile } = require('node:child_process');
const { dashboardURL, sameOrigin, externalURL } = require('./navigation');
const { DesktopService } = require('./service');
const { voicePage, listenerPage, audioPermission, displayCapturePermission, clipboardWritePermission, macMicrophoneConsent,
  requestMicrophoneAccess, microphoneSettingsURL, systemAudioStreams } = require('./voice-permissions');
const { SHORTCUT, fitBounds, fitLauncher, point, dragBounds, clientPoint, trayImage, dockCompanion, orbSide, haloLayout } = require('./companion');
const { desktopRuntime } = require('./runtime');
const { readActivity, describeActivity } = require('./activity');
const { IDLE, recorderState: checkedRecorderState, recordingStart, recorderLevels } = require('./recording-relay');
const { WAKE_FILE, HOUR, wakeIntent, readWakeIntent, wakeChoice, pauseReason, wakeLabel } = require('./wake');

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

// While the dashboard is hidden, a shown companion docks beside the launcher's
// orb and either window's drag moves both (ADR 0070). The orb stays where the
// user put it; Echo slides along it to stay on screen (ADR 0135).
let echoSide = 0; // the side of the orb Echo docked on: 1 right, -1 left, 0 above or below
function echoVisible() {
  return !!companionWin && !companionWin.isDestroyed() && companionWin.isVisible();
}
function floatingGroup() {
  return wantLauncher && echoVisible() && !!launcherWin && !launcherWin.isDestroyed();
}
function placeFloatingGroup(group) {
  companionWin.setBounds(group.companion); launcherWin.setBounds(group.launcher);
  echoSide = group.side;
  saveCompanionBounds(companionWin, 'echo-window.json', group.companion);
  saveCompanionBounds(launcherWin, 'launcher-window.json', group.launcher);
  notifyLauncher();
}
function dockFloatingGroup(launcher = launcherWin?.getBounds()) {
  if (!floatingGroup()) return;
  const area = companionArea(launcher);
  placeFloatingGroup(dockCompanion(companionWin.getBounds(), launcher, area, echoSide || orbSide(launcher, area).side));
  companionWin.setAlwaysOnTop(true);
}
function finishFloatingGroup(targets) {
  if (!floatingGroup()) return false;
  // Native position reports can lag setPosition independently for each window;
  // dock to the position the drag intended, never a half-updated pair.
  dockFloatingGroup(targets?.launcher || launcherWin.getBounds());
  return true;
}

// The dashboard's theme choice (ADR 0154): the app follows it, so native
// surfaces and prefers-color-scheme in every window (the orb's ring, the
// splash) match the dashboard. Kept in the profile so the next start opens in
// it before any page loads.
const THEME_FILE = 'theme.json';
const THEME_BACKGROUND = { light: '#fafafa', dark: '#0a0a0a' }; // the dashboard's --background
function themeChoice(value) {
  return value === 'light' || value === 'dark' || value === 'system' ? value : null;
}
function savedTheme() {
  try { return themeChoice(JSON.parse(fs.readFileSync(path.join(app.getPath('userData'), THEME_FILE), 'utf8'))?.choice) ?? 'system'; }
  catch { return 'system'; }
}
function themeBackground() {
  return nativeTheme?.shouldUseDarkColors ? THEME_BACKGROUND.dark : THEME_BACKGROUND.light;
}
function applyTheme(choice) {
  if (nativeTheme && nativeTheme.themeSource !== choice) nativeTheme.themeSource = choice;
  // Opaque windows only: the launcher, its ring and Echo stay transparent.
  for (const window of [win, startupWin]) {
    if (window && !window.isDestroyed()) window.setBackgroundColor?.(themeBackground());
  }
}

const windowOptions = {
  title: 'Imprint',
  icon: path.join(__dirname, 'assets', 'icon.png'),
  get backgroundColor() { return themeBackground(); },
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
  closeHalo();
  if (recorder.phase !== 'idle') setRecorder(IDLE);
  reconcileWake();
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
  reconcileWake();
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
    closeHalo();
    launcherWin?.hide();
    watchLauncherActivity(false);
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
  if (launcherWin) { launcherWin.showInactive(); dockFloatingGroup(); watchLauncherActivity(true); return; }
  const saved = savedCompanionBounds('launcher-window.json');
  // acceptFirstMouse (macOS): the orb answers the press that also takes focus
  // back from its ring, so a double-click reaches it.
  const window = new BrowserWindow({ ...windowOptions, ...fitLauncher(saved, companionArea(saved)),
    title: 'Imprint launcher', transparent: true, backgroundColor: '#00000000', resizable: false,
    hasShadow: false, alwaysOnTop: true, skipTaskbar: true, acceptFirstMouse: true, minWidth: 48, minHeight: 48 });
  // Above its ring and Echo, so the orb is never covered (macOS levels; elsewhere moveTop).
  window.setAlwaysOnTop(true, 'floating', 2);
  launcherWin = window;
  window.on('closed', () => { if (launcherWin === window) launcherWin = null; launcherDrag = null; });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    if (url !== `${UI_ORIGIN}/launcher/`) event.preventDefault();
  });
  try {
    await window.loadURL(`${UI_ORIGIN}/launcher/`);
    if (!quitting && wantLauncher && !window.isDestroyed()) { window.showInactive(); dockFloatingGroup(); watchLauncherActivity(true); }
  } catch { if (!window.isDestroyed()) window.destroy(); }
}
function trustedLauncher(event) {
  return launcherWin?.webContents === event.sender
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && event.sender.getURL() === `${UI_ORIGIN}/launcher/`;
}
// The orb is a handle: a click opens its ring of actions, a double-click the
// dashboard, a drag moves it with everything docked to it (ADR 0135).
ipcMain.on('launcher-open', event => { if (trustedLauncher(event)) showWindow(); });
ipcMain.on('launcher-ring', (event, focus) => {
  if (!trustedLauncher(event)) return;
  // The press that took focus from the ring already closed it; its click must not reopen it.
  if (Date.now() - orbClosedRing < 1000) { orbClosedRing = 0; return; }
  if (halo) closeHalo();
  else void openHalo('ring', ['first', 'last'].includes(focus) ? focus : null);
});
ipcMain.handle('launcher-state', event => trustedLauncher(event) ? launcherState() : null);
ipcMain.on('launcher-menu', event => {
  if (!trustedLauncher(event)) return;
  closeHalo();
  const echo = echoVisible();
  Menu.buildFromTemplate([{ label: 'Open Dashboard', click: showWindow },
    { label: echo ? 'Collapse Echo' : 'Show Echo', accelerator: SHORTCUT, click: echo ? collapseCompanion : recoverCompanion },
    recorder.phase === 'recording' ? { label: 'Stop recording', click: stopRecorder }
      : { label: 'New recording…', enabled: recorder.phase === 'idle', click: () => void openHalo('record') },
    { type: 'separator' }, ...wakeMenu(), { label: 'Quit Imprint', click: () => app.quit() }]).popup({ window: launcherWin });
});
// Alt+arrow keys move the orb from the keyboard.
ipcMain.on('launcher-nudge', (event, delta) => {
  if (!trustedLauncher(event) || !point(delta) || Math.abs(delta.x) > 64 || Math.abs(delta.y) > 64) return;
  closeHalo();
  const bounds = launcherWin.getBounds();
  const next = fitLauncher({ ...bounds, x: bounds.x + delta.x, y: bounds.y + delta.y }, companionArea(bounds));
  launcherWin.setBounds(next);
  saveCompanionBounds(launcherWin, 'launcher-window.json', next);
  dockFloatingGroup(next);
});
ipcMain.on('launcher-drag-start', (event, origin) => {
  if (!trustedLauncher(event) || !point(origin)) return;
  orbClosedRing = 0;
  if (halo?.kind === 'ring') closeHalo();
  launcherDrag = { bounds: launcherWin.getBounds(), origin,
    companion: floatingGroup() ? companionWin.getBounds() : null, panel: halo && haloWin?.isVisible() ? haloWin.getBounds() : null };
});
ipcMain.on('launcher-drag-move', (event, current) => {
  if (!trustedLauncher(event) || !launcherDrag) return;
  const bounds = dragBounds(launcherDrag.bounds, launcherDrag.origin, current);
  if (!bounds) return;
  launcherWin.setPosition(bounds.x, bounds.y);
  launcherDrag.targets = { launcher: bounds };
  if (launcherDrag.companion && floatingGroup()) {
    const head = dragBounds(launcherDrag.companion, launcherDrag.origin, current);
    launcherDrag.targets.companion = head;
    companionWin.setPosition(head.x, head.y);
  }
  if (launcherDrag.panel && halo) {
    const panel = dragBounds(launcherDrag.panel, launcherDrag.origin, current);
    haloWin.setPosition(panel.x, panel.y);
  }
});
ipcMain.on('launcher-drag-end', event => {
  if (!trustedLauncher(event) || !launcherDrag) return;
  const { targets } = launcherDrag;
  launcherDrag = null;
  if (!finishFloatingGroup(targets)) {
    const bounds = targets?.launcher || launcherWin.getBounds();
    launcherWin.setBounds(fitLauncher(bounds, screen.getDisplayMatching(bounds).workArea));
    saveCompanionBounds(launcherWin, 'launcher-window.json');
  }
  // An open panel settles beside the orb again, on whichever side now has room.
  if (halo) void openHalo(halo.kind);
});

// The orb's ring of actions and its recording panels share one transparent
// window, shown only while one is open (ADR 0135). The orb's own window never
// resizes, so the orb never moves under the pointer.
const HALO_PAGE = () => `${UI_ORIGIN}/launcher/halo/`;
const PANELS = { record: { width: 344, height: 402 }, live: { width: 344, height: 256 } };
let haloWin = null;
let halo = null; // { kind: 'ring' | 'record' | 'live', focus, seq, size, layout }
let haloSeq = 0;
let orbClosedRing = 0;
let launcherActivity = null, launcherActivityTimer = null;

function trustedHalo(event) {
  return !!haloWin && haloWin.webContents === event.sender
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && event.sender.getURL() === HALO_PAGE();
}
function launcherState() {
  return { ring: halo?.kind === 'ring', panel: halo && halo.kind !== 'ring' ? halo.kind : null,
    echo: echoVisible(), echoSide: floatingGroup() ? echoSide : 0, recorder, activity: launcherActivity, wake: wakeView() };
}
function notifyLauncher() {
  if (launcherWin && !launcherWin.isDestroyed()) launcherWin.webContents.send('launcher-state', launcherState());
}
function haloPayload() {
  return halo?.layout ? { ...halo.layout, seq: halo.seq, focus: halo.focus, echo: echoVisible(), recorder } : null;
}
function createHalo() {
  const window = new BrowserWindow({ ...windowOptions, width: 290, height: 173,
    title: 'Imprint actions', transparent: true, backgroundColor: '#00000000', resizable: false,
    hasShadow: false, alwaysOnTop: true, skipTaskbar: true, acceptFirstMouse: true,
    // Hidden between uses; it must answer at once when shown.
    webPreferences: { ...windowOptions.webPreferences, backgroundThrottling: false } });
  window.setAlwaysOnTop(true, 'floating', 1);
  haloWin = window;
  window.on('closed', () => { if (haloWin === window) { haloWin = null; halo = null; notifyLauncher(); } });
  // Like a menu, the ring closes when the user clicks anywhere else. A press on
  // the orb also takes focus from it; that press only closes it.
  window.on('blur', () => {
    if (halo?.kind !== 'ring') return;
    const cursor = screen.getCursorScreenPoint(), orb = launcherWin?.getBounds();
    if (orb && cursor.x >= orb.x && cursor.x < orb.x + orb.width && cursor.y >= orb.y && cursor.y < orb.y + orb.height) orbClosedRing = Date.now();
    closeHalo();
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (url !== HALO_PAGE()) event.preventDefault(); });
  window.webContents.on('render-process-gone', () => window.destroy());
  window.loading = window.loadURL(HALO_PAGE()).then(() => true, () => { if (!window.isDestroyed()) window.destroy(); return false; });
  return window;
}
function placeHalo() {
  if (!halo || !launcherWin || launcherWin.isDestroyed()) return false;
  const launcher = launcherWin.getBounds(), area = companionArea(launcher);
  const side = orbSide(launcher, area, floatingGroup() ? echoSide : 0);
  halo.layout = haloLayout(launcher, area, side, halo.kind, halo.size || PANELS[halo.kind]);
  haloWin.setBounds(halo.layout.bounds);
  return true;
}
async function openHalo(kind, focus = null) {
  if (quitting || phase !== 'ready' || !wantLauncher || !launcherWin?.isVisible()) return;
  if (kind === 'record' && recorder.phase !== 'idle') kind = recorder.phase === 'recording' ? 'live' : 'ring';
  const changed = halo && halo.kind !== kind;
  halo = { kind, focus, seq: ++haloSeq, size: halo?.kind === kind ? halo.size : null, layout: null };
  orbClosedRing = 0;
  const window = haloWin || createHalo();
  // A different kind is drawn while hidden, so the old one never shows in the new bounds.
  if (changed && window.isVisible()) window.hide();
  const seq = halo.seq;
  if (!await window.loading || halo?.seq !== seq || window.isDestroyed() || !placeHalo()) return;
  window.webContents.send('halo-layout', haloPayload());
  notifyLauncher();
  // Shown once the page has drawn this layout (halo-ready), or after a moment regardless.
  setTimeout(() => showHalo(seq), 400);
}
function showHalo(seq) {
  if (!halo || halo.seq !== seq || halo.shown || !haloWin || haloWin.isDestroyed() || !launcherWin?.isVisible()) return;
  halo.shown = true;
  if (!haloWin.isVisible()) haloWin.show();
  haloWin.focus();
  launcherWin.moveTop();
}
function closeHalo() {
  if (!halo) return;
  halo = null;
  if (haloWin && !haloWin.isDestroyed()) {
    const window = haloWin;
    window.webContents.send('halo-layout', null);
    // The page plays its closing motion first.
    setTimeout(() => { if (!halo && !window.isDestroyed()) window.hide(); }, 160);
  }
  notifyLauncher();
}
ipcMain.handle('halo-layout', event => trustedHalo(event) ? haloPayload() : null);
ipcMain.on('halo-ready', (event, seq) => { if (trustedHalo(event)) showHalo(seq); });
ipcMain.on('halo-size', (event, height) => {
  if (!trustedHalo(event) || !halo || halo.kind === 'ring' || !Number.isFinite(height)) return;
  const size = { width: PANELS[halo.kind].width, height: Math.round(Math.max(160, Math.min(640, height))) };
  if (size.height === halo.layout?.bounds.height) return;
  halo.size = size;
  if (placeHalo()) haloWin.webContents.send('halo-layout', haloPayload());
});
ipcMain.on('halo-action', (event, action) => {
  if (!trustedHalo(event)) return;
  if (action === 'dashboard') { closeHalo(); showWindow(); }
  else if (action === 'echo') { closeHalo(); if (echoVisible()) collapseCompanion(); else void openCompanion(); }
  else if (action === 'record') void openHalo('record');
  else if (action === 'details') void openHalo('live');
  else if (action === 'stop') { closeHalo(); stopRecorder(); }
  else if (action === 'close') closeHalo();
  else if (action === 'microphone-settings') {
    const url = microphoneSettingsURL(process.platform);
    if (url) shell.openExternal(url).catch(() => {});
  }
});

// The orb shows the running job's progress, as the tray does (ADR 0089).
function refreshLauncherActivity() {
  const record = readActivity(dataDir);
  const activity = record ? { text: describeActivity(record),
    percent: record.total > 0 ? Math.floor((100 * Math.min(record.processed, record.total)) / record.total) : null } : null;
  if (JSON.stringify(activity) === JSON.stringify(launcherActivity)) return;
  launcherActivity = activity;
  notifyLauncher();
}
function watchLauncherActivity(watching) {
  if (!watching) { clearInterval(launcherActivityTimer); launcherActivityTimer = null; return; }
  refreshLauncherActivity();
  launcherActivityTimer ??= setInterval(refreshLauncherActivity, 2000);
}

// Meeting recordings run in the dashboard window, shown or hidden (ADR 0131).
// The orb starts and stops them there and shows what the dashboard reports.
let recorder = IDLE;
function setRecorder(state) {
  recorder = state;
  // A hidden window's timers slow down; the recording's levels and uploads must not.
  if (win && !win.isDestroyed()) win.webContents.setBackgroundThrottling(recorder.phase === 'idle');
  // A started recording folds its panel into the orb; a finished one closes its details.
  if ((halo?.kind === 'record' && recorder.phase === 'recording') || (halo?.kind === 'live' && recorder.phase === 'idle')) closeHalo();
  for (const window of [launcherWin, haloWin]) {
    if (window && !window.isDestroyed()) window.webContents.send('recorder-state', recorder);
  }
  notifyLauncher();
  reconcileWake();
}
function stopRecorder() {
  if (recorder.phase === 'recording' && win && !win.isDestroyed()) win.webContents.send('recorder-command', { type: 'stop' });
}
ipcMain.on('recorder-state', (event, value) => {
  const state = trustedDashboardSender(event) && checkedRecorderState(value);
  if (state) setRecorder(state);
});
ipcMain.on('recorder-levels', (event, value) => {
  const levels = trustedDashboardSender(event) && recorderLevels(value);
  if (!levels) return;
  for (const window of [launcherWin, haloWin]) {
    if (window && !window.isDestroyed() && window.isVisible()) window.webContents.send('recorder-levels', levels);
  }
});
ipcMain.handle('launcher-recorder', event => trustedLauncher(event) || trustedHalo(event) ? recorder : null);
ipcMain.on('launcher-record', (event, value) => {
  const start = (trustedLauncher(event) || trustedHalo(event)) && recordingStart(value);
  if (!start || recorder.phase !== 'idle' || !win || win.isDestroyed()) return;
  // The hidden dashboard first moves to the Recordings page; unthrottled, it does so at once.
  win.webContents.setBackgroundThrottling(false);
  win.webContents.send('recorder-command', { type: 'start', title: start.title, project: start.project, others: start.others });
});
ipcMain.on('launcher-record-stop', event => { if (trustedLauncher(event) || trustedHalo(event)) stopRecorder(); });

// "Hey Echo" (ADR 0137). While the user's choice, the app and the moment allow,
// a hidden window listens; every indicator (the orb's ear dot, the dashboard's
// top-bar chip, the tray line) follows whether that window exists.
const WAKE_PAGE = () => `${UI_ORIGIN}/wake/`;
const wake = { intent: readWakeIntent(() => fs.readFileSync(path.join(app.getPath('userData'), WAKE_FILE), 'utf8')),
  win: null, blocked: '', retryAt: 0, timer: null, session: null, checking: false, heard: 0 };
const system = { locked: false, asleep: false, onBattery: false, thermal: 'nominal' };
const echoMicrophones = new Map(); // a voice page's webContents → its cleanup, while its microphone is on

function saveWakeIntent() {
  try {
    const directory = app.getPath('userData');
    fs.mkdirSync(directory, { recursive: true });
    const file = path.join(directory, WAKE_FILE);
    fs.writeFileSync(file + '.tmp', JSON.stringify(wake.intent), { mode: 0o600 });
    fs.renameSync(file + '.tmp', file);
  } catch { /* The choice holds for this run even if it cannot be kept. */ }
}
function wakeListening() { return !!wake.win && !wake.win.isDestroyed(); }
function wakeReason() {
  return pauseReason({ intent: wake.intent, blocked: !!wake.blocked, phase, now: Date.now(), recording: recorder.phase !== 'idle',
    conversation: echoMicrophones.size > 0, locked: system.locked, asleep: system.asleep, onBattery: system.onBattery,
    thermal: system.thermal, retryAt: wake.retryAt });
}
function wakeView() {
  return { enabled: wake.intent.enabled, listening: wakeListening(), reason: wakeReason(), pausedUntil: wake.intent.pausedUntil,
    battery: wake.intent.battery, problem: wake.blocked, checking: wakeListening() && wake.checking, heard: wake.heard };
}
function notifyWake() {
  const view = wakeView();
  for (const window of [win, companionWin]) {
    if (window && !window.isDestroyed()) window.webContents.send('wake-state', view);
  }
  notifyLauncher();
  refreshTray(true);
}
function reconcileWake() {
  if (quitting) return;
  clearTimeout(wake.timer); wake.timer = null;
  const reason = wakeReason();
  // A wake hands its question over before the listener goes.
  const handover = wake.session && !wake.session.delivered;
  if (!reason && !wakeListening()) openWakeListener();
  else if (reason && wakeListening() && !handover) wake.win.destroy();
  // Look again when a pause or a retry ends.
  const next = [wake.intent.pausedUntil, wake.retryAt].filter(at => at > Date.now());
  if (wake.intent.enabled && next.length) {
    wake.timer = setTimeout(reconcileWake, Math.min(...next) - Date.now() + 50);
    wake.timer?.unref?.();
  }
  notifyWake();
}
function openWakeListener() {
  const window = new BrowserWindow({ ...windowOptions, width: 160, height: 90, title: 'Imprint listener', skipTaskbar: true, focusable: false,
    // Never shown: it must keep its audio running and start it without a click.
    webPreferences: { ...windowOptions.webPreferences, backgroundThrottling: false, autoplayPolicy: 'no-user-gesture-required' } });
  wake.win = window; wake.checking = false;
  window.on('closed', () => { if (wake.win === window) { wake.win = null; wake.checking = false; notifyWake(); } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (url !== WAKE_PAGE()) event.preventDefault(); });
  window.webContents.on('render-process-gone', () => retryWake());
  window.loadURL(WAKE_PAGE()).catch(() => { if (wake.win === window) retryWake(); });
}
// The service went away or the page failed: try again in a while.
function retryWake() {
  wake.retryAt = Date.now() + 30000;
  if (wakeListening()) wake.win.destroy();
  reconcileWake();
}
function sendWakePower() {
  if (wakeListening()) wake.win.webContents.send('wake-listener-config', { battery: system.onBattery });
}
function controlWake(action) {
  if (!wake.intent.enabled) return;
  if (action === 'pause') wake.intent = { ...wake.intent, pausedUntil: Date.now() + HOUR };
  else if (action === 'resume') wake.intent = { ...wake.intent, pausedUntil: 0 };
  else if (action === 'off') wake.intent = { ...wake.intent, enabled: false, pausedUntil: 0 };
  else return;
  saveWakeIntent(); reconcileWake();
}
function wakeMenu() {
  if (!wake.intent.enabled) return [];
  return [{ label: wakeLabel(wakeView()), enabled: false },
    wake.intent.pausedUntil > Date.now() ? { label: 'Resume listening', click: () => controlWake('resume') }
      : { label: 'Pause listening for 1 hour', click: () => controlWake('pause') },
    { label: 'Turn off “Hey Echo”', click: () => controlWake('off') }, { type: 'separator' }];
}
function trustedWakeListener(event) {
  return wakeListening() && wake.win.webContents === event.sender
    && (!event.senderFrame || event.senderFrame === event.sender.mainFrame)
    && listenerPage(event.sender.getURL(), UI_ORIGIN);
}
// On "Hey Echo", Echo appears without taking focus and starts at once; the
// rest of the sentence follows as its first question. Afterwards Echo goes
// back where it was: folded, closed or open.
async function startWakeSession(id) {
  if (wake.session || quitting || phase !== 'ready') return;
  const before = !companionWin || companionWin.isDestroyed() ? 'closed' : echoVisible() ? 'open' : 'folded';
  const session = wake.session = { id, before, text: null, delivered: false, timer: null };
  // Without its question in 35 s, the listener goes anyway.
  session.timer = setTimeout(() => { if (wake.session === session && !session.delivered) { session.delivered = true; reconcileWake(); } }, 35000);
  session.timer?.unref?.();
  wake.heard = Date.now();
  notifyWake();
  await openCompanion(false, true);
  if (wake.session === session) companionWin?.webContents.send('voice-wake', { type: 'start', id });
}
function deliverWakeQuestion(text) {
  const session = wake.session;
  session.text = text; session.delivered = true; clearTimeout(session.timer);
  companionWin?.webContents.send('voice-wake', { type: 'question', id: session.id, text });
  reconcileWake();
}
function endWakeSession(restore) {
  const session = wake.session;
  if (!session) return;
  wake.session = null; clearTimeout(session.timer);
  if (restore && session.before === 'folded') collapseCompanion();
  else if (restore && session.before === 'closed' && companionWin) { saveCompanionBounds(); companionWin.destroy(); }
  reconcileWake();
}
ipcMain.handle('wake-listener-config', event => trustedWakeListener(event) ? { battery: system.onBattery } : null);
ipcMain.on('wake-listener', (event, report) => {
  if (!trustedWakeListener(event) || !report || typeof report !== 'object') return;
  if (report.type === 'listening') wake.retryAt = 0;
  else if (report.type === 'checking') { wake.checking = report.on === true; notifyLauncher(); }
  else if (report.type === 'heard' && Number.isSafeInteger(report.id)) void startWakeSession(report.id);
  else if (report.type === 'question' && wake.session && wake.session.id === report.id && typeof report.text === 'string') {
    deliverWakeQuestion(report.text.slice(0, 2000));
  } else if (report.type === 'error') {
    // A refused or missing microphone, or a missing model, waits for the user; anything else is tried again.
    if (report.kind !== 'microphone' && report.kind !== 'model') { retryWake(); return; }
    wake.blocked = typeof report.message === 'string' && report.message ? report.message.slice(0, 400) : 'The microphone is unavailable.';
    wake.win.destroy();
    reconcileWake();
  }
});
ipcMain.handle('voice-wake-state', event => trustedVoiceSender(event) || trustedDashboardSender(event) || trustedLauncher(event) ? wakeView() : null);
// Turning it on comes from Echo's settings after the explanation and the OS consent step.
ipcMain.handle('voice-wake-set', (event, value) => {
  const choice = trustedVoiceSender(event) && wakeChoice(value);
  if (!choice) return null;
  wake.intent = wakeIntent({ ...wake.intent, ...choice, pausedUntil: 0 });
  if (choice.enabled) { wake.blocked = ''; wake.retryAt = 0; }
  saveWakeIntent(); reconcileWake();
  return wakeView();
});
ipcMain.on('wake-control', (event, action) => {
  if (trustedDashboardSender(event) || (companionWin?.webContents === event.sender && trustedVoiceSender(event))) controlWake(action);
});
ipcMain.on('voice-microphone-state', (event, on) => {
  if (typeof on !== 'boolean' || !trustedVoiceSender(event)) return;
  const contents = event.sender;
  if (on && !echoMicrophones.has(contents)) {
    const gone = () => { if (echoMicrophones.delete(contents)) reconcileWake(); };
    echoMicrophones.set(contents, gone);
    contents.once?.('destroyed', gone); contents.once?.('did-navigate', gone);
  } else if (!on && echoMicrophones.has(contents)) {
    const gone = echoMicrophones.get(contents);
    echoMicrophones.delete(contents);
    contents.removeListener?.('destroyed', gone); contents.removeListener?.('did-navigate', gone);
  } else return;
  reconcileWake();
});
ipcMain.handle('voice-wake-session', event => companionWin?.webContents === event.sender && trustedVoiceSender(event) && wake.session
  ? { id: wake.session.id, text: wake.session.text } : null);
ipcMain.on('voice-wake-done', (event, value) => {
  if (companionWin?.webContents !== event.sender || !trustedVoiceSender(event) || !wake.session || value?.id !== wake.session.id) return;
  endWakeSession(value.restore === true);
});

// The tray says what the running job is doing (ADR 0089): in its tooltip, as
// the first line of its menu, which Linux trays show when they skip tooltips,
// and on macOS as the percentage beside the menu bar icon.
let trayKey = null;

function trayMenu(activity) {
  return Menu.buildFromTemplate([
    ...(activity ? [{ label: activity, enabled: false }, { type: 'separator' }] : []),
    ...wakeMenu(),
    { label: 'Open Dashboard', click: showWindow },
    { label: 'Float Echo', accelerator: SHORTCUT, click: recoverCompanion },
    { type: 'separator' },
    { label: 'Quit Imprint', click: () => app.quit() },
  ]);
}

function refreshTray(force = false) {
  if (!tray) return;
  const record = readActivity(dataDir);
  const activity = record ? describeActivity(record) : '';
  // The tray also says whether Imprint is listening for "Hey Echo" (ADR 0137).
  const listening = wakeLabel(wakeView());
  const key = JSON.stringify([activity, listening]);
  if (key === trayKey && force !== true) return;
  trayKey = key;
  tray.setToolTip(['Imprint', activity, listening].filter(Boolean).join(' · '));
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
// Only the dashboard's own page chooses the theme, and only one of three values.
ipcMain.on('theme-set', (event, value) => {
  const choice = themeChoice(value);
  if (!choice || !trustedDashboardSender(event)) return;
  applyTheme(choice);
  try {
    const directory = app.getPath('userData');
    fs.mkdirSync(directory, { recursive: true });
    const file = path.join(directory, THEME_FILE);
    fs.writeFileSync(file + '.tmp', JSON.stringify({ choice }), { mode: 0o600 });
    fs.renameSync(file + '.tmp', file);
  } catch { /* Not remembered for the next start; applied all the same. */ }
});
// A reminder rang (ADR 0158). The dashboard, shown or hidden, raises it; the
// OS shows it unless the dashboard is in front, where its toast is enough.
// Clicking it shows the dashboard on the reminder.
const reminderNotes = new Map();
function reminderAlert(value) {
  if (!value || typeof value !== 'object') return null;
  const { id, title, body } = value;
  if (typeof id !== 'string' || !/^[a-f0-9]{32}$/.test(id) || typeof title !== 'string') return null;
  return { id, title: title.slice(0, 200), body: typeof body === 'string' ? body.slice(0, 500) : '' };
}
ipcMain.on('reminder-notify', (event, value) => {
  const alert = reminderAlert(value);
  if (!alert || !trustedDashboardSender(event)) return;
  if (win && win.isVisible() && win.isFocused()) return;
  if (!Notification?.isSupported?.()) return;
  const note = new Notification({ title: alert.title, body: alert.body, silent: false });
  reminderNotes.set(alert.id, note); // Kept, or the OS loses its click handler.
  note.on('click', () => {
    reminderNotes.delete(alert.id);
    showWindow();
    if (win && !win.isDestroyed()) win.webContents.send('reminder-open', alert.id);
  });
  note.on('close', () => reminderNotes.delete(alert.id));
  note.show();
});
// Enable microphone asks the OS first (ADR 0069). Consent can take as long as the
// user leaves the dialog open, so the sender is checked again afterwards.
ipcMain.handle('voice-microphone-access', async event => {
  const trusted = () => trustedVoiceSender(event) || trustedWakeListener(event);
  if (!trusted()) return { status: 'denied' };
  const result = await requestMicrophoneAccess(process.platform, systemPreferences);
  return trusted() ? result : { status: 'denied' };
});
ipcMain.handle('voice-microphone-settings', async event => {
  const url = microphoneSettingsURL(process.platform);
  if (!trustedVoiceSender(event) || !url) return false;
  try { await shell.openExternal(url); return true; } catch { return false; }
});
function companionState() {
  return { open: echoVisible(), docked: floatingGroup(), pinned: companionPinned, shortcut: SHORTCUT, shortcutAvailable };
}
function notifyCompanion() {
  const state = companionState();
  for (const window of [win, companionWin]) {
    if (window && !window.isDestroyed()) window.webContents.send('voice-companion-state', state);
  }
  notifyLauncher();
}
// Echo folds into the orb: the window hides and keeps its place; hidden, its page
// releases the microphone and lets go of the conversation, whose reply goes on in
// the API and is picked up when Echo shows again (ADR 0069, 0135, 0145).
function collapseCompanion() {
  if (!echoVisible()) return;
  saveCompanionBounds(); companionWin.hide(); notifyCompanion();
}
// `inactive` shows Echo without taking focus from the app the user is in (a wake, ADR 0137).
async function openCompanion(recover = false, inactive = false) {
  if (quitting || phase !== 'ready') return;
  if (companionWin) {
    companionWin.setBounds(fitBounds(companionWin.getBounds(), companionArea(recover ? null : companionWin.getBounds()), recover));
    if (companionWin.isMinimized()) companionWin.restore();
    if (recover && process.platform === 'darwin') app.focus?.({ steal: true });
    if (inactive) companionWin.showInactive();
    else { companionWin.show(); companionWin.moveTop(); companionWin.focus(); }
    saveCompanionBounds(); dockFloatingGroup(); notifyCompanion(); return;
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
  window.on('closed', () => { if (companionWin === window) companionWin = null; companionDrag = null; notifyCompanion(); endWakeSession(false); });
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
    if (!quitting && !window.isDestroyed()) {
      if (inactive) window.showInactive(); else { window.show(); window.focus(); }
      dockFloatingGroup(); notifyCompanion();
    }
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
  closeHalo();
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
ipcMain.on('voice-collapse-companion', event => {
  if (companionWin?.webContents === event.sender && trustedVoiceSender(event)) collapseCompanion();
});
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
// Quitting stops the dashboard that records a meeting; ask first, and let the
// recording finish saving before the app goes.
let quitWhileRecording = false;
async function confirmQuitWhileRecording() {
  const { response } = await dialog.showMessageBox({ type: 'question', buttons: ['Stop and quit', 'Cancel'], defaultId: 0, cancelId: 1,
    message: 'Stop the recording and quit?', detail: 'Imprint saves what was recorded so far and transcribes it.' });
  if (response !== 0) return;
  quitWhileRecording = true;
  stopRecorder();
  for (let waited = 0; recorder.phase !== 'idle' && waited < 15000; waited += 100) await new Promise(resolve => setTimeout(resolve, 100));
  app.quit();
}
app.on('before-quit', event => {
  if (cleanupFinished) return;
  event.preventDefault();
  if (quitting) return;
  if (recorder.phase === 'recording' && !quitWhileRecording) { void confirmQuitWhileRecording(); return; }
  quitting = true;
  globalShortcut.unregisterAll();
  ++attempt;
  win?.hide();
  startupWin?.hide();
  // Releasing this renderer also closes its mic/socket on a reused daemon.
  saveCompanionBounds(); companionWin?.destroy();
  saveCompanionBounds(launcherWin, 'launcher-window.json'); launcherWin?.destroy(); haloWin?.destroy(); wake.win?.destroy();
  Promise.resolve(service?.stop()).finally(() => {
    cleanupFinished = true;
    app.quit();
  });
});

app.whenReady().then(() => {
  applyTheme(savedTheme());
  nativeTheme?.on?.('updated', () => applyTheme(nativeTheme.themeSource));
  try { shortcutAvailable = globalShortcut.register(SHORTCUT, () => { void recoverCompanion(); }); }
  catch { shortcutAvailable = false; }
  screen.on('display-removed', () => {
    closeHalo();
    if (companionWin) void recoverCompanion();
    if (launcherWin) launcherWin.setBounds(fitLauncher(null, companionArea()));
    dockFloatingGroup();
  });
  screen.on('display-metrics-changed', () => {
    closeHalo();
    if (launcherWin) launcherWin.setBounds(fitLauncher(launcherWin.getBounds(), screen.getDisplayMatching(launcherWin.getBounds()).workArea));
    if (companionWin) {
      const bounds = companionWin.getBounds();
      companionWin.setBounds(fitBounds(bounds, screen.getDisplayMatching(bounds).workArea));
    }
    dockFloatingGroup();
  });
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => {
    if (clipboardWritePermission(contents, permission, details, UI_ORIGIN, [win, companionWin])) { callback(true); return; }
    // A meeting recording's computer audio (ADR 0131); the display-media handler below picks what it gets.
    if (displayCapturePermission(contents, permission, details, UI_ORIGIN, win)) { callback(true); return; }
    if (!audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin], wake.win)) { callback(false); return; }
    if (process.platform !== 'darwin') { callback(true); return; }
    void macMicrophoneConsent(systemPreferences).then(granted => {
      callback(granted && audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin], wake.win));
    }).catch(() => callback(false));
  });
  // Meeting recordings take the system's audio without a picker; any other
  // screen-capture request is refused (ADR 0131).
  // null refuses; the callback throws when the page has gone meanwhile, and the request is refused then too.
  session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
    try { callback(systemAudioStreams(request, UI_ORIGIN, win)); } catch { /* refused */ }
  });
  session.defaultSession.setPermissionCheckHandler((contents, permission, requestingOrigin, details) => {
    if (!sameOrigin(requestingOrigin, UI_ORIGIN)) return false;
    if (clipboardWritePermission(contents, permission, details, UI_ORIGIN, [win, companionWin])) return true;
    if (displayCapturePermission(contents, permission, details, UI_ORIGIN, win)) return true;
    return audioPermission(contents, permission, details, UI_ORIGIN, [win, companionWin], wake.win)
      && (process.platform !== 'darwin' || systemPreferences.getMediaAccessStatus('microphone') === 'granted');
  });
  // Listening for "Hey Echo" pauses while the screen is locked or the computer
  // sleeps, and on battery when the user chose that (ADR 0137).
  system.onBattery = powerMonitor?.isOnBatteryPower?.() === true;
  const power = (name, change) => powerMonitor?.on?.(name, (...args) => { change(...args); reconcileWake(); });
  power('lock-screen', () => { system.locked = true; });
  power('unlock-screen', () => { system.locked = false; });
  power('suspend', () => { system.asleep = true; });
  power('resume', () => { system.asleep = false; });
  power('on-battery', () => { system.onBattery = true; sendWakePower(); });
  power('on-ac', () => { system.onBattery = false; sendWakePower(); });
  power('thermal-state-change', (event, state) => { system.thermal = typeof state === 'string' ? state : event?.state || 'unknown'; });
  // Display the splash before probing or starting Python.
  showStartup();
  try { createTray(); } catch (err) { console.warn('Tray unavailable:', err.message); }
  return start();
});
