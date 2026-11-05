// The orb, its ring of actions, Echo folding into it and a recording started
// from it, in real Electron with the real dashboard bundle, a disposable
// fixture API and profile, and Chromium's fake microphone (ADR 0135). The
// computer's audio is left out: capturing it asks the OS for screen access.
// Usage: node ui-electron/smoke-orb.mjs [dashboard-out] [screenshot-directory]
import { _electron as electron } from '../imprint/ui/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const root = path.dirname(fileURLToPath(import.meta.url));
const bundle = path.resolve(process.argv[2] || path.join(root, '../imprint/ui/out'));
const output = process.argv[3];
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'imprint-orb-'));
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.glb': 'model/gltf-binary', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };
const recording = { id: 'r1', title: 'Smoke call', project: 'meetings', status: 'recording', started_at: Date.now() / 1000, duration: 0, others: false, audio_bytes: 0, memories: 0, transcript: false };
const requests = [];
function json(response, value) { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(value)); }
const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://fixture').pathname;
  if (pathname.startsWith('/api/')) {
    let body = ''; for await (const chunk of request) body += chunk;
    if (request.method !== 'GET') requests.push([request.method, pathname, request.headers['content-type']?.startsWith('application/json') ? JSON.parse(body || '{}') : body.length]);
    if (pathname === '/api/recordings' && request.method === 'POST') return json(response, recording);
    if (pathname === '/api/recordings') return json(response, { recordings: [], default_project: 'meetings' });
    if (pathname === '/api/recordings/r1/stop') return json(response, { ...recording, status: 'stopped', job: null });
    if (pathname.startsWith('/api/stats')) return json(response, { projects: [['meetings', 3], ['imprint', 40]], total_memories: 43 });
    if (pathname.endsWith('/voice/status')) return json(response, { installed: true, supported: true, chat_provider: 'local', busy: false });
    if (pathname.endsWith('/voice/setup')) return json(response, { stage: 'ready', installed: true, supported: true, busy: false });
    return json(response, pathname === '/api/workspaces' ? { active: 'default', known: ['default'] } : { ok: true });
  }
  const target = path.resolve(bundle, '.' + decodeURIComponent(pathname), pathname.endsWith('/') ? 'index.html' : '');
  if (!target.startsWith(bundle + path.sep)) { response.writeHead(403).end(); return; }
  try { response.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream'); response.end(await fs.readFile(target)); }
  catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const until = async (check, what, ms = 15000) => {
  for (const end = Date.now() + ms; Date.now() < end; await new Promise(resolve => setTimeout(resolve, 100))) {
    const value = await check();
    if (value) return value;
  }
  throw new Error(`Timed out waiting for ${what}`);
};
let app;
try {
  const env = { ...process.env, XDG_CONFIG_HOME: profile };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ executablePath: require('electron'), env,
    args: [path.join(root, 'main.js'), `--imprint-url=${origin}`, `--user-data-dir=${profile}`,
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', ...process.platform === 'linux' ? ['--no-sandbox'] : []] });
  // The fake microphone needs no OS consent; answer for the OS so no system prompt opens.
  await app.evaluate(({ systemPreferences }) => {
    systemPreferences.getMediaAccessStatus = () => 'granted';
    systemPreferences.askForMediaAccess = async () => true;
  });
  const windows = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map(window => ({
    url: window.webContents.getURL(), visible: window.isVisible(), bounds: window.getBounds() })));
  const windowAt = async route => (await windows()).find(window => new URL(window.url || 'about:blank').pathname === route);
  const page = async route => until(() => app.windows().find(window => window.url() !== 'about:blank' && new URL(window.url()).pathname === route), route);
  await until(async () => (await windows()).some(window => window.url.startsWith(origin) && window.visible), 'the dashboard');
  const dashboard = await page('/');
  dashboard.on('pageerror', error => console.log('dashboard error:', error.message));
  await dashboard.waitForLoadState('load');
  dashboard.on('pageerror', error => console.error('dashboard error:', error.message));
  // A page the fixture API can feed; the dashboard must move to Recordings by itself to capture.
  await dashboard.goto(`${origin}/voice/`);
  await dashboard.waitForLoadState('load');
  await dashboard.evaluate(() => window.electronAPI.minimize());
  const orb = await page('/launcher/');
  await orb.locator('.imprint-launcher[data-ready=true]').waitFor();
  const orbBounds = (await until(windowAt.bind(null, '/launcher/'), 'the orb window')).bounds;
  assert.equal(orbBounds.width, 72); assert.equal(orbBounds.height, 72);

  // A click opens the ring in its own window beside the orb, toward free space.
  await orb.getByRole('button', { name: 'Imprint' }).click();
  const halo = await page('/launcher/halo/');
  const menu = halo.getByRole('menu', { name: 'Imprint actions' });
  await menu.getByRole('menuitem', { name: 'New recording' }).waitFor();
  const ring = await until(async () => { const w = await windowAt('/launcher/halo/'); return w?.visible && w; }, 'the ring to show');
  assert.ok(ring.bounds.x <= orbBounds.x + 36 && ring.bounds.x + ring.bounds.width > orbBounds.x + 72, JSON.stringify([ring.bounds, orbBounds]));
  assert.deepEqual((await windowAt('/launcher/')).bounds, orbBounds, 'the orb did not move');
  await halo.waitForTimeout(400);
  if (output) await halo.screenshot({ path: path.join(output, 'orb-ring.png'), omitBackground: true });

  // Echo opens docked beside the orb, and folds back into it.
  await menu.getByRole('menuitem', { name: 'Show Echo' }).click();
  const echo = await until(async () => { const w = await windowAt('/companion/'); return w?.visible && w; }, 'Echo');
  assert.ok(Math.abs(echo.bounds.x - (orbBounds.x + 74)) <= 1 || Math.abs(echo.bounds.x + echo.bounds.width - (orbBounds.x - 2)) <= 1, JSON.stringify(echo.bounds));
  await orb.locator('.orb-rim-notch').waitFor({ state: 'attached' });
  const companion = await page('/companion/');
  await companion.getByRole('button', { name: 'Collapse Echo into the Imprint button' }).click();
  await until(async () => !(await windowAt('/companion/'))?.visible, 'Echo to fold into the orb');

  // A recording starts from the orb's panel only after consent, in the hidden dashboard.
  await orb.getByRole('button', { name: 'Imprint' }).click();
  await menu.getByRole('menuitem', { name: 'New recording' }).click();
  const dialog = halo.getByRole('dialog', { name: 'New recording' });
  await dialog.waitFor();
  await until(async () => (await windowAt('/launcher/halo/'))?.bounds.width === 344, 'the panel window');
  await dialog.getByRole('switch', { name: /Include what this computer plays/ }).click();
  await dialog.getByRole('checkbox', { name: /Everyone on this call knows/ }).check();
  await dialog.getByRole('textbox', { name: 'Title' }).fill('Smoke call');
  if (output) await halo.screenshot({ path: path.join(output, 'orb-record-panel.png'), omitBackground: true });
  await dialog.getByRole('button', { name: 'Start recording' }).click();
  await orb.locator('.orb-timer').waitFor({ timeout: 15000 }).catch(async error => {
    const alert = await halo.getByRole('alert').textContent().catch(() => '');
    console.error('requests', JSON.stringify(requests), 'dashboard at', dashboard.url(), 'panel says', alert);
    throw error;
  });
  assert.ok(requests.some(([method, route, body]) => method === 'POST' && route === '/api/recordings' && body.consent === true && body.others === false && body.title === 'Smoke call'));
  assert.equal(new URL(dashboard.url()).pathname, '/recordings/', 'the hidden dashboard moved to Recordings to capture');
  await until(async () => !(await windowAt('/launcher/halo/'))?.visible, 'the panel to fold into the orb');
  await until(() => requests.some(([method, route]) => method !== 'GET' && route === '/api/recordings/r1/audio'), 'audio to upload', 12000);
  if (output) await orb.screenshot({ path: path.join(output, 'orb-recording.png'), omitBackground: true });

  // The ring offers Stop while recording; Stop saves and queues transcription.
  await orb.getByRole('button', { name: /Recording/ }).click();
  await menu.getByRole('menuitem', { name: 'Stop recording and transcribe' }).waitFor();
  await halo.waitForTimeout(300);
  if (output) await halo.screenshot({ path: path.join(output, 'orb-ring-recording.png'), omitBackground: true });
  await menu.getByRole('menuitem', { name: 'Stop recording and transcribe' }).click();
  await until(() => requests.some(([method, route]) => method === 'POST' && route === '/api/recordings/r1/stop'), 'the recording to stop');
  await orb.locator('.orb-timer').waitFor({ state: 'detached' });

  // A double-click opens the dashboard and hides the orb.
  await orb.getByRole('button', { name: 'Imprint' }).dblclick();
  await until(async () => (await windows()).some(window => window.url.startsWith(origin) && new URL(window.url).pathname === '/recordings/' && window.visible), 'the dashboard');
  await until(async () => !(await windowAt('/launcher/'))?.visible, 'the orb to hide');
  console.log('orb smoke passed');
} finally {
  await app?.close().catch(() => {});
  server.close();
  await fs.rm(profile, { recursive: true, force: true }).catch(() => {});
}
