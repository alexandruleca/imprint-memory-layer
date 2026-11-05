// "Hey Echo" (ADR 0137) in real Electron with the real dashboard bundle, a
// disposable profile that has chosen it, a fixture API, and Chromium's fake
// microphone playing a recording of "Hey Echo, what did we decide yesterday?"
// (macOS `say`; elsewhere a synthetic burst). Checks that the hidden listener
// window runs and the orb shows its ear, that a wake shows Echo without focus
// and hands it the question, that Echo goes back once the conversation ends
// and listening resumes, and reports the listener's CPU.
//
// The fixture answers the listener's probes itself. With IMPRINT_WAKE_SMOKE_MODEL
// set to a folder holding the pinned tiny.en files (and IMPRINT_WAKE_SMOKE_PYTHON
// to an Imprint Python), the probes go to the real wake worker instead.
// Usage: node ui-electron/smoke-wake.mjs [dashboard-out]
import { _electron as electron } from '../imprint/ui/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const root = path.dirname(fileURLToPath(import.meta.url));
const bundle = path.resolve(process.argv[2] || path.join(root, '../imprint/ui/out'));
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'imprint-wake-'));
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.glb': 'model/gltf-binary', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };
const until = async (check, what, ms = 30000) => {
  for (const end = Date.now() + ms; Date.now() < end; await new Promise(resolve => setTimeout(resolve, 100))) {
    const value = await check();
    if (value) return value;
  }
  throw new Error(`Timed out waiting for ${what}`);
};

// ── The fake microphone: 1 s of quiet, the sentence, then 40 s of quiet (it loops).
const RATE = 16000;
function wav(samples) {
  const data = Buffer.alloc(samples.length * 2);
  samples.forEach((value, index) => data.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(value * 32767))), index * 2));
  const header = Buffer.alloc(44);
  header.write('RIFF', 0); header.writeUInt32LE(36 + data.length, 4); header.write('WAVEfmt ', 8);
  header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22); header.writeUInt32LE(RATE, 24);
  header.writeUInt32LE(RATE * 2, 28); header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34); header.write('data', 36); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}
async function speech() {
  const spoken = path.join(profile, 'spoken.wav');
  if (process.platform === 'darwin' && spawnSync('say', ['-v', 'Samantha', '-o', spoken, '--file-format=WAVE', '--data-format=LEI16@16000',
    'Hey Echo, what did we decide yesterday?']).status === 0) {
    const file = await fs.readFile(spoken);
    const at = file.indexOf('data'), length = file.readUInt32LE(at + 4);
    const pcm = file.subarray(at + 8, at + 8 + length);
    return { kind: 'say', samples: Array.from({ length: pcm.length / 2 }, (_, index) => pcm.readInt16LE(index * 2) / 32768) };
  }
  // A loud 220 Hz burst, which the fixture treats as the phrase.
  return { kind: 'tone', samples: Array.from({ length: Math.round(2.2 * RATE) }, (_, index) => .4 * Math.sin(2 * Math.PI * 220 * index / RATE)) };
}
const sentence = await speech();
const microphone = path.join(profile, 'microphone.wav');
await fs.writeFile(microphone, wav([...new Array(RATE).fill(0), ...sentence.samples, ...new Array(40 * RATE).fill(0)]));

// ── An optional real wake worker.
const MODEL = 'faster-whisper-tiny.en-0d3d19a32d33';
let worker = null;
const answers = new Map();
if (process.env.IMPRINT_WAKE_SMOKE_MODEL) {
  const data = path.join(profile, 'data');
  await fs.mkdir(path.join(data, 'models', MODEL), { recursive: true });
  for (const name of ['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt']) {
    await fs.symlink(path.join(process.env.IMPRINT_WAKE_SMOKE_MODEL, name), path.join(data, 'models', MODEL, name));
  }
  const ready = new Promise(resolve => answers.set('ready', resolve));
  worker = spawn(process.env.IMPRINT_WAKE_SMOKE_PYTHON || 'python3', ['-m', 'imprint.wake_worker'],
    { cwd: path.join(root, '..'), env: { ...process.env, IMPRINT_DATA_DIR: data, OMP_NUM_THREADS: '1' }, stdio: ['pipe', 'pipe', 'ignore'] });
  let pending = '';
  worker.stdout.on('data', chunk => {
    pending += chunk;
    for (let at; (at = pending.indexOf('\n')) >= 0; pending = pending.slice(at + 1)) {
      const event = JSON.parse(pending.slice(0, at));
      answers.get(event.ready ? 'ready' : event.id)?.(event);
    }
  });
  const loaded = await Promise.race([ready, new Promise(resolve => setTimeout(() => resolve(null), 60000))]);
  assert.ok(loaded?.ready, `the wake worker did not start: ${JSON.stringify(loaded)}`);
}
const ask = message => new Promise(resolve => { answers.set(message.id, resolve); worker.stdin.write(JSON.stringify(message) + '\n'); });

// ── A minimal WebSocket server for the fixture's two sockets.
function frame(text) {
  const data = Buffer.from(text);
  let head;
  if (data.length < 126) head = Buffer.from([0x81, data.length]);
  else if (data.length < 65536) { head = Buffer.alloc(4); head[0] = 0x81; head[1] = 126; head.writeUInt16BE(data.length, 2); }
  else { head = Buffer.alloc(10); head[0] = 0x81; head[1] = 127; head.writeBigUInt64BE(BigInt(data.length), 2); }
  return Buffer.concat([head, data]);
}
function accept(request, socket, handle) {
  const key = crypto.createHash('sha1').update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${key}\r\n\r\n`);
  const peer = { send: value => { if (!socket.destroyed) socket.write(frame(JSON.stringify(value))); } };
  let buffer = Buffer.alloc(0), parts = [];
  socket.on('error', () => {});
  socket.on('data', chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    for (;;) {
      if (buffer.length < 2) return;
      const opcode = buffer[0] & 15, fin = buffer[0] & 128, masked = buffer[1] & 128;
      let length = buffer[1] & 127, at = 2;
      if (length === 126) { if (buffer.length < 4) return; length = buffer.readUInt16BE(2); at = 4; }
      else if (length === 127) { if (buffer.length < 10) return; length = Number(buffer.readBigUInt64BE(2)); at = 10; }
      const mask = masked ? buffer.subarray(at, at + 4) : null;
      at += masked ? 4 : 0;
      if (buffer.length < at + length) return;
      const payload = Buffer.from(buffer.subarray(at, at + length));
      if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
      buffer = buffer.subarray(at + length);
      if (opcode === 8) { socket.end(); return; }
      if (opcode === 9) { socket.write(Buffer.concat([Buffer.from([0x8a, payload.length]), payload])); continue; }
      if (opcode === 1 || opcode === 0) parts.push(payload);
      if (fin && parts.length) { const text = Buffer.concat(parts).toString(); parts = []; void handle(JSON.parse(text), peer); }
    }
  });
}

// ── The fixture API.
const seen = { probes: [], utterances: [], turns: [], cpu: [] };
let woke = false;
const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://fixture').pathname;
  const json = value => { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(value)); };
  if (pathname.startsWith('/api/')) {
    for await (const chunk of request) void chunk;
    if (pathname.endsWith('/voice/wake/status')) return json({ installed: true, model: 'tiny.en', bytes: 78090594, idle_seconds: 5, listening: true, setup: { stage: 'ready', busy: false } });
    if (pathname.endsWith('/voice/wake/sessions')) return json({ token: 'wake', expires_in: 30 });
    if (pathname.endsWith('/voice/status')) return json({ installed: true, configured: true, supported: true, chat_provider: 'local', busy: false, workspace: 'default' });
    if (pathname.endsWith('/voice/setup')) return json({ stage: 'ready', installed: true, supported: true, busy: false });
    if (pathname.endsWith('/voice/sessions')) return json({ token: 'echo', workspace: 'default', expires_in: 30 });
    if (pathname.startsWith('/api/stats')) return json({ projects: [['imprint', 40]], total_memories: 40 });
    return json(pathname === '/api/workspaces' ? { active: 'default', known: ['default'] } : { ok: true });
  }
  const target = path.resolve(bundle, '.' + decodeURIComponent(pathname), pathname.endsWith('/') ? 'index.html' : '');
  if (!target.startsWith(bundle + path.sep)) { response.writeHead(403).end(); return; }
  try { response.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream'); response.end(await fs.readFile(target)); }
  catch { response.writeHead(404).end(); }
});
server.on('upgrade', (request, socket) => {
  const pathname = new URL(request.url, 'http://fixture').pathname;
  if (pathname === '/api/voice/wake/stream') accept(request, socket, async (message, peer) => {
    if (message.token) return peer.send({ type: 'ready' });
    if (message.type === 'probe') {
      seen.probes.push(message.id);
      // One wake per run: later probes (the looped recording) are answered no.
      let wake = !woke;
      if (wake && worker) { const answer = await ask({ id: message.id, kind: 'probe', audio: message.audio }); seen.cpu.push(answer.cpu); wake = answer.wake === true; }
      woke ||= wake;
      return peer.send({ type: 'probe', id: message.id, wake });
    }
    if (message.type === 'utterance') {
      const text = worker ? (await ask({ id: message.id, kind: 'utterance', audio: message.audio })).text : 'what did we decide yesterday?';
      seen.utterances.push(text);
      return peer.send({ type: 'question', id: message.id, text });
    }
  });
  else if (pathname === '/api/voice/stream') accept(request, socket, (message, peer) => {
    if (message.token) { peer.send({ type: 'connecting' }); return peer.send({ type: 'ready', workspace: 'default', engine: 'chat', voice: 'echo', effect: null }); }
    if (message.type !== 'turn') return;
    seen.turns.push({ text: message.text ?? null, audio: !!message.audio, wake: message.wake === true });
    // A spoken turn after the question stands in for the user saying "stop".
    if (message.audio) { peer.send({ type: 'user', id: message.id, text: 'Stop.' }); return peer.send({ type: 'end', id: message.id }); }
    peer.send({ type: 'user', id: message.id, text: message.text });
    peer.send({ type: 'text', id: message.id, text: 'We decided to ship on Friday.' });
    peer.send({ type: 'done', id: message.id });
  });
  else socket.destroy();
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

let app;
try {
  // The user chose "Hey Echo" on this device earlier.
  await fs.writeFile(path.join(profile, 'wake.json'), JSON.stringify({ enabled: true, pausedUntil: 0, battery: 'listen' }));
  const env = { ...process.env, XDG_CONFIG_HOME: profile };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ executablePath: require('electron'), env,
    args: [path.join(root, 'main.js'), `--imprint-url=${origin}`, `--user-data-dir=${profile}`,
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${microphone}`,
      // macOS's audio service sandbox keeps the fake device from reading its file.
      '--disable-features=AudioServiceSandbox', ...process.platform === 'linux' ? ['--no-sandbox'] : []] });
  // The fake microphone needs no OS consent; answer for the OS so no system prompt opens.
  await app.evaluate(({ systemPreferences }) => {
    systemPreferences.getMediaAccessStatus = () => 'granted';
    systemPreferences.askForMediaAccess = async () => true;
  });
  const windows = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map(window => ({
    url: window.webContents.getURL(), visible: window.isVisible(), focused: window.isFocused(), bounds: window.getBounds(),
    pid: window.webContents.getOSProcessId() })));
  const windowAt = async route => (await windows()).find(window => new URL(window.url || 'about:blank').pathname === route);
  const page = async route => until(() => app.windows().find(window => window.url() !== 'about:blank' && new URL(window.url()).pathname === route), route);
  await until(async () => (await windows()).some(window => window.url.startsWith(origin) && window.visible), 'the dashboard');
  const dashboard = await page('/');
  // A page the fixture API can feed.
  await dashboard.goto(`${origin}/voice/`);
  await dashboard.waitForLoadState('load');
  await dashboard.evaluate(() => window.electronAPI.minimize());
  const orb = await page('/launcher/');
  await orb.locator('.imprint-launcher[data-ready=true]').waitFor();

  // The hidden listener runs, unseen, and the orb shows its ear.
  const ear = await until(windowAt.bind(null, '/wake/'), 'the listener window');
  assert.equal(ear.visible, false, 'the listener is never shown');
  await orb.locator('.orb-ear[data-state=listening]').waitFor();

  // "Hey Echo, what did we decide yesterday?" wakes Echo beside the orb, without focus.
  const listening = Date.now();
  const echo = await until(async () => { const w = await windowAt('/companion/'); return w?.visible && w; }, 'Echo to wake', 45000);
  console.log(`Echo showed ${((Date.now() - listening) / 1000).toFixed(1)} s after the listener was found (the speech starts 1 s into the recording)`);
  assert.equal(echo.focused, false, 'Echo did not take focus');
  await until(() => seen.turns.find(turn => turn.wake && turn.text), 'the question as Echo\'s first turn', 30000);
  const question = seen.turns.find(turn => turn.text);
  assert.ok(question.wake, 'the first turn belongs to the wake session');
  if (sentence.kind === 'say' && worker) assert.match(question.text, /decide/i, question.text);
  console.log('probes', seen.probes.length, 'question', JSON.stringify(question.text), 'worker probe CPU s', JSON.stringify(seen.cpu));

  // The conversation ends by itself ("stop", or quiet) and Echo goes back: it was closed before.
  await until(async () => !(await windowAt('/companion/')), 'Echo to go back', 45000);
  console.log('Echo heard', JSON.stringify(seen.turns), seen.turns.some(turn => turn.audio) ? '(ended when asked)' : '(ended after quiet)');
  await until(windowAt.bind(null, '/wake/'), 'listening to resume', 15000);
  await orb.locator('.orb-ear[data-state=listening]').waitFor();

  // The listener's cost in a quiet room (the recording loops after 40 s of quiet).
  const listener = await windowAt('/wake/');
  await app.evaluate(({ app: electronApp }) => electronApp.getAppMetrics());
  const samples = [];
  for (let i = 0; i < 10; i++) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    samples.push(await app.evaluate(({ app: electronApp }) => electronApp.getAppMetrics().map(metric => ({
      pid: metric.pid, type: metric.type, name: metric.serviceName || metric.name || '', cpu: metric.cpu.percentCPUUsage,
      memory: Math.round(metric.memory.workingSetSize / 1024) }))));
  }
  const mean = select => Math.round(100 * samples.reduce((sum, sample) => sum + sample.filter(select).reduce((s, m) => s + m.cpu, 0), 0) / samples.length) / 100;
  const renderer = mean(metric => metric.pid === listener.pid), audio = mean(metric => /audio/i.test(metric.name));
  const memory = samples.at(-1).find(metric => metric.pid === listener.pid)?.memory;
  console.log(`listener renderer ${renderer}% + audio service ${audio}% of one core over 20 s; renderer working set ${memory} MB`);
  if (worker) {
    const rss = spawnSync('ps', ['-o', 'rss=', '-p', String(worker.pid)]).stdout.toString().trim();
    console.log(`wake worker RSS ${Math.round(Number(rss) / 1024)} MB`);
  }
  console.log(`wake smoke passed (${sentence.kind} recording, ${worker ? 'real wake worker' : 'fixture wake answers'})`);
} finally {
  await app?.close().catch(() => {});
  worker?.kill();
  server.close();
  await fs.rm(profile, { recursive: true, force: true }).catch(() => {});
}
