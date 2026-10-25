// Opt-in native graphical smoke against a disposable, already running API.
// Never point this at a contributor's real profiles/memories as a test.
import { _electron as electron } from '../imprint/ui/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const root = path.dirname(fileURLToPath(import.meta.url));
const url = process.argv[2];
const output = process.argv[3];
if (!url || !output || !['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) {
  throw Error('Usage: node ui-electron/smoke-voice.mjs http://127.0.0.1:PORT /path/to/new-output');
}
await fs.mkdir(output, { mode: 0o700 });
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'imprint-electron-echo-'));
let app;
try {
  const environment = { ...process.env, XDG_CONFIG_HOME: profile };
  delete environment.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ executablePath: require('electron'),
    args: [path.join(root, 'main.js'), `--imprint-url=${url}`, `--user-data-dir=${profile}`,
      ...process.platform === 'linux' ? ['--no-sandbox'] : [], '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader',
      // Synthetic devices only: the production permission handlers and OS consent check still run.
      '--use-fake-device-for-media-stream'], env: environment });
  const actualProfile = await app.evaluate(({ app }) => app.getPath('userData'));
  if (!actualProfile.startsWith(profile)) throw Error('Native smoke requires a disposable profile');
  // Hosted desktops need not have a physical playback endpoint. A silent sink
  // still runs the graph/clock; production keeps the user's actual speakers.
  await app.context().addInitScript(() => {
    const Original = window.AudioContext;
    window.AudioContext = class extends Original {
      constructor(options) { super({ ...options, sinkId: { type: 'none' } }); }
    };
  });
  let main;
  for (let i = 0; i < 100; i++) {
    main = app.windows().find(window => window.url().startsWith(url));
    const visible = await app.evaluate(({ BrowserWindow }, origin) => BrowserWindow.getAllWindows()
      .some(window => window.webContents.getURL().startsWith(origin) && window.isVisible()), url);
    if (main && visible) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!main) throw Error('Dashboard did not open');
  await main.goto(`${url}/voice/`);
  await main.locator('.echo-canvas[data-loaded=true]').waitFor();
  await main.screenshot({ path: path.join(output, 'dashboard.png') });
  const toggle = main.getByRole('switch', { name: 'Floating Echo' });
  await toggle.waitFor();
  const opened = app.waitForEvent('window');
  await toggle.click();
  const popup = await opened;
  await popup.waitForURL('**/companion/');
  await popup.locator('.echo-canvas[data-loaded=true]').waitFor();
  const renderer = await popup.locator('.echo-canvas').getAttribute('data-renderer');
  const frameRateLimit = Number(await popup.locator('.echo-canvas').getAttribute('data-frame-rate-limit'));
  if (renderer === 'webgl' && frameRateLimit !== 10) {
    throw Error('The forced software WebGL test renderer must use its CPU frame-rate cap');
  }
  const surface = await popup.locator('.voice-compact').evaluate(element => {
    const style = getComputedStyle(element);
    return { background: style.backgroundColor, image: style.backgroundImage, border: style.borderTopWidth };
  });
  if (surface.background !== 'rgba(0, 0, 0, 0)' || surface.image !== 'none' || surface.border !== '0px') {
    throw Error('Companion must have no background panel or border: ' + JSON.stringify(surface));
  }
  const bounds = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .find(window => window.webContents.getURL().endsWith('/companion/')).getBounds());
  const beforeDrag = await bounds();
  await popup.mouse.move(200, 220); await popup.mouse.down();
  await popup.mouse.move(240, 245, { steps: 4 }); await popup.mouse.up();
  const afterDrag = await bounds();
  if (beforeDrag.x === afterDrag.x && beforeDrag.y === afterDrag.y) throw Error('Dragging the head did not move its native window');
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .find(window => window.webContents.getURL().endsWith('/companion/')).setPosition(-20000,-20000));
  await main.evaluate(() => window.electronAPI.recoverCompanion());
  let recovered;
  for (let i=0;i<40;i++) {
    recovered = await app.evaluate(({ BrowserWindow, screen, globalShortcut }) => {
      const window = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('/companion/'));
      const b = window.getBounds(), area = screen.getDisplayMatching(b).workArea;
      return { visible: window.isVisible(), bounds: b, area,
        inBounds: b.x >= area.x && b.y >= area.y && b.x+b.width <= area.x+area.width && b.y+b.height <= area.y+area.height,
        shortcutRegistered: globalShortcut.isRegistered('CommandOrControl+Alt+Shift+E') };
    });
    if (recovered.visible && recovered.inBounds) break;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if (!recovered.visible || !recovered.inBounds) throw Error('Echo recovery did not restore an offscreen head: '+JSON.stringify(recovered));
  const overflow = await popup.evaluate(() => document.documentElement.scrollHeight > innerHeight || document.documentElement.scrollWidth > innerWidth);
  if (overflow) throw Error('The compact voice view must fit without document scrolling');
  await popup.getByRole('button', { name: 'Companion settings' }).click();
  await popup.getByRole('button', { name: 'Keep companion on top' }).click();
  const pinned = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .find(window => window.webContents.getURL().endsWith('/companion/'))?.isAlwaysOnTop());
  if (!pinned) throw Error('Companion did not pin');
  await popup.getByLabel('Voice reasoning model').selectOption('lfm');
  await popup.keyboard.press('Escape');
  await popup.getByRole('button', { name: 'Start conversation' }).click();
  // Include integrity verification, model load and owned-process cleanup.
  await popup.getByRole('button', { name: 'Enable microphone' }).waitFor({ timeout: 130000 });
  const testMicrophone = process.platform !== 'darwin';
  // Real macOS TCC consent is a human OS dialog, even for a fake Chromium mic.
  // Check typed-session lifecycle there without bypassing production policy.
  if (testMicrophone) {
    await popup.getByRole('button', { name: 'Enable microphone' }).click();
    await popup.getByRole('button', { name: 'Hold to talk', exact: true }).waitFor({ timeout: 15000 });
  }
  await popup.screenshot({ path: path.join(output, 'companion.png'), omitBackground: true });
  // The dashboard switch must close an active microphone/session, not merely
  // hide an invisible window. Focus it because the companion is pinned on top.
  const closed = popup.waitForEvent('close');
  await main.bringToFront();
  await toggle.click();
  await closed;
  if (await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .some(window => window.webContents.getURL().endsWith('/companion/')))) throw Error('Companion survived close');
  for (let i = 0; i < 100; i++) {
    const state = await fetch(`${url}/api/voice/status`).then(response => response.json());
    if (!state.busy) break;
    if (i === 99) throw Error('Turning the companion off left a voice session active');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  // The actual dashboard Minimize and Close controls keep the API alive.
  await main.evaluate(() => window.electronAPI.minimize());
  let launcher;
  for (let i=0;i<50;i++) { launcher=app.windows().find(w=>w.url().endsWith('/launcher/')); if(launcher)break; await new Promise(resolve=>setTimeout(resolve,100)); }
  if (!launcher) throw Error('Minimizing did not open the floating launcher');
  await launcher.locator('.imprint-launcher[data-ready=true]').waitFor();
  await launcher.getByRole('button', { name: 'Open Imprint' }).waitFor();
  const launcherBounds = () => app.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().endsWith('/launcher/')).getBounds());
  const oldLauncher = await launcherBounds();
  await launcher.mouse.move(28,28); await launcher.mouse.down(); await launcher.mouse.move(48,42,{steps:3}); await launcher.mouse.up();
  let newLauncher = await launcherBounds();
  for(let i=0;i<30 && oldLauncher.x===newLauncher.x && oldLauncher.y===newLauncher.y;i++){
    await new Promise(resolve=>setTimeout(resolve,100)); newLauncher=await launcherBounds();
  }
  if(oldLauncher.x===newLauncher.x && oldLauncher.y===newLauncher.y) throw Error('Floating launcher did not drag');
  await launcher.getByRole('button',{name:'Open Imprint'}).click();
  await main.waitForTimeout(150);
  await main.evaluate(()=>window.electronAPI.close());
  await launcher.getByRole('button',{name:'Open Imprint'}).click();
  const restored = await app.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().endsWith('/voice/')).isVisible());
  if(!restored) throw Error('Launcher did not restore the closed dashboard');
  const result = { platform: `${process.platform}/${process.arch}`, electron: await app.evaluate(() => process.versions.electron),
    disposableProfile: true, realCompanionLoaded: true, wholeHeadDrag: true, offscreenRecovery: true, shortcutRegistered: recovered.shortcutRegistered, compactWithoutScroll: true, minimizeAndCloseToLauncher: true, launcherDrag: true, alwaysOnTop: true, syntheticMicrophonePermission: testMicrophone,
    transparentWithoutPanel: true, dashboardToggleStopsSession: true, stopAndClose: true, silentWebAudioSink: true, renderer, frameRateLimit,
    limits: [testMicrophone ? 'synthetic microphone' : 'microphone capture not exercised',
      'no real acoustic/output-device test', 'silent WebAudio test output',
      renderer === 'canvas-3d' ? 'software 3D renderer' : 'software WebGL',
      ...process.platform === 'darwin' ? ['interactive macOS TCC microphone consent requires manual validation'] : [],
      ...process.platform === 'linux' ? ['no-sandbox for the Linux graphical test'] : []] };
  await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
} catch (error) {
  if (app) {
    for (const [index, page] of app.windows().entries()) {
      await page.screenshot({ path: path.join(output, `failure-${index}.png`), omitBackground: true }).catch(() => {});
      const content = await page.locator('body').innerText().catch(() => 'Window unavailable');
      await fs.writeFile(path.join(output, `failure-${index}.txt`), content.slice(0, 12000));
      const graphics = await page.locator('.echo-canvas').getAttribute('data-error').catch(() => null);
      if (graphics) await fs.writeFile(path.join(output, `graphics-${index}.txt`), graphics);
    }
  }
  throw error;
} finally {
  await app?.close();
  await fs.rm(profile, { recursive: true, force: true });
}
