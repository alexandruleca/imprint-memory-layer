'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the actual main process with Electron windows and processes replaced
// by observable doubles; these do not establish native graphical behavior.
const RUNTIME_ARGS = ['--project-dir=/source', '--data-dir=/data', '--python=/writable/venv/python'];
function harness({ alreadyUp = false, failSpawn = false, trayAvailable = true, dashboardPending = false, packaged = true, savedPosition = null,
  runtimeArgs = RUNTIME_ARGS, cli = () => [new Error('spawn ENOENT'), ''], platform = 'darwin',
  mediaStatus = 'granted', consent = async () => true, activity = null, servicePending = false } = {}) {
  const windows = [];
  const services = [];
  const calls = [];
  const ipcMain = new EventEmitter();
  const handlers = {};
  ipcMain.handle = (name, handler) => { handlers[name] = handler; };
  const app = new EventEmitter();
  app.isPackaged = packaged;
  app.requestSingleInstanceLock = () => true;
  app.whenReady = () => Promise.resolve();
  app.getPath = () => '/disposable-profile';
  app.focus = () => {};
  if (platform === 'darwin') app.dock = { show() { calls.push('dock-show'); }, hide() { calls.push('dock-hide'); } };
  app.quit = () => {
    const e = { prevented: false, preventDefault() { this.prevented = true; } };
    app.emit('before-quit', e);
    if (!e.prevented) calls.push('quit');
  };
  class Window extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.webContents = new EventEmitter();
      this.webContents.send = () => {};
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.getURL = () => this.url || '';
      this.webContents.getZoomFactor = () => 1;
      this.visible = false;
      this.destroyed = false;
      windows.push(this);
    }
    async loadFile() { calls.push('splash'); this.emit('ready-to-show'); }
    loadURL(url) {
      this.url = url;
      calls.push('dashboard');
      return dashboardPending ? new Promise(resolve => { this.loaded = resolve; }) : Promise.resolve();
    }
    show() { this.visible = true; }
    hide() { this.visible = false; }
    focus() {}
    moveTop() {}
    showInactive() { this.visible = true; }
    isVisible() { return this.visible; }
    getBounds() { return this.bounds || { x: this.options.x || 0, y: this.options.y || 0, width: this.options.width, height: this.options.height }; }
    getContentBounds() { return this.getBounds(); }
    setBounds(bounds) { this.bounds = bounds; }
    setPosition(x, y) { this.bounds = { ...this.getBounds(), x, y }; }
    setAlwaysOnTop(pinned) { this.pinned = pinned; }
    isAlwaysOnTop() { return !!this.pinned; }
    isDestroyed() { return this.destroyed; }
    isMinimized() { return !!this.minimized; }
    minimize() { this.minimized = true; }
    restore() { this.minimized = false; }
    destroy() { this.destroyed = true; this.emit('closed'); }
    close() { this.emit('close', { preventDefault() {} }); }
    static fromWebContents(sender) { return windows.find(w => w.webContents === sender); }
  }
  class Service {
    constructor(options) { Object.assign(this, options); this.logPath = '/tmp/desktop.log'; services.push(this); }
    start(onFailure) {
      calls.push('service');
      this.onFailure = onFailure;
      if (failSpawn) queueMicrotask(() => onFailure('Missing Python'));
    }
    async stop() { this.stopped = true; calls.push('stop'); }
  }
  class Tray extends EventEmitter {
    constructor() { super(); if (!trayAvailable) throw new Error('No tray'); }
    setToolTip(text) { calls.tooltip = text; }
    setTitle(text) { calls.title = text; }
    setContextMenu(menu) { calls.menu = menu; }
  }
  const electron = {
    app, BrowserWindow: Window, Tray, Menu: { buildFromTemplate: menu => menu },
    nativeImage: { createFromPath: () => ({ isEmpty: () => false, getSize: () => ({ width: 32, height: 32 }), resize: size => { calls.traySize = size; return { isEmpty: () => false }; } }) },
    shell: { openExternal(url) { calls.push(['open-external', url]); }, openPath() {} }, ipcMain,
    systemPreferences: { getMediaAccessStatus() { calls.push('media-status'); return mediaStatus; },
      askForMediaAccess() { calls.push('media-consent'); return consent(); } },
    session: { defaultSession: { setPermissionRequestHandler(handler) { calls.request = handler; },
      setPermissionCheckHandler(handler) { calls.check = handler; } } },
  };
  const shortcuts = new Map();
  electron.globalShortcut = { register: (key, action) => { shortcuts.set(key, action); return true; }, unregisterAll: () => shortcuts.clear() };
  electron.screen = Object.assign(new EventEmitter(), { getCursorScreenPoint: () => ({ x: 800, y: 400 }),
    getDisplayNearestPoint: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1080 } }),
    getDisplayMatching: bounds => ({ workArea: { x: bounds.x >= 1920 ? 1920 : 0, y: 0, width: 1920, height: 1080 } }) });
  const http = {
    get(_url, _options, callback) {
      calls.push('ping');
      const req = new EventEmitter();
      req.destroy = () => {};
      const up = alreadyUp || (!servicePending && services.some(service => !service.stopped));
      queueMicrotask(() => callback({ statusCode: up ? 200 : 503, resume() {} }));
      return req;
    },
  };
  const intervals = new Set();
  const childProcess = { execFile(file, args, options, callback) {
    calls.push(['cli', file, ...args]);
    queueMicrotask(() => callback(...cli()));
  } };
  const modules = { electron, 'node:path': path, 'node:http': http, 'node:child_process': childProcess, './runtime': require('./runtime'),
    'node:fs': { readFileSync: () => { if (savedPosition) return JSON.stringify(savedPosition); throw Error('No saved position'); }, mkdirSync() {}, writeFileSync() {}, renameSync() {} },
    './companion': require('./companion'),
    './navigation': require('./navigation'), './service': { DesktopService: Service },
    './activity': { readActivity: dir => { calls.push(['activity', dir]); return typeof activity === 'function' ? activity() : activity; },
      describeActivity: require('./activity').describeActivity },
    './voice-permissions': require('./voice-permissions') };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8'), {
    require: name => { assert.ok(name in modules, name); return modules[name]; },
    process: { argv: ['electron', ...packaged ? [] : ['main.js'], ...runtimeArgs], platform, env: {},
      execPath: '/opt/imprint/bin/imprint-ui.app/Contents/MacOS/Imprint' },
    __dirname, console: { warn() {}, error() {} }, setTimeout, Promise, URL, Date,
    setInterval: tick => { intervals.add(tick); return tick; }, clearInterval: tick => intervals.delete(tick),
  });
  return { app, windows, services, calls, ipcMain, shortcuts, intervals, handlers, tick: () => intervals.forEach(tick => tick()),
    invoke: (name, window, ...args) => handlers[name]({ sender: window.webContents }, ...args),
    emit: (name, window, ...args) => ipcMain.emit(name, { sender: window.webContents }, ...args) };
}

async function settle() {
  for (let i = 0; i < 25; i++) await Promise.resolve();
}

test('splash precedes probes/service and stays until dashboard finishes loading', async () => {
  const h = harness({ dashboardPending: true });
  await settle();
  assert.ok(h.calls.indexOf('splash') < h.calls.indexOf('ping'));
  assert.ok(h.calls.indexOf('splash') < h.calls.indexOf('service'));
  assert.equal(h.services[0].python, '/writable/venv/python');
  assert.equal(h.services[0].projectDir, '/source');
  assert.equal(h.windows[0].visible, true);
  assert.equal(h.windows[1].visible, false);
  h.windows[1].loaded();
  await settle();
  assert.equal(h.windows[0].destroyed, true);
  assert.equal(h.windows[1].visible, true);
});

const SETUP = { pid: 7, command: 'setup', phase: 'installing llama.cpp', processed: 1, total: 3, unit: 'steps' };

test('while setup holds the service back the splash says what it does', async () => {
  const h = harness({ activity: SETUP, servicePending: true });
  // Until the startup loop has read the activity, however slow the machine.
  for (const end = Date.now() + 30000; !h.calls.some(call => call[0] === 'activity') && Date.now() < end;) {
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.deepEqual(h.calls.find(call => call[0] === 'activity'), ['activity', '/data']);
  assert.equal(h.invoke('startup-state', h.windows[0]).message, 'Waiting for setup: installing llama.cpp (1 of 3 steps, 33%)…');
  assert.equal(h.invoke('startup-state', h.windows[0]).phase, 'starting');
  h.app.quit();
});

test('the dashboard reads the running job from the app while its server is down', async () => {
  const h = harness({ alreadyUp: true, activity: SETUP });
  await settle();
  assert.deepEqual(h.invoke('activity', h.windows[1]), SETUP);
  assert.equal(h.invoke('activity', h.windows[0]), null);
});

test('dashboard toggle opens and destroys Echo from any app page, reflecting manual close', async () => {
  const h = harness({ alreadyUp: true });
  await settle();
  const dashboard = h.windows[1];
  assert.equal(h.invoke('voice-companion-state', dashboard).open, false);
  assert.equal(await h.invoke('voice-set-companion', dashboard, true).then(state => state.open), true);
  const companion = h.windows[2];
  assert.equal(h.invoke('voice-companion-state', dashboard).open, true);
  assert.equal(h.invoke('voice-companion-state', h.windows[0]), null);
  await h.invoke('voice-set-companion', dashboard, false);
  assert.equal(companion.destroyed, true);
  assert.equal(h.invoke('voice-companion-state', dashboard).open, false);
  await h.invoke('voice-set-companion', dashboard, true);
  h.windows[3].destroy();
  assert.equal(h.invoke('voice-companion-state', dashboard).open, false);
});

test('an OS relaunch without arguments asks the bundled CLI for the runtime', async () => {
  // Reopen after a crash, the Dock or Spotlight start the bundle with no arguments.
  const h = harness({ runtimeArgs: [], cli: () => [null, JSON.stringify({ project_dir: '/opt/imprint',
    data_dir: '/home/a/.local/share/imprint/data', python: '/home/a/.local/share/imprint/.venv/bin/python' })] });
  await settle();
  assert.ok(h.calls.some(call => Array.isArray(call) && call.join(' ') === 'cli /opt/imprint/bin/imprint ui runtime'));
  assert.equal(h.services[0].projectDir, '/opt/imprint');
  assert.equal(h.services[0].dataDir, '/home/a/.local/share/imprint/data');
  assert.equal(h.services[0].python, '/home/a/.local/share/imprint/.venv/bin/python');
});

test('a relaunch whose CLI is missing shows why on the startup screen', async () => {
  const h = harness({ runtimeArgs: [] });
  await settle();
  assert.equal(h.services.length, 0);
  assert.equal(h.calls.includes('dashboard'), false);
  assert.equal(h.windows[0].visible, true);
  assert.match(await h.invoke('startup-state', h.windows[0]).message, /could not locate its runtime/);
});

test('a running service needs no runtime, with or without arguments', async () => {
  const h = harness({ alreadyUp: true, runtimeArgs: [] });
  await settle();
  assert.equal(h.calls.some(call => Array.isArray(call) && call[0] === 'cli'), false);
  assert.ok(h.calls.includes('dashboard'));
});

test('development launch also retains the first runtime option', async () => {
  const h = harness({ packaged: false });
  await settle();
  assert.equal(h.services[0].projectDir, '/source');
});

test('close hides without stopping, second launch restores, Quit stops owned API', async () => {
  const h = harness();
  await settle();
  const dashboard = h.windows[1];
  h.emit('win-close', dashboard);
  assert.equal(dashboard.visible, false);
  assert.equal(h.services[0].stopped, undefined);
  h.app.emit('second-instance');
  assert.equal(dashboard.visible, true);
  h.emit('app-quit', dashboard);
  await settle();
  assert.equal(h.services[0].stopped, true);
  assert.ok(h.calls.includes('quit'));
});

test('reuses independent daemon and explicit Quit leaves it running', async () => {
  const h = harness({ alreadyUp: true });
  await settle();
  assert.equal(h.services.length, 0);
  h.calls.menu.find(item => item.label === 'Quit Imprint').click();
  await settle();
  assert.ok(h.calls.includes('quit'));
  assert.equal(h.calls.includes('stop'), false);
});

test('macOS: the Dock icon goes to the menu bar with the dashboard and comes back with it', async () => {
  const h = harness();
  await settle();
  const dashboard = h.windows[1];
  h.emit('win-close', dashboard);
  await settle();
  assert.equal(dashboard.visible, false);
  assert.equal(h.calls.filter(c => String(c).startsWith('dock-')).at(-1), 'dock-hide');
  h.app.emit('second-instance');
  assert.equal(dashboard.visible, true);
  assert.equal(h.calls.filter(c => String(c).startsWith('dock-')).at(-1), 'dock-show');
  dashboard.emit('minimize');
  await settle();
  assert.equal(h.calls.filter(c => String(c).startsWith('dock-')).at(-1), 'dock-hide');
});

test('macOS without a menu bar icon keeps the Dock icon, the only way back', async () => {
  const h = harness({ trayAvailable: false });
  await settle();
  h.emit('win-close', h.windows[1]);
  await settle();
  assert.equal(h.calls.includes('dock-hide'), false);
});

test('Echo stays off the taskbar like the launcher', async () => {
  const h = harness({ alreadyUp: true, platform: 'linux' });
  await settle();
  const dashboard = h.windows[1];
  dashboard.url = 'http://127.0.0.1:8420/voice/';
  h.emit('voice-open-companion', dashboard);
  await settle();
  assert.equal(h.windows[2].options.skipTaskbar, true);
  h.emit('win-close', dashboard);
  await settle();
  assert.equal(h.windows.find(w => w.options.title === 'Imprint launcher').options.skipTaskbar, true);
});

test('the tray shows the running job: tooltip, first menu line and, on macOS, the percentage', async () => {
  let record = { pid: 42, command: 'retag', phase: 'llm_tagging', processed: 340, total: 1200, unit: 'memories' };
  const h = harness({ activity: () => record });
  await settle();
  h.tick();
  const line = 'retag: llm tagging (340 of 1,200 memories, 28%)';
  assert.equal(h.calls.tooltip, `Imprint · ${line}`);
  assert.equal(h.calls.menu[0].label, line);
  assert.equal(h.calls.menu[0].enabled, false);
  assert.equal(h.calls.title, ' 28%');
  record = null;
  h.tick();
  assert.equal(h.calls.tooltip, 'Imprint');
  assert.equal(h.calls.menu[0].label, 'Open Dashboard');
  assert.equal(h.calls.title, '');
});

test('without a system tray, the floating launcher keeps a hidden dashboard accessible', async () => {
  const h = harness({ trayAvailable: false });
  await settle();
  h.windows[1].close();
  await settle();
  assert.equal(h.windows[1].visible, false);
  const launcher = h.windows[2];
  assert.equal(launcher.visible, true);
  h.emit('launcher-open', launcher);
  assert.equal(h.windows[1].visible, true);
  assert.equal(launcher.visible, false);
  assert.equal(h.services[0].stopped, undefined);
});

test('macOS tray icon is two-thirds size; recovery shortcut restores lost Echo and drag IPC is scoped', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  assert.equal(h.calls.traySize.width, 21);
  const dashboard = h.windows[1];
  await h.invoke('voice-set-companion', dashboard, true);
  const echo = h.windows[2];
  const before = echo.getBounds();
  h.emit('voice-drag-start', dashboard, { x: 10, y: 10 });
  h.emit('voice-drag-move', dashboard, { x: 100, y: 100 });
  assert.deepEqual(echo.getBounds(), before);
  h.emit('voice-drag-start', echo, { x: 10, y: 10 });
  h.emit('voice-drag-move', echo, { x: 130, y: 80 });
  assert.equal(echo.getBounds().x, before.x + 120);
  echo.setBounds({ ...before, x: -20000, y: 20000 });
  h.shortcuts.values().next().value(); await settle();
  assert.ok(echo.getBounds().x >= 0 && echo.getBounds().y >= 0);
  assert.equal(echo.visible, true);
  h.app.quit(); await settle(); assert.equal(h.shortcuts.size, 0);
});

test('Windows grants the microphone to both voice windows; request and check handlers agree', async () => {
  // A refusal by the request handler reaches the page as NotAllowedError; a check
  // that disagrees would hide devices instead. Both must answer alike.
  const h = harness({ alreadyUp: true, platform: 'win32' }); await settle();
  const origin = 'http://127.0.0.1:8420';
  const dashboard = h.windows[1];
  dashboard.url = `${origin}/voice/`;
  await h.invoke('voice-set-companion', dashboard, true);
  const echo = h.windows[2];
  const request = (window, details) => new Promise(resolve => h.calls.request(window.webContents, 'media', resolve,
    { securityOrigin: `${origin}/`, requestingUrl: window.url, isMainFrame: true, ...details }));
  const check = (window, details, requestingOrigin = window.url) => h.calls.check(window.webContents, 'media', requestingOrigin,
    { securityOrigin: `${origin}/`, requestingUrl: window.url, isMainFrame: true, embeddingOrigin: '', ...details });
  for (const window of [dashboard, echo]) {
    assert.equal(await request(window, { mediaTypes: ['audio'] }), true, window.url);
    assert.equal(check(window, { mediaType: 'audio' }), true, window.url);
    assert.equal(check(window, { mediaType: 'audio' }, origin), true, window.url);
    assert.equal(await request(window, { mediaTypes: ['audio', 'video'] }), false);
    assert.equal(check(window, { mediaType: 'video' }), false);
  }
  dashboard.url = `${origin}/chat/`;
  assert.equal(await request(dashboard, { mediaTypes: ['audio'] }), false);
  assert.equal(check(dashboard, { mediaType: 'audio' }), false);
});

test('cancel during startup prevents service spawn or dashboard opening', async () => {
  const h = harness();
  await Promise.resolve();
  h.emit('win-close', h.windows[0]);
  await settle();
  assert.equal(h.services.length, 0);
  assert.equal(h.calls.includes('dashboard'), false);
  assert.ok(h.calls.includes('quit'));
});

test('service failure shows recovery screen and retry waits for old service', async () => {
  const h = harness();
  await settle();
  h.services[0].onFailure('Service exited');
  assert.equal(h.windows[1].destroyed, true);
  const error = h.windows[2];
  h.app.emit('second-instance');
  assert.equal(error.visible, true);
  h.emit('startup-retry', error);
  await settle();
  assert.equal(h.services[0].stopped, true);
  assert.equal(h.services.length, 2);
  assert.equal(h.windows[3].visible, true);
});

test('missing interpreter produces an error screen without opening dashboard', async () => {
  const h = harness({ failSpawn: true });
  await settle();
  assert.equal(h.calls.includes('dashboard'), false);
  assert.equal(h.windows[0].visible, true);
  h.emit('app-quit', h.windows[0]);
  await settle();
  assert.equal(h.services[0].stopped, true);
});

test('Echo has a separate transparent sandboxed window, scoped IPC, pin and destroy lifecycle', async () => {
  const h = harness({ alreadyUp: true });
  await settle();
  const dashboard = h.windows[1];
  h.emit('voice-open-companion', dashboard); // Dashboard route has no voice controls.
  assert.equal(h.windows.length, 2);
  dashboard.url = 'http://127.0.0.1:8420/voice/';
  h.emit('voice-open-companion', dashboard);
  await settle();
  const echo = h.windows[2];
  assert.equal(echo.url, 'http://127.0.0.1:8420/companion/');
  assert.equal(echo.options.transparent, true);
  assert.equal(echo.options.alwaysOnTop, false);
  assert.equal(echo.options.acceptFirstMouse, true); // macOS: the first click on Echo reaches its controls.
  assert.equal(echo.options.webPreferences.sandbox, true);
  assert.equal(echo.options.webPreferences.nodeIntegration, false);
  h.emit('voice-pin-companion', dashboard, true);
  assert.equal(echo.pinned, undefined);
  h.emit('voice-pin-companion', echo, true);
  assert.equal(echo.pinned, true);
  h.emit('voice-close-companion', echo);
  assert.equal(echo.destroyed, true);
  h.calls.menu.find(item => item.label === 'Float Echo').click();
  await settle();
  h.app.quit();
  await settle();
  assert.equal(h.windows[3].destroyed, true);
  assert.equal(h.services.length, 0);
});

test('minimize leaves a draggable launcher; only its own frame can move or open it', async () => {
  const h = harness(); await settle();
  const dashboard = h.windows[1];
  dashboard.emit('minimize'); await settle();
  const launcher = h.windows[2];
  assert.equal(dashboard.visible, false); assert.equal(launcher.visible, true);
  const before = launcher.getBounds();
  h.emit('launcher-drag-start', dashboard, { x: 0, y: 0 });
  h.emit('launcher-drag-move', dashboard, { x: 200, y: 200 });
  assert.deepEqual(launcher.getBounds(), before);
  h.emit('launcher-drag-start', launcher, { x: 10, y: 10 });
  h.emit('launcher-drag-move', launcher, { x: 140, y: 70 });
  assert.equal(launcher.getBounds().x, before.x + 130);
  h.emit('launcher-drag-end', launcher);
  h.emit('launcher-open', dashboard); assert.equal(dashboard.visible, false);
  h.emit('launcher-open', launcher); assert.equal(dashboard.visible, true); assert.equal(launcher.visible, false);
  assert.equal(h.services[0].stopped, undefined);
});

test('display fitting restores an offscreen avatar and handles a small work area', () => {
  const { fitBounds, fitLauncher, trayImage } = require('./companion');
  const area = { x: -800, y: 20, width: 800, height: 600 };
  const head = fitBounds({ x: 20000, y: -20000, width: 400, height: 640 }, area);
  assert.deepEqual(head, { x: -400, y: 20, width: 400, height: 600 });
  assert.deepEqual(fitBounds({ width: 'corrupt', height: [] }, area), { x: -420, y: 20, width: 400, height: 600 });
  assert.deepEqual(fitLauncher({ x: 9999, y: 9999 }, area), { x: -56, y: 564, width: 56, height: 56 });
  const image = { isEmpty: () => false };
  assert.equal(trayImage(image, 'win32'), image); assert.equal(trayImage(image, 'linux'), image);
});

test('a watching voice page gets the cursor in its own coordinates until it stops or reloads', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  await h.invoke('voice-set-companion', h.windows[1], true);
  const echo = h.windows[2], sent = [];
  echo.webContents.send = (name, value) => { if (name === 'voice-cursor') sent.push(value); };
  echo.setBounds({ x: 1500, y: 300, width: 400, height: 640 });
  h.emit('voice-cursor-watch', h.windows[1], true);
  const always = h.intervals.size; // the tray's own refresh
  assert.equal(h.intervals.size, always, 'the dashboard root is not a voice page');
  h.emit('voice-cursor-watch', echo, true); h.emit('voice-cursor-watch', echo, true);
  h.tick(); h.tick();
  assert.deepEqual(sent, [{ x: -700, y: 100 }]);
  echo.webContents.getZoomFactor = () => 2; echo.setPosition(0, 0);
  h.tick(); assert.deepEqual(sent.at(-1), { x: 400, y: 200 });
  h.emit('voice-cursor-watch', echo, false); assert.equal(h.intervals.size, always + 1);
  h.emit('voice-cursor-watch', echo, false); assert.equal(h.intervals.size, always);
  h.emit('voice-cursor-watch', echo, true); echo.webContents.emit('did-navigate');
  assert.equal(h.intervals.size, always);
});

test('normal open restores the saved monitor; recall moves Echo to the cursor display', async () => {
  const h = harness({ alreadyUp:true, savedPosition: {x:2200,y:50,width:400,height:640} }); await settle();
  await h.invoke('voice-set-companion', h.windows[1], true);
  const echo=h.windows[2]; assert.equal(echo.getBounds().x,2200);
  h.shortcuts.values().next().value(); await settle();
  assert.ok(echo.getBounds().x>=0 && echo.getBounds().x<1920);
});

test('hidden dashboard docks the companion to the launcher and either drag moves both', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  const dashboard = h.windows[1];
  await h.invoke('voice-set-companion', dashboard, true);
  const head = h.windows[2];
  h.emit('win-close', dashboard); await settle();
  const launcher = h.windows[3];
  assert.equal(head.isAlwaysOnTop(), true);
  const delta = () => ({ x: head.getBounds().x - launcher.getBounds().x, y: head.getBounds().y - launcher.getBounds().y });
  const relative = delta();
  const initial = head.getBounds();
  h.emit('launcher-drag-start', launcher, { x: 0, y: 0 });
  h.emit('launcher-drag-move', launcher, { x: 60, y: -20 });
  assert.equal(head.getBounds().x, initial.x + 60); assert.equal(head.getBounds().y, initial.y - 20);
  assert.deepEqual(delta(), relative);
  h.emit('launcher-drag-end', launcher);
  assert.deepEqual(delta(), relative);
  const before = launcher.getBounds();
  h.emit('voice-drag-start', head, { x: 100, y: 100 });
  h.emit('voice-drag-move', head, { x: 120, y: 130 });
  assert.equal(launcher.getBounds().x, before.x + 20); assert.equal(launcher.getBounds().y, before.y + 30);
  h.emit('voice-drag-end', head);
  assert.deepEqual(delta(), relative);
  // Pinning while grouped records the choice; the group keeps the head on top.
  h.emit('voice-pin-companion', head, false);
  assert.equal(head.isAlwaysOnTop(), true);
  h.emit('launcher-open', launcher);
  assert.equal(launcher.visible, false); assert.equal(head.isAlwaysOnTop(), false);
  const undocked = launcher.getBounds();
  h.emit('voice-drag-start', head, { x: 0, y: 0 }); h.emit('voice-drag-move', head, { x: 10, y: 10 });
  assert.deepEqual(launcher.getBounds(), undocked);
});

test('the pin state the companion shows is the user choice, not the grouped on-top state', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  const dashboard = h.windows[1];
  await h.invoke('voice-set-companion', dashboard, true);
  const head = h.windows[2];
  h.emit('voice-pin-companion', head, true);
  assert.equal((await h.invoke('voice-companion-state', head)).pinned, true);
  h.emit('win-close', dashboard); await settle();
  h.emit('voice-pin-companion', head, false);
  assert.equal((await h.invoke('voice-companion-state', head)).pinned, false);
  assert.equal(head.isAlwaysOnTop(), true);
  h.emit('launcher-open', h.windows[3]);
  assert.equal(head.isAlwaysOnTop(), false);
});

test('group layout stays in one work area and stacks on narrow displays', () => {
  const { dockCompanion } = require('./companion');
  for (const area of [{ x: -1000, y: 40, width: 1000, height: 700 }, { x: 0, y: 0, width: 320, height: 480 }]) {
    const group = dockCompanion({ x: 9000, y: 9000, width: 400, height: 640 }, { x: -9000, y: 9000 }, area);
    for (const bounds of Object.values(group)) {
      assert.ok(bounds.x >= area.x && bounds.y >= area.y);
      assert.ok(bounds.x + bounds.width <= area.x + area.width);
      assert.ok(bounds.y + bounds.height <= area.y + area.height);
    }
  }
});

test('group drag completion uses intended positions when native bounds notifications lag', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  await h.invoke('voice-set-companion', h.windows[1], true);
  const head = h.windows[2]; h.emit('win-close', h.windows[1]); await settle();
  const launcher = h.windows[3], initialHead = head.getBounds(), initialIcon = launcher.getBounds();
  h.emit('voice-drag-start', head, { x: 0, y: 0 });
  h.emit('voice-drag-move', head, { x: 40, y: -20 });
  // The launcher still reports its old native position at pointer-up.
  launcher.bounds = initialIcon;
  h.emit('voice-drag-end', head);
  assert.equal(head.getBounds().x - launcher.getBounds().x, initialHead.x - initialIcon.x);
  assert.equal(head.getBounds().y - launcher.getBounds().y, initialHead.y - initialIcon.y);
  assert.equal(head.getBounds().x, initialHead.x + 40);
});

test('microphone consent and settings IPC refuse other pages and subframes and recheck the sender after consent', async () => {
  let answer;
  const h = harness({ mediaStatus: 'not-determined', consent: () => new Promise(resolve => { answer = resolve; }) });
  await settle();
  const dashboard = h.windows[1];
  assert.equal((await h.invoke('voice-microphone-access', dashboard)).status, 'denied');
  assert.equal(await h.invoke('voice-microphone-settings', dashboard), false);
  assert.equal(h.calls.includes('media-status'), false);
  dashboard.url = 'http://127.0.0.1:8420/voice/';
  dashboard.webContents.mainFrame = {};
  assert.equal((await h.handlers['voice-microphone-access']({ sender: dashboard.webContents, senderFrame: {} })).status, 'denied');
  const pending = h.invoke('voice-microphone-access', dashboard);
  assert.equal(h.calls.filter(call => call === 'media-consent').length, 1);
  dashboard.url = 'http://127.0.0.1:8420/chat/'; // navigated away while the OS dialog was open
  answer(true);
  assert.equal((await pending).status, 'denied');
  dashboard.url = 'http://127.0.0.1:8420/voice/';
  assert.equal(await h.invoke('voice-microphone-settings', dashboard), true);
  assert.deepEqual(h.calls.find(call => call[0] === 'open-external'),
    ['open-external', 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone']);
});

test('Windows reports its desktop privacy setting; Linux has no OS consent and no settings link', async () => {
  const windows = harness({ alreadyUp: true, platform: 'win32', mediaStatus: 'denied' }); await settle();
  windows.windows[1].url = 'http://127.0.0.1:8420/voice/';
  assert.deepEqual(await windows.invoke('voice-microphone-access', windows.windows[1]), { status: 'denied' });
  assert.equal(windows.calls.includes('media-consent'), false);
  assert.equal(await windows.invoke('voice-microphone-settings', windows.windows[1]), true);
  const linux = harness({ alreadyUp: true, platform: 'linux' }); await settle();
  linux.windows[1].url = 'http://127.0.0.1:8420/voice/';
  assert.deepEqual(await linux.invoke('voice-microphone-access', linux.windows[1]), { status: 'unmanaged' });
  assert.equal(await linux.invoke('voice-microphone-settings', linux.windows[1]), false);
});

test('the session permission request asks macOS consent and refuses a page that navigated away meanwhile', async () => {
  let answer;
  const h = harness({ mediaStatus: 'not-determined', consent: () => new Promise(resolve => { answer = resolve; }) });
  await settle();
  const dashboard = h.windows[1];
  dashboard.url = 'http://127.0.0.1:8420/voice/';
  const details = { isMainFrame: true, mediaTypes: ['audio'], requestingUrl: dashboard.url };
  const result = new Promise(done => h.calls.request(dashboard.webContents, 'media', done, details));
  dashboard.url = 'http://127.0.0.1:8420/chat/';
  answer(true);
  assert.equal(await result, false);
  assert.equal(h.calls.check(dashboard.webContents, 'media', 'https://foreign.test', { mediaType: 'audio' }), false);
});
