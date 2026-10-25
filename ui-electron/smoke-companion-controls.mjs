// Native companion controls and grouping with the real dashboard bundle and a
// disposable fixture API (ADR 0070). No model download, client profile, user
// memory or physical microphone.
// Usage: node ui-electron/smoke-companion-controls.mjs [dashboard-out] [screenshot-directory]
import { _electron as electron } from '../imprint/ui/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const root = path.dirname(fileURLToPath(import.meta.url));
const bundle = path.resolve(process.argv[2] || path.join(root, '../imprint/ui/out'));
const output = process.argv[3];
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'imprint-companion-controls-'));
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.glb': 'model/gltf-binary', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
const looks = [{ value: 'echo', label: 'Echo', detail: 'Holographic core', unavailable: '' },
  { value: 'iris', label: 'Iris', detail: 'Indigo straight cut', unavailable: '' },
  { value: 'kai', label: 'Kai', detail: 'Spiky orange hair', unavailable: '' }];
let look = 'echo';
const sessions = [];
function json(response, value) { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(value)); }
const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://fixture').pathname;
  if (pathname === '/api/config/voice.avatar' && request.method === 'PUT') {
    let body = ''; for await (const chunk of request) body += chunk;
    look = JSON.parse(body).value; json(response, { ok: true, value: look }); return;
  }
  if (pathname.startsWith('/api/')) {
    if (pathname.endsWith('/voice/sessions')) {
      let body = ''; for await (const chunk of request) body += chunk;
      sessions.push(JSON.parse(body || '{}'));
    }
    json(response, pathname.endsWith('/voice/setup') ? { stage: 'ready', installed: true, supported: true, busy: false }
      : pathname.endsWith('/voice/status') ? { installed: true, supported: true, chat_provider: 'local', busy: false }
      : pathname.endsWith('/voice/sessions') ? { token: 'fixture', workspace: 'default' }
      : pathname === '/api/config' ? { settings: [{ key: 'voice.avatar', value: look, source: 'config', default: 'echo', type: 'str',
        env: 'IMPRINT_VOICE_AVATAR', desc: 'How Echo looks', choices: looks, custom: false }] }
      : { active: 'default', known: ['default'] });
    return;
  }
  const target = path.resolve(bundle, '.' + decodeURIComponent(pathname), pathname.endsWith('/') ? 'index.html' : '');
  if (!target.startsWith(bundle + path.sep)) { response.writeHead(403).end(); return; }
  try { response.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream'); response.end(await fs.readFile(target)); }
  catch { response.writeHead(404).end(); }
});
// A real loopback WebSocket fixture. Context-level WebSocket interception races
// Electron's splash target teardown on some native runs.
const sockets = new Set();
server.on('upgrade', (request, socket) => {
  const key = request.headers['sec-websocket-key'];
  if (request.url !== '/api/voice/stream' || request.headers.origin !== origin || typeof key !== 'string') { socket.destroy(); return; }
  const accept = createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  sockets.add(socket); socket.on('close', () => sockets.delete(socket));
  let pending = Buffer.alloc(0), ready = false;
  socket.on('data', chunk => {
    pending = Buffer.concat([pending, chunk]);
    while (pending.length >= 2) {
      const kind = pending[0] & 15, length = pending[1] & 127;
      // This fixture handles small control/capability messages only.
      if (!(pending[1] & 128) || length >= 126) { socket.destroy(); return; }
      if (pending.length < length + 6) return;
      const payload = pending.subarray(6, 6 + length).map((value, index) => value ^ pending[2 + index % 4]);
      pending = pending.subarray(6 + length);
      if (kind === 8) { socket.end(Buffer.from([0x88, 0])); return; }
      if (kind === 1 && !ready) {
        try { if (JSON.parse(payload).token !== 'fixture') { socket.destroy(); return; } }
        catch { socket.destroy(); return; }
        ready = true;
        const welcome = Buffer.from(JSON.stringify({ type: 'ready', workspace: 'default' }));
        socket.write(Buffer.concat([Buffer.from([0x81, welcome.length]), welcome]));
      }
    }
  });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app;
try {
  const env = { ...process.env, XDG_CONFIG_HOME: profile }; delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ executablePath: require('electron'), env, args: [path.join(root, 'main.js'),
    `--imprint-url=${origin}`, `--user-data-dir=${profile}`, '--use-fake-device-for-media-stream',
    '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader', ...process.platform === 'linux' ? ['--no-sandbox'] : []] });
  assert.equal(await app.evaluate(({ app }) => app.getPath('userData')), profile);
  let dashboard;
  for (let i = 0; i < 100 && !dashboard; i++) { dashboard = app.windows().find(page => page.url().startsWith(origin)); if (!dashboard) await pause(100); }
  assert.ok(dashboard, 'dashboard opened');
  await dashboard.waitForLoadState();
  await dashboard.goto(origin + '/voice/');
  const opened = app.waitForEvent('window');
  await dashboard.evaluate(() => window.electronAPI.openCompanion());
  const popup = await opened; await popup.waitForURL('**/companion/');
  await popup.locator('.echo-canvas[data-loaded=true]').waitFor();
  await popup.evaluate(() => {
    const Original = window.AudioContext;
    window.AudioContext = class extends Original { constructor(options) { super({ ...options, sinkId: { type: 'none' } }); } };
  });
  assert.equal(await popup.getByRole('combobox').count(), 0, 'settings stay hidden until asked for');
  await popup.getByRole('button', { name: 'Companion settings' }).click();
  const model = popup.getByRole('combobox', { name: 'Voice reasoning model' });
  // Open the actual native select and choose with the keyboard, not selectOption.
  await model.click(); await popup.keyboard.press('ArrowDown'); await popup.keyboard.press('Enter');
  assert.equal(await model.inputValue(), 'lfm');
  await popup.getByRole('group', { name: "Echo's look" }).getByRole('button', { name: /^Kai:/ }).click();
  for (let i = 0; i < 50 && look !== 'kai'; i++) await pause(100);
  assert.equal(look, 'kai', 'the look picker saved the choice');
  // The actual checkbox, by its label as a person clicks it (ADR 0069).
  const checkbox = popup.getByRole('checkbox', { name: 'Use microphone for conversations' });
  await popup.getByText('Use microphone for conversations', { exact: true }).click(); assert.equal(await checkbox.isChecked(), true);
  await checkbox.click(); assert.equal(await checkbox.isChecked(), false);
  if (output) { await fs.mkdir(output, { recursive: true }); await popup.screenshot({ path: path.join(output, 'settings.png'), omitBackground: true }); }
  await popup.keyboard.press('Escape');
  await popup.getByRole('dialog', { name: 'Companion settings' }).waitFor({ state: 'detached' });
  // Real macOS consent is a person answering an OS dialog; elsewhere the
  // production handlers grant the synthetic device to the companion.
  const microphone = process.platform !== 'darwin';
  if (microphone) {
    await popup.getByRole('button', { name: 'Start conversation' }).click();
    await popup.getByRole('button', { name: 'Enable microphone' }).click();
    await popup.getByRole('button', { name: 'Hold to talk', exact: true }).waitFor();
    await popup.getByRole('button', { name: 'Companion settings' }).click();
    await popup.getByRole('button', { name: 'Stop conversation and microphone' }).click();
    await popup.keyboard.press('Escape');
    // Remembered: the next conversation arms the microphone without another click.
    await popup.getByRole('button', { name: 'Start conversation' }).click();
    await popup.getByRole('button', { name: 'Hold to talk', exact: true }).waitFor();
  }

  const bounds = () => app.evaluate(({ BrowserWindow }) => {
    const windows = BrowserWindow.getAllWindows();
    return { head: windows.find(w => w.webContents.getURL().endsWith('/companion/'))?.getBounds(),
      icon: windows.find(w => w.webContents.getURL().endsWith('/launcher/'))?.getBounds() };
  });
  const settledBounds = async () => {
    let previous, stable = 0;
    for (let i = 0; i < 50; i++) {
      const value = await bounds();
      if (JSON.stringify(value) === JSON.stringify(previous)) stable++; else stable = 0;
      if (stable >= 3) return value;
      previous = value;
      await pause(100);
    }
    throw Error('Native group positions did not settle');
  };
  await dashboard.evaluate(() => window.electronAPI.close());
  let launcher;
  for (let i = 0; i < 100 && !launcher; i++) { launcher = app.windows().find(page => page.url().endsWith('/launcher/')); if (!launcher) await pause(100); }
  assert.ok(launcher, 'closing the dashboard leaves the launcher'); await launcher.getByRole('button', { name: 'Open Imprint' }).waitFor();
  const relative = value => ({ x: value.head.x - value.icon.x, y: value.head.y - value.icon.y });
  let before = await settledBounds();
  assert.ok(before.head.x === before.icon.x + before.icon.width + 10
    || before.icon.x === before.head.x + before.head.width + 10
    || before.icon.y === before.head.y + before.head.height + 10, 'head docks beside the launcher');
  await launcher.mouse.move(28, 28); await launcher.mouse.down(); await launcher.mouse.move(68, 48, { steps: 4 }); await launcher.mouse.up();
  let after = await settledBounds(); assert.notDeepEqual(after.icon, before.icon); assert.deepEqual(relative(after), relative(before));
  before = after;
  const box = await popup.locator('.echo-canvas').boundingBox();
  await popup.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await popup.mouse.down();
  await popup.mouse.move(box.x + box.width / 2 + 30, box.y + box.height / 2 - 20, { steps: 4 }); await popup.mouse.up();
  after = await settledBounds(); assert.notDeepEqual(after.head, before.head); assert.deepEqual(relative(after), relative(before));
  if (output) await popup.screenshot({ path: path.join(output, 'companion.png'), omitBackground: true });
  console.log(JSON.stringify({ platform: process.platform, actualNativeSelect: true, lookPicker: true, checkbox: true,
    rememberedSyntheticMicrophone: microphone, groupedDragFromBothWindows: true,
    limits: ['fixture API', 'synthetic devices', 'no physical voice quality or interactive macOS TCC validation'] }));
} finally {
  await app?.close(); for (const socket of sockets) socket.destroy();
  await new Promise(resolve => server.close(resolve)); await fs.rm(profile, { recursive: true, force: true });
}
