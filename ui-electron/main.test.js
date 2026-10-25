'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the actual main process with Electron windows and processes replaced
// by observable doubles; these do not establish native graphical behavior.
function harness({ alreadyUp = false, failSpawn = false, trayAvailable = true, dashboardPending = false, packaged = true, savedPosition = null } = {}) {
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
    setToolTip() {}
    setContextMenu(menu) { calls.menu = menu; }
  }
  const electron = {
    app, BrowserWindow: Window, Tray, Menu: { buildFromTemplate: menu => menu },
    nativeImage: { createFromPath: () => ({ isEmpty: () => false, getSize: () => ({ width: 32, height: 32 }), resize: size => { calls.traySize = size; return { isEmpty: () => false }; } }) },
    shell: { openExternal() {}, openPath() {} }, ipcMain,
    session: { defaultSession: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} } },
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
      queueMicrotask(() => callback({ statusCode: alreadyUp || services.some(service => !service.stopped) ? 200 : 503, resume() {} }));
      return req;
    },
  };
  const intervals = new Set();
  const modules = { electron, 'node:path': path, 'node:http': http,
    'node:fs': { readFileSync: () => { if (savedPosition) return JSON.stringify(savedPosition); throw Error('No saved position'); }, mkdirSync() {}, writeFileSync() {}, renameSync() {} },
    './companion': require('./companion'),
    './navigation': require('./navigation'), './service': { DesktopService: Service },
    './voice-permissions': require('./voice-permissions') };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8'), {
    require: name => { assert.ok(name in modules, name); return modules[name]; },
    process: { argv: ['electron', ...packaged ? [] : ['main.js'], '--project-dir=/source', '--data-dir=/data', '--python=/writable/venv/python'], platform: 'darwin' },
    __dirname, console: { warn() {}, error() {} }, setTimeout, Promise, URL, Date,
    setInterval: tick => { intervals.add(tick); return tick; }, clearInterval: tick => intervals.delete(tick),
  });
  return { app, windows, services, calls, ipcMain, shortcuts, intervals, tick: () => intervals.forEach(tick => tick()),
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
  assert.equal(h.intervals.size, 0, 'the dashboard root is not a voice page');
  h.emit('voice-cursor-watch', echo, true); h.emit('voice-cursor-watch', echo, true);
  h.tick(); h.tick();
  assert.deepEqual(sent, [{ x: -700, y: 100 }]);
  echo.webContents.getZoomFactor = () => 2; echo.setPosition(0, 0);
  h.tick(); assert.deepEqual(sent.at(-1), { x: 400, y: 200 });
  h.emit('voice-cursor-watch', echo, false); assert.equal(h.intervals.size, 1);
  h.emit('voice-cursor-watch', echo, false); assert.equal(h.intervals.size, 0);
  h.emit('voice-cursor-watch', echo, true); echo.webContents.emit('did-navigate');
  assert.equal(h.intervals.size, 0);
});

test('normal open restores the saved monitor; recall moves Echo to the cursor display', async () => {
  const h = harness({ alreadyUp:true, savedPosition: {x:2200,y:50,width:400,height:640} }); await settle();
  await h.invoke('voice-set-companion', h.windows[1], true);
  const echo=h.windows[2]; assert.equal(echo.getBounds().x,2200);
  h.shortcuts.values().next().value(); await settle();
  assert.ok(echo.getBounds().x>=0 && echo.getBounds().x<1920);
});
