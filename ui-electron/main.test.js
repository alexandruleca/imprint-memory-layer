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
  mediaStatus = 'granted', consent = async () => true, activity = null, servicePending = false, messageBox = async () => ({ response: 1 }),
  wakeFile = null, onBattery = false, themeFile = null, floatingOrb = true } = {}) {
  // The orb tests run with the floating orb turned on; Imprint's default is off (ADR 0174), tested on its own.
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
      this.sent = [];
      this.webContents.send = (...args) => { this.sent.push(args); };
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.getURL = () => this.url || '';
      this.webContents.getZoomFactor = () => 1;
      this.webContents.setBackgroundThrottling = allowed => { this.throttled = allowed; };
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
    setBackgroundColor(color) { this.background = color; }
    show() { this.visible = true; }
    hide() { this.visible = false; }
    focus() { this.focused = true; }
    isFocused() { return !!this.focused && this.visible; }
    moveTop() {}
    showInactive() { this.visible = true; this.inactive = (this.inactive || 0) + 1; }
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
  class Notification extends EventEmitter {
    static isSupported() { return true; }
    constructor(options) { super(); this.options = options; }
    show() { calls.push(['notification', this.options.title, this.options.body]); calls.notification = this; }
  }
  const electron = {
    app, BrowserWindow: Window, Tray, Notification, Menu: { buildFromTemplate: menu => menu },
    nativeImage: { createFromPath: () => ({ isEmpty: () => false, getSize: () => ({ width: 32, height: 32 }), resize: size => { calls.traySize = size; return { isEmpty: () => false }; } }) },
    shell: { openExternal(url) { calls.push(['open-external', url]); }, openPath() {} }, ipcMain,
    systemPreferences: { getMediaAccessStatus() { calls.push('media-status'); return mediaStatus; },
      askForMediaAccess() { calls.push('media-consent'); return consent(); } },
    session: { defaultSession: { setPermissionRequestHandler(handler) { calls.request = handler; },
      setPermissionCheckHandler(handler) { calls.check = handler; },
      setDisplayMediaRequestHandler(handler) { calls.display = handler; } } },
    desktopCapturer: { getSources: async () => [{ id: 'screen:0', name: 'Entire screen' }] },
    dialog: { showMessageBox: options => { calls.push(['dialog', options.message]); return messageBox(options); } },
    powerMonitor: Object.assign(new EventEmitter(), { isOnBatteryPower: () => onBattery }),
    nativeTheme: Object.defineProperty(Object.assign(new EventEmitter(), { themeSource: 'system', systemDark: false }), 'shouldUseDarkColors',
      { get() { return this.themeSource === 'dark' || (this.themeSource === 'system' && this.systemDark); } }),
  };
  const shortcuts = new Map();
  electron.globalShortcut = { register: (key, action) => { shortcuts.set(key, action); return true; }, unregisterAll: () => shortcuts.clear() };
  electron.screen = Object.assign(new EventEmitter(), { getCursorScreenPoint: () => ({ x: 800, y: 400 }),
    getDisplayNearestPoint: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1080 } }),
    getDisplayMatching: bounds => ({ workArea: { x: bounds.x >= 1920 ? 1920 : 0, y: 0, width: 1920, height: 1080 } }) });
  const http = {
    get(url, _options, callback) {
      if (String(url).endsWith('/api/config/desktop.floating_orb')) {
        calls.push('orb-setting');
        const req = new EventEmitter();
        req.destroy = () => {};
        queueMicrotask(() => {
          const res = Object.assign(new EventEmitter(), { statusCode: 200, setEncoding() {}, resume() {} });
          callback(res);
          res.emit('data', JSON.stringify({ key: 'desktop.floating_orb', value: floatingOrb, source: 'config' }));
          res.emit('end');
        });
        return req;
      }
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
    'node:fs': { readFileSync: file => {
      if (file.endsWith('wake.json')) { if (wakeFile) return wakeFile; throw Error('No choice'); }
      if (file.endsWith('theme.json')) { if (themeFile) return themeFile; throw Error('No theme'); }
      if (savedPosition) return JSON.stringify(savedPosition); throw Error('No saved position');
    }, mkdirSync() {}, writeFileSync(file, data) { calls.push(['write', path.basename(file), data]); }, renameSync() {} },
    './companion': require('./companion'),
    './navigation': require('./navigation'), './service': { DesktopService: Service },
    './activity': { readActivity: dir => { calls.push(['activity', dir]); return typeof activity === 'function' ? activity() : activity; },
      describeActivity: require('./activity').describeActivity },
    './voice-permissions': require('./voice-permissions'), './recording-relay': require('./recording-relay'), './wake': require('./wake') };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8'), {
    require: name => { assert.ok(name in modules, name); return modules[name]; },
    process: { argv: ['electron', ...packaged ? [] : ['main.js'], ...runtimeArgs], platform, env: {},
      execPath: '/opt/imprint/bin/imprint-ui.app/Contents/MacOS/Imprint' },
    __dirname, console: { warn() {}, error() {} }, setTimeout, clearTimeout, Promise, URL, Date,
    setInterval: tick => { intervals.add(tick); return tick; }, clearInterval: tick => intervals.delete(tick),
  });
  return { app, windows, services, calls, ipcMain, shortcuts, intervals, handlers, power: electron.powerMonitor, theme: electron.nativeTheme, tick: () => intervals.forEach(tick => tick()),
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

test('a meeting recording gets the computer audio: display-capture is granted and answered on Recordings only', async () => {
  // Electron 45 asks the permission handler for getDisplayMedia as "display-capture"
  // before the display-media handler; refusing it there failed every recording with
  // the computer's audio as NotAllowedError. macOS asks no microphone consent for it.
  for (const platform of ['win32', 'darwin', 'linux']) {
    const h = harness({ alreadyUp: true, platform, mediaStatus: 'denied' }); await settle();
    const origin = 'http://127.0.0.1:8420';
    const dashboard = h.windows[1];
    dashboard.url = `${origin}/recordings/`;
    const mainFrame = { get url() { return dashboard.url; } };
    dashboard.webContents.mainFrame = mainFrame;
    const details = () => ({ securityOrigin: `${origin}/`, requestingUrl: dashboard.url, isMainFrame: true, mediaTypes: ['audio', 'video'] });
    const request = window => new Promise(resolve => h.calls.request(window.webContents, 'display-capture', resolve, details()));
    const answer = request => { let streams = 'unanswered'; h.calls.display(request, value => { streams = value; }); return streams; };
    assert.equal(await request(dashboard), true, platform);
    assert.equal(h.calls.check(dashboard.webContents, 'display-capture', origin, { requestingUrl: dashboard.url, isMainFrame: true }), true);
    assert.deepEqual(answer({ frame: mainFrame, audioRequested: true, videoRequested: true }), { video: mainFrame, audio: 'loopback' });
    assert.equal(answer({ frame: mainFrame, audioRequested: false, videoRequested: true }), null);
    assert.equal(answer({ frame: { url: dashboard.url }, audioRequested: true, videoRequested: true }), null);
    let thrown = false;
    h.calls.display({ frame: mainFrame, audioRequested: true }, () => { thrown = true; throw new TypeError('frame destroyed'); });
    assert.equal(thrown, true);
    dashboard.url = `${origin}/voice/`;
    assert.equal(await request(dashboard), false);
    assert.equal(answer({ frame: mainFrame, audioRequested: true, videoRequested: true }), null);
  }
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
  assert.deepEqual(fitLauncher({ x: 9999, y: 9999 }, area), { x: -72, y: 548, width: 72, height: 72 });
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
    for (const bounds of [group.launcher, group.companion]) {
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

const ORIGIN = 'http://127.0.0.1:8420';
const inside = (box, area) => box.x >= area.x && box.y >= area.y && box.x + box.width <= area.x + area.width && box.y + box.height <= area.y + area.height;

test('the orb fans its actions toward free space and keeps them on screen at every edge and corner', () => {
  const { fitLauncher, orbSide, haloLayout } = require('./companion');
  const area = { x: 1920, y: 25, width: 1440, height: 875 };
  for (const fx of [0, 0.4, 1]) for (const fy of [0, 0.5, 1]) {
    const orb = fitLauncher({ x: area.x + fx * area.width, y: area.y + fy * area.height }, area);
    const side = orbSide(orb, area);
    assert.equal(side.side, fx < 1 ? 1 : -1, 'toward the wider half');
    assert.equal(side.tilt, fy === 0 ? 'down' : fy === 1 ? 'up' : 'level');
    const ring = haloLayout(orb, area, side);
    assert.ok(inside(ring.bounds, area), JSON.stringify(ring.bounds));
    assert.deepEqual({ x: ring.bounds.x + ring.orb.x, y: ring.bounds.y + ring.orb.y }, { x: orb.x + 36, y: orb.y + 36 });
    assert.equal(ring.satellites.length, 3);
    for (const satellite of ring.satellites) {
      assert.ok(Math.sign(satellite.x - ring.orb.x) !== -side.side, 'never behind the orb');
      assert.ok(inside({ x: ring.bounds.x + satellite.x - 20, y: ring.bounds.y + satellite.y - 20, width: 40, height: 40 }, area));
      assert.ok(inside({ ...satellite.slot, x: satellite.slot.x + ring.bounds.x, y: satellite.slot.y + ring.bounds.y }, area));
    }
    const panel = haloLayout(orb, area, side, 'record', { width: 344, height: 402 });
    assert.ok(inside(panel.bounds, area));
    assert.ok(side.side > 0 ? panel.bounds.x >= orb.x + 60 : panel.bounds.x + panel.bounds.width <= orb.x + 12, 'beside the orb');
  }
});

test('Echo docks beside the orb on its side without moving the orb', () => {
  const { fitLauncher, dockCompanion } = require('./companion');
  const area = { x: 0, y: 0, width: 1920, height: 1080 };
  const orb = fitLauncher({ x: 1000, y: 1000 }, area);
  const right = dockCompanion({ width: 400, height: 640 }, orb, area, 1);
  assert.deepEqual(right.launcher, orb);
  assert.equal(right.side, 1); assert.equal(right.companion.x, orb.x + 72 + 2);
  assert.equal(right.companion.y + right.companion.height, 1080, 'slides up along the orb to stay on screen');
  const left = dockCompanion({ width: 400, height: 640 }, orb, area, -1);
  assert.equal(left.side, -1); assert.equal(left.companion.x + left.companion.width, orb.x - 2);
  const edge = dockCompanion({ width: 400, height: 640 }, fitLauncher({ x: 1900, y: 400 }, area), area, 1);
  assert.equal(edge.side, -1, 'the other side when the preferred one has no room');
});

test('the orb opens its ring in a window of its own; clicking elsewhere or on the orb closes it', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  const dashboard = h.windows[1];
  dashboard.emit('minimize'); await settle();
  const orb = h.windows[2], orbBounds = orb.getBounds();
  h.emit('launcher-ring', dashboard); await settle();
  assert.equal(h.windows.length, 3, 'only the orb opens its ring');
  h.emit('launcher-ring', orb, 'first');
  const ring = h.windows[3], layouts = [];
  ring.webContents.send = (name, value) => { if (name === 'halo-layout') layouts.push(value); };
  await settle();
  assert.equal(ring.url, `${ORIGIN}/launcher/halo/`);
  assert.equal(ring.options.transparent, true); assert.equal(ring.options.webPreferences.backgroundThrottling, false);
  const layout = layouts.at(-1);
  assert.equal(layout.kind, 'ring'); assert.equal(layout.focus, 'first'); assert.equal(layout.satellites.length, 3);
  assert.deepEqual(ring.getBounds(), layout.bounds);
  assert.equal(ring.visible, false, 'shown only once the page has drawn it');
  h.emit('halo-ready', orb, layout.seq); assert.equal(ring.visible, false);
  h.emit('halo-ready', ring, layout.seq); assert.equal(ring.visible, true);
  assert.deepEqual(orb.getBounds(), orbBounds, 'the orb never moves or resizes');
  assert.equal((await h.invoke('launcher-state', orb)).ring, true);
  assert.equal(await h.invoke('halo-layout', orb), null);
  // A click elsewhere closes it.
  ring.emit('blur');
  assert.equal(layouts.at(-1), null);
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(ring.visible, false);
  // A press on the orb closes it too, and that press's click does not reopen it.
  h.emit('launcher-ring', orb); await settle();
  h.emit('halo-ready', ring, layouts.at(-1).seq);
  orb.setBounds({ x: 770, y: 370, width: 72, height: 72 }); // under the pointer
  ring.emit('blur');
  h.emit('launcher-ring', orb); await settle();
  assert.equal((await h.invoke('launcher-state', orb)).ring, false);
  h.emit('launcher-ring', orb); await settle();
  assert.equal((await h.invoke('launcher-state', orb)).ring, true);
  // Its actions come only from the ring's own page.
  h.emit('halo-action', orb, 'dashboard'); assert.equal(dashboard.visible, false);
  h.emit('halo-action', ring, 'dashboard');
  assert.equal(dashboard.visible, true); assert.equal(orb.visible, false);
  assert.equal(h.windows.length, 4, 'the ring window is reused');
});

test('the orb starts a recording in the hidden dashboard only with consent, and the orb and its ring follow it', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  const dashboard = h.windows[1], commands = [];
  dashboard.webContents.send = (name, value) => { if (name === 'recorder-command') commands.push(value); };
  dashboard.emit('minimize'); await settle();
  const orb = h.windows[2];
  h.emit('launcher-ring', orb); await settle();
  const ring = h.windows[3];
  h.emit('halo-action', ring, 'record'); await settle();
  assert.equal((await h.invoke('halo-layout', ring)).kind, 'record');
  assert.equal(ring.getBounds().width, 344);
  h.emit('launcher-record', ring, { title: 'Weekly', project: 'meetings', others: true });
  h.emit('launcher-record', dashboard, { title: 'Weekly', project: 'meetings', others: true, consent: true });
  h.emit('launcher-record', ring, { title: 'Weekly', project: '', others: true, consent: true });
  assert.equal(commands.length, 0, 'no consent, not the orb, or no project');
  h.emit('launcher-record', ring, { title: ' Weekly ', project: 'meetings', others: true, consent: true });
  assert.equal(JSON.stringify(commands), JSON.stringify([{ type: 'start', title: 'Weekly', project: 'meetings', others: true }]));
  assert.equal(dashboard.throttled, false, 'the hidden dashboard answers at once');
  const heard = [];
  for (const [name, window] of [['orb', orb], ['ring', ring]]) {
    window.webContents.send = (message, value) => { if (message === 'recorder-state') heard.push([name, value.phase]); };
  }
  const report = phase => ({ phase, id: 'r1', title: 'Weekly', project: 'meetings', startedAt: 1000, others: true, error: '' });
  h.emit('recorder-state', orb, report('recording'));
  assert.deepEqual(heard, [], 'only the dashboard reports');
  h.emit('recorder-state', dashboard, report('recording'));
  assert.deepEqual(heard, [['orb', 'recording'], ['ring', 'recording']]);
  assert.equal(await h.invoke('halo-layout', ring), null, 'the panel folds into the orb once recording');
  assert.equal((await h.invoke('launcher-recorder', orb)).phase, 'recording');
  assert.equal(await h.invoke('launcher-recorder', dashboard), null);
  h.emit('launcher-record', ring, { title: 'Again', project: 'meetings', others: false, consent: true });
  assert.equal(commands.length, 1, 'one recording at a time');
  h.emit('launcher-ring', orb); await settle();
  h.emit('halo-action', ring, 'stop');
  assert.equal(commands.at(-1).type, 'stop');
  h.emit('recorder-state', dashboard, report('idle'));
  assert.equal(dashboard.throttled, true);
});

test('with the floating orb off, the default, minimize stays in the taskbar and close goes to the tray', async () => {
  const h = harness({ alreadyUp: true, floatingOrb: false }); await settle();
  const dashboard = h.windows[1];
  dashboard.emit('minimize'); await settle();
  assert.ok(h.calls.includes('orb-setting'), 'the choice is read when the dashboard is hidden');
  assert.equal(h.windows.length, 2, 'no orb window');
  assert.equal(dashboard.visible, true, 'left minimized, as the system put it, not hidden');
  h.emit('win-close', dashboard);
  assert.equal(dashboard.visible, false, 'closing hides it at once');
  await settle();
  assert.equal(h.windows.length, 2, 'still no orb');
  assert.ok(h.calls.includes('dock-hide'), 'the Dock icon goes with it, as with the orb');
  h.app.emit('second-instance');
  assert.equal(dashboard.visible, true);
});

test('with the floating orb off, a recording brings it up and it goes when the recording ends', async () => {
  const h = harness({ alreadyUp: true, floatingOrb: false }); await settle();
  const dashboard = h.windows[1];
  dashboard.webContents.send = () => {};
  const report = phase => ({ phase, id: 'r1', title: 'Weekly', project: 'meetings', startedAt: 1000, others: true, error: '' });
  h.emit('recorder-state', dashboard, report('recording'));
  dashboard.emit('minimize'); await settle();
  const orb = h.windows[2];
  assert.equal(orb?.visible, true, 'the orb shows the hidden recording');
  assert.equal(dashboard.visible, false);
  h.emit('recorder-state', dashboard, report('idle'));
  assert.equal(orb.visible, false, 'and goes with it');
});

test('Echo folds into the orb and comes back from it in the same window', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  const dashboard = h.windows[1];
  await h.invoke('voice-set-companion', dashboard, true);
  const echo = h.windows[2];
  h.emit('win-close', dashboard); await settle();
  const orb = h.windows[3], orbBounds = orb.getBounds();
  assert.equal((await h.invoke('voice-companion-state', echo)).docked, true);
  assert.equal((await h.invoke('launcher-state', orb)).echoSide, 1);
  h.emit('voice-collapse-companion', dashboard); assert.equal(echo.visible, true);
  h.emit('voice-collapse-companion', echo);
  assert.equal(echo.visible, false); assert.equal(echo.destroyed, false);
  assert.equal((await h.invoke('launcher-state', orb)).echo, false);
  assert.equal((await h.invoke('voice-companion-state', dashboard)).open, false);
  h.emit('launcher-ring', orb); await settle();
  const ring = h.windows[4];
  h.emit('halo-action', ring, 'echo'); await settle();
  assert.equal(echo.visible, true); assert.equal(h.windows.length, 5, 'no new Echo window');
  assert.deepEqual(orb.getBounds(), orbBounds, 'the orb stays where the user put it');
  assert.equal((await h.invoke('launcher-state', orb)).echo, true);
  h.emit('launcher-ring', orb); await settle();
  h.emit('halo-action', ring, 'echo'); assert.equal(echo.visible, false);
});

test('quitting during a recording asks first, then stops it and quits once it is saved', async () => {
  const answers = [1, 0];
  const h = harness({ alreadyUp: true, messageBox: async () => ({ response: answers.shift() }) }); await settle();
  const dashboard = h.windows[1], commands = [];
  dashboard.webContents.send = (name, value) => { if (name === 'recorder-command') commands.push(value); };
  const report = phase => ({ phase, id: 'r1', title: 'Weekly', project: 'meetings', startedAt: 1000, others: true, error: '' });
  h.emit('recorder-state', dashboard, report('recording'));
  h.app.quit(); await settle();
  assert.deepEqual(h.calls.filter(call => call[0] === 'dialog'), [['dialog', 'Stop the recording and quit?']]);
  assert.equal(h.calls.includes('quit'), false, 'Cancel keeps Imprint running');
  h.app.quit(); await settle();
  assert.equal(JSON.stringify(commands), '[{"type":"stop"}]');
  assert.equal(h.calls.includes('quit'), false, 'waits for the last audio');
  h.emit('recorder-state', dashboard, report('idle'));
  await new Promise(resolve => setTimeout(resolve, 150)); await settle();
  assert.equal(h.calls.includes('quit'), true);
});

// "Hey Echo" (ADR 0137): the hidden listener and everything that shows it.
const WAKE_ON = JSON.stringify({ enabled: true, pausedUntil: 0, battery: 'listen' });
const listener = h => h.windows.find(window => window.url === `${ORIGIN}/wake/` && !window.destroyed);
const wakeWrites = h => h.calls.filter(call => call[0] === 'write' && call[1] === 'wake.json.tmp').map(call => JSON.parse(call[2]));
// Values made in the main process's context have its prototypes.
const plain = value => JSON.parse(JSON.stringify(value));
const meeting = phase => ({ phase, id: 'r1', title: 'Weekly', project: 'meetings', startedAt: 1000, others: true, error: '' });

test('a chosen wake word listens from a hidden, unthrottled window, and the tray, top bar and orb say so', async () => {
  const h = harness({ alreadyUp: true, wakeFile: WAKE_ON }); await settle();
  const dashboard = h.windows[1], ear = listener(h);
  assert.ok(ear, 'the listener window');
  assert.equal(ear.visible, false, 'never shown');
  assert.equal(ear.options.webPreferences.backgroundThrottling, false);
  assert.equal(ear.options.webPreferences.autoplayPolicy, 'no-user-gesture-required');
  assert.equal(ear.options.webPreferences.sandbox, true);
  assert.equal(ear.options.skipTaskbar, true);
  assert.equal(h.invoke('voice-wake-state', dashboard).listening, true);
  assert.equal(dashboard.sent.filter(([name]) => name === 'wake-state').at(-1)[1].listening, true);
  assert.equal(h.calls.tooltip, 'Imprint · Listening for “Hey Echo”');
  assert.deepEqual(plain(h.calls.menu.slice(0, 3).map(item => item.label)), ['Listening for “Hey Echo”', 'Pause listening for 1 hour', 'Turn off “Hey Echo”']);
  // Only the listener window's /wake page gets the microphone, and its consent step.
  const allowed = contents => new Promise(resolve => h.calls.request(contents, 'media', resolve, { mediaType: 'audio' }));
  assert.equal(await allowed(ear.webContents), true);
  assert.equal(h.calls.check(ear.webContents, 'media', ORIGIN, { mediaType: 'audio' }), true);
  assert.equal((await h.invoke('voice-microphone-access', ear)).status, 'granted');
  dashboard.url = `${ORIGIN}/wake/`;
  assert.equal(await allowed(dashboard.webContents), false, 'the dashboard on /wake gets nothing');
  ear.url = `${ORIGIN}/voice/`;
  assert.equal(await allowed(ear.webContents), false, 'the listener elsewhere gets nothing');
  assert.equal((await h.invoke('voice-microphone-access', ear)).status, 'denied');
  ear.url = `${ORIGIN}/wake/`; dashboard.url = `${ORIGIN}/`;
  h.emit('win-close', dashboard); await settle();
  const orb = h.windows.find(window => window.url === `${ORIGIN}/launcher/`);
  assert.equal((await h.invoke('launcher-state', orb)).wake.listening, true);
  h.emit('wake-listener', ear, { type: 'checking', on: true });
  assert.equal((await h.invoke('launcher-state', orb)).wake.checking, true);
});

test('listening stops for recordings, Echo conversations, lock, sleep and a pause, and comes back', async () => {
  const h = harness({ alreadyUp: true, wakeFile: WAKE_ON }); await settle();
  const dashboard = h.windows[1];
  const expect = (listening, what) => {
    assert.equal(!!listener(h), listening, what);
    // The indicators follow the window, always.
    assert.equal(h.invoke('voice-wake-state', dashboard).listening, listening, `${what} (indicator)`);
    assert.equal(/Listening for/.test(h.calls.tooltip), listening, `${what} (tray)`);
  };
  expect(true, 'chosen');
  h.emit('recorder-state', dashboard, meeting('recording')); expect(false, 'recording');
  assert.equal(h.invoke('voice-wake-state', dashboard).reason, 'recording');
  h.emit('recorder-state', dashboard, meeting('idle')); expect(true, 'recording over');
  await h.invoke('voice-set-companion', dashboard, true);
  const echo = h.windows.find(window => window.url === `${ORIGIN}/companion/`);
  h.emit('voice-microphone-state', echo, true); expect(false, 'Echo holds the microphone');
  h.emit('voice-microphone-state', echo, false); expect(true, 'conversation over');
  h.emit('voice-microphone-state', echo, true); echo.webContents.emit('destroyed'); expect(true, 'its page went away');
  dashboard.url = `${ORIGIN}/chat/`;
  h.emit('voice-microphone-state', dashboard, true); expect(true, 'only voice pages report a microphone');
  for (const [off, on] of [['lock-screen', 'unlock-screen'], ['suspend', 'resume']]) {
    h.power.emit(off); expect(false, off);
    h.power.emit(on); expect(true, on);
  }
  h.power.emit('thermal-state-change', {}, 'critical'); expect(false, 'critical heat');
  h.power.emit('thermal-state-change', {}, 'fair'); expect(true, 'cooler');
  h.emit('wake-control', dashboard, 'pause'); expect(false, 'paused for an hour');
  assert.match(h.calls.tooltip, /“Hey Echo” paused until \d\d:\d\d/);
  assert.ok(wakeWrites(h).at(-1).pausedUntil > Date.now() + 59 * 60 * 1000);
  assert.equal(h.calls.menu.find(item => item.label === 'Resume listening') !== undefined, true);
  h.emit('wake-control', dashboard, 'resume'); expect(true, 'resumed');
  h.emit('wake-control', dashboard, 'off'); expect(false, 'off');
  assert.equal(wakeWrites(h).at(-1).enabled, false);
  assert.equal(h.calls.tooltip, 'Imprint');
});

test('on battery it keeps listening unless the user chose to pause there, and tells the listener', async () => {
  const h = harness({ alreadyUp: true, wakeFile: JSON.stringify({ enabled: true, battery: 'pause' }) }); await settle();
  assert.ok(listener(h));
  h.power.emit('on-battery'); assert.equal(listener(h), undefined);
  h.power.emit('on-ac'); assert.ok(listener(h));
  const other = harness({ alreadyUp: true, wakeFile: WAKE_ON, onBattery: true }); await settle();
  const ear = listener(other);
  assert.deepEqual(plain(await other.invoke('wake-listener-config', ear)), { battery: true });
  assert.equal(await other.invoke('wake-listener-config', other.windows[1]), null);
  other.power.emit('on-ac');
  assert.deepEqual(plain(ear.sent.at(-1)), ['wake-listener-config', { battery: false }]);
});

test('"Hey Echo" shows Echo without focus, hands it the question, then puts it back where it was', async () => {
  const h = harness({ alreadyUp: true, wakeFile: WAKE_ON }); await settle();
  const dashboard = h.windows[1];
  await h.invoke('voice-set-companion', dashboard, true);
  const echo = h.windows.find(window => window.url === `${ORIGIN}/companion/`);
  h.emit('win-close', dashboard); await settle();
  h.emit('voice-collapse-companion', echo); assert.equal(echo.visible, false);
  const ear = listener(h), wakes = () => echo.sent.filter(([name]) => name === 'voice-wake').map(([, value]) => value);
  h.emit('wake-listener', h.windows[1], { type: 'heard', id: 6 }); await settle();
  assert.equal(echo.visible, false, 'only the listener reports a wake');
  h.emit('wake-listener', ear, { type: 'heard', id: 7 }); await settle();
  assert.equal(echo.visible, true);
  assert.ok(echo.inactive >= 1, 'shown without taking focus');
  assert.deepEqual(plain(wakes().at(-1)), { type: 'start', id: 7 });
  assert.deepEqual(plain(await h.invoke('voice-wake-session', echo)), { id: 7, text: null });
  // Echo's microphone comes on before the question arrives: the listener stays to hand it over.
  h.emit('voice-microphone-state', echo, true); assert.ok(listener(h));
  h.emit('wake-listener', ear, { type: 'question', id: 3, text: 'not this one' });
  h.emit('wake-listener', ear, { type: 'question', id: 7, text: 'what did we decide yesterday?' });
  assert.deepEqual(plain(wakes().at(-1)), { type: 'question', id: 7, text: 'what did we decide yesterday?' });
  assert.equal(listener(h), undefined, 'Echo hears the user now');
  h.emit('voice-wake-done', dashboard, { id: 7, restore: true }); assert.equal(echo.visible, true, 'only Echo ends its session');
  h.emit('voice-microphone-state', echo, false);
  h.emit('voice-wake-done', echo, { id: 7, restore: true });
  assert.equal(echo.visible, false); assert.equal(echo.destroyed, false, 'folded back, not closed');
  assert.ok(listener(h), 'listening again');
  // Closed before the wake: closed again afterwards.
  h.emit('voice-close-companion', echo); assert.equal(echo.destroyed, true);
  h.emit('wake-listener', listener(h), { type: 'heard', id: 8 }); await settle();
  const second = h.windows.filter(window => window.url === `${ORIGIN}/companion/`).at(-1);
  assert.notEqual(second, echo); assert.equal(second.visible, true); assert.ok(second.inactive >= 1);
  assert.deepEqual(plain(await h.invoke('voice-wake-session', second)), { id: 8, text: null });
  h.emit('voice-wake-done', second, { id: 8, restore: true });
  assert.equal(second.destroyed, true);
  // Taken over by the user: Echo stays.
  h.emit('wake-listener', listener(h), { type: 'heard', id: 9 }); await settle();
  const third = h.windows.filter(window => window.url === `${ORIGIN}/companion/`).at(-1);
  h.emit('voice-wake-done', third, { id: 9, restore: false });
  assert.equal(third.visible, true); assert.equal(third.destroyed, false);
});

test('a refused microphone waits for the user; only a voice page turns listening on', async () => {
  const h = harness({ alreadyUp: true }); await settle();
  const dashboard = h.windows[1];
  assert.equal(listener(h), undefined, 'off by default');
  assert.equal(h.calls.tooltip, 'Imprint');
  dashboard.url = `${ORIGIN}/chat/`;
  assert.equal(await h.invoke('voice-wake-set', dashboard, { enabled: true }), null);
  h.emit('wake-control', dashboard, 'resume'); assert.equal(listener(h), undefined, 'controls cannot turn it on');
  dashboard.url = `${ORIGIN}/voice/`;
  assert.equal(await h.invoke('voice-wake-set', dashboard, { enabled: true, battery: 'pause' }).listening, true);
  assert.deepEqual(wakeWrites(h).at(-1), { enabled: true, pausedUntil: 0, battery: 'pause' });
  h.emit('wake-listener', listener(h), { type: 'error', kind: 'microphone', message: 'Microphone permission denied.' });
  assert.equal(listener(h), undefined);
  const view = h.invoke('voice-wake-state', dashboard);
  assert.deepEqual(plain([view.enabled, view.reason, view.problem]), [true, 'blocked', 'Microphone permission denied.']);
  h.power.emit('unlock-screen'); assert.equal(listener(h), undefined, 'still waits for the user');
  await h.invoke('voice-wake-set', dashboard, { enabled: true }); assert.ok(listener(h), 'turned on again');
  h.emit('wake-listener', listener(h), { type: 'error', kind: 'service' });
  assert.equal(listener(h), undefined);
  assert.equal(h.invoke('voice-wake-state', dashboard).reason, 'retrying');
});

test('the app follows the dashboard theme choice: validated, from the dashboard only, kept for the next start', async () => {
  const h = harness({ alreadyUp: true, themeFile: JSON.stringify({ choice: 'dark' }) });
  await settle();
  // The saved choice applies before any window opens, so they open in it.
  assert.equal(h.theme.themeSource, 'dark');
  const [splash, dashboard] = h.windows;
  assert.equal(splash.options.backgroundColor, '#0a0a0a');
  assert.equal(dashboard.options.backgroundColor, '#0a0a0a');
  dashboard.url = 'http://127.0.0.1:8420/';
  h.emit('theme-set', dashboard, 'light');
  assert.equal(h.theme.themeSource, 'light');
  assert.equal(dashboard.background, '#fafafa');
  assert.deepEqual(h.calls.filter(call => call[0] === 'write' && call[1] === 'theme.json.tmp').at(-1), ['write', 'theme.json.tmp', '{"choice":"light"}']);
  // Anything but the three choices, or from another window or origin, is ignored.
  for (const value of ['purple', '', null, { choice: 'dark' }]) h.emit('theme-set', dashboard, value);
  assert.equal(h.theme.themeSource, 'light');
  dashboard.url = 'https://example.com/';
  h.emit('theme-set', dashboard, 'dark');
  assert.equal(h.theme.themeSource, 'light');
  dashboard.url = 'http://127.0.0.1:8420/settings/';
  h.emit('theme-set', dashboard, 'system');
  assert.equal(h.theme.themeSource, 'system');
  // "System" follows the operating system as it changes.
  h.theme.systemDark = true;
  h.theme.emit('updated');
  assert.equal(dashboard.background, '#0a0a0a');
});

test('without a saved theme the app follows the system', async () => {
  const h = harness({ alreadyUp: true });
  await settle();
  assert.equal(h.theme.themeSource, 'system');
  assert.equal(h.windows[1].options.backgroundColor, '#fafafa');
});

test('a reminder that rings notifies while the dashboard is away, and its click opens it there', async () => {
  const h = harness({ alreadyUp: true });
  await settle();
  const dashboard = h.windows[1];
  dashboard.url = 'http://127.0.0.1:8420/reminders/';
  const id = 'a'.repeat(32);
  const notes = () => h.calls.filter(call => call[0] === 'notification');
  // In front, the dashboard's own toast is enough.
  h.emit('reminder-notify', dashboard, { id, title: 'Reminder: call Dan', body: '' });
  assert.deepEqual(notes(), []);
  dashboard.hide();
  // Malformed alerts and other origins are ignored.
  for (const value of [null, { id: 'x', title: 't' }, { id, title: 7 }]) h.emit('reminder-notify', dashboard, value);
  dashboard.url = 'https://example.com/';
  h.emit('reminder-notify', dashboard, { id, title: 'Reminder: call Dan', body: '' });
  assert.deepEqual(notes(), []);
  dashboard.url = 'http://127.0.0.1:8420/';
  h.emit('reminder-notify', dashboard, { id, title: 'Reminder: call Dan', body: 'Late: was due today 10:00.' });
  assert.deepEqual(notes(), [['notification', 'Reminder: call Dan', 'Late: was due today 10:00.']]);
  h.calls.notification.emit('click');
  assert.equal(dashboard.visible, true);
  assert.deepEqual(dashboard.sent.at(-1), ['reminder-open', id]);
});
