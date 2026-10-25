// Real Electron permission handlers and OS consent IPC (ADR 0069) with synthetic
// devices, no fake permission UI, no speech models, no API/user memories, and a
// disposable Electron profile.
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
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'imprint-microphone-'));
const server = http.createServer((_request, response) => {
  response.setHeader('Content-Type', 'text/html');
  response.end('<!doctype html><title>Disposable microphone fixture</title>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let app;
try {
  const env = { ...process.env, XDG_CONFIG_HOME: profile };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ executablePath: require('electron'), env,
    args: [path.join(root, 'main.js'), `--imprint-url=${origin}`, `--user-data-dir=${profile}`,
      '--use-fake-device-for-media-stream', ...process.platform === 'linux' ? ['--no-sandbox'] : []] });
  assert.equal(await app.evaluate(({ app }) => app.getPath('userData')), profile);
  let page;
  for (let i = 0; i < 100; i++) {
    page = app.windows().find(window => window.url().startsWith(origin));
    if (page) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(page, 'dashboard opened');
  assert.equal((await page.evaluate(() => window.electronAPI.requestMicrophoneAccess())).status, 'denied');
  assert.equal(await page.evaluate(() => window.electronAPI.openMicrophoneSettings()), false);
  if (process.platform !== 'darwin') {
    const capture = async constraints => page.evaluate(async constraints => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        const tracks = stream.getTracks().map(track => track.kind);
        stream.getTracks().forEach(track => track.stop());
        return tracks;
      } catch (error) { return error.name; }
    }, constraints);
    assert.equal(await capture({ audio: true }), 'NotAllowedError');
    for (const route of ['/voice/', '/companion/']) {
      await page.goto(origin + route);
      const consent = await page.evaluate(() => window.electronAPI.requestMicrophoneAccess());
      assert.ok(['granted', 'unmanaged', 'unknown', 'not-determined'].includes(consent.status), JSON.stringify(consent));
      assert.deepEqual(await capture({ audio: true }), ['audio']);
      assert.equal(await capture({ video: true }), 'NotAllowedError');
      assert.equal(await capture({ audio: true, video: true }), 'NotAllowedError');
      await page.evaluate(() => { const frame = document.createElement('iframe'); frame.src = '/voice/'; document.body.append(frame); });
      const frame = await page.waitForSelector('iframe');
      const child = await frame.contentFrame();
      await child.waitForLoadState();
      assert.equal(await child.evaluate(async () => {
        try { const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); stream.getTracks().forEach(track => track.stop()); return 'unexpected grant'; }
        catch (error) { return error.name; }
      }), 'NotAllowedError');
    }
  }
  console.log(JSON.stringify({ platform: process.platform, productionPermissionHandlers: true,
    syntheticCapture: process.platform !== 'darwin', permissionUIBypass: false,
    limits: ['synthetic devices only', ...process.platform === 'darwin' ? ['interactive TCC capture not exercised'] : [],
      ...process.platform === 'linux' ? ['WSL/Xvfb smoke uses no-sandbox'] : []] }));
} finally {
  await app?.close();
  await new Promise(resolve => server.close(resolve));
  await fs.rm(profile, { recursive: true, force: true });
}
