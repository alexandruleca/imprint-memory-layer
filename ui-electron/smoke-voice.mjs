// Opt-in native graphical smoke against a disposable, already running API.
// Never point this at a contributor's real profiles/memories as a test.
import { _electron as electron } from '../imprint/ui/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
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
  app = await electron.launch({ executablePath: path.join(root, 'node_modules/electron/dist/electron'),
    args: [path.join(root, 'main.js'), `--imprint-url=${url}`, '--no-sandbox', '--enable-unsafe-swiftshader',
      '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'], env: environment });
  const actualProfile = await app.evaluate(({ app }) => app.getPath('userData'));
  if (!actualProfile.startsWith(profile)) throw Error('Native smoke requires a disposable Linux profile');
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
  const opened = app.waitForEvent('window');
  await main.getByRole('button', { name: 'Float Echo' }).click();
  const popup = await opened;
  await popup.waitForURL('**/companion/');
  await popup.locator('.echo-canvas[data-loaded=true]').waitFor();
  await popup.getByRole('button', { name: 'Keep companion on top' }).click();
  const pinned = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .find(window => window.webContents.getURL().endsWith('/companion/'))?.isAlwaysOnTop());
  if (!pinned) throw Error('Companion did not pin');
  await popup.getByLabel('Voice reasoning model').selectOption('lfm');
  await popup.getByRole('button', { name: 'Start conversation' }).click();
  await popup.getByRole('button', { name: 'Enable microphone' }).waitFor({ timeout: 20000 });
  await popup.getByRole('button', { name: 'Enable microphone' }).click();
  await popup.getByRole('button', { name: 'Hold to talk', exact: true }).waitFor({ timeout: 15000 });
  await popup.screenshot({ path: path.join(output, 'companion.png') });
  await popup.getByRole('button', { name: 'Stop conversation and microphone' }).click();
  const closed = popup.waitForEvent('close');
  await popup.getByRole('button', { name: 'Close companion' }).click().catch(error => {
    if (!popup.isClosed()) throw error; // IPC can destroy the frame before Playwright acknowledges the click.
  });
  await closed;
  if (await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .some(window => window.webContents.getURL().endsWith('/companion/')))) throw Error('Companion survived close');
  const result = { platform: 'Linux/WSLg', electron: await app.evaluate(() => process.versions.electron),
    disposableProfile: true, realCompanionLoaded: true, alwaysOnTop: true, syntheticMicrophonePermission: true,
    stopAndClose: true, limits: ['synthetic microphone', 'no real acoustic test', 'no native Windows/macOS test',
      'no-sandbox and software WebGL were used for the disposable WSL graphical test'] };
  await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
} finally {
  await app?.close();
  await fs.rm(profile, { recursive: true, force: true });
}
