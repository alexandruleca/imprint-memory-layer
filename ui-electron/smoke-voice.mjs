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
      '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'], env: environment });
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
  const surface = await popup.locator('.voice-compact').evaluate(element => {
    const style = getComputedStyle(element);
    return { background: style.backgroundColor, image: style.backgroundImage, border: style.borderTopWidth };
  });
  if (surface.background !== 'rgba(0, 0, 0, 0)' || surface.image !== 'none' || surface.border !== '0px') {
    throw Error('Companion must have no background panel or border: ' + JSON.stringify(surface));
  }
  await popup.getByRole('button', { name: 'Keep companion on top' }).click();
  const pinned = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .find(window => window.webContents.getURL().endsWith('/companion/'))?.isAlwaysOnTop());
  if (!pinned) throw Error('Companion did not pin');
  await popup.getByLabel('Voice reasoning model').selectOption('lfm');
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
  const result = { platform: `${process.platform}/${process.arch}`, electron: await app.evaluate(() => process.versions.electron),
    disposableProfile: true, realCompanionLoaded: true, alwaysOnTop: true, syntheticMicrophonePermission: testMicrophone,
    transparentWithoutPanel: true, dashboardToggleStopsSession: true, stopAndClose: true, silentWebAudioSink: true, renderer,
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
