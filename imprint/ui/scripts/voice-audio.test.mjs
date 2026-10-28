// Run the actual TypeScript audio controller without a browser or a model.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, '../src/lib/voice-client.ts'), 'utf8');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, {
  // This realm's Error, so errors made here pass the controller's instanceof checks.
  exports: exportsObject, require: () => ({ API_BASE: '' }), Float32Array, Uint8Array, ArrayBuffer, DataView, btoa, atob, Error,
  WebSocket: { OPEN: 1 },
});
const { VoiceGate, encodeWav } = exportsObject;
const frame = (volume = 0) => Float32Array.from({ length: 960 }, (_, i) => Math.sin(i * .15) * volume);

test('long replies report completed playback and acknowledge history only after the final audio', () => {
  const client = new exportsObject.VoiceClient(() => {}), controls = [], scheduled = [];
  client.context = {
    currentTime: 0, destination: {},
    createBuffer: (_channels, samples, rate) => ({ duration: samples / rate, getChannelData: () => new Float32Array(samples) }),
    createAnalyser: () => ({ connect() {}, getFloatTimeDomainData(values) { values.fill(0); } }),
    createBufferSource: () => {
      const source = { connect() {}, disconnect() {}, start(at) { this.end = at + this.buffer.duration; }, stop() {} };
      scheduled.push(source);
      return source;
    },
  };
  client.socket = { readyState: 1, send: data => controls.push(JSON.parse(data)) };
  client.snapshot.connected = true;
  client.accepted = 1;
  const data = Buffer.alloc(24000 * 2 * 2).toString('base64');
  for (let i = 0; i < 40; i++) {
    client.receive({ type: 'audio', id: 1, data });
    if (i === 39) client.receive({ type: 'done', id: 1 });
    client.tick();
    assert.equal(controls.filter(c => c.type === 'played').length, 0);
    const source = scheduled.at(-1);
    client.context.currentTime = source.end;
    source.onended();
  }
  client.tick();
  assert.equal(controls.filter(c => c.type === 'playback').length, 40);
  assert.equal(controls.filter(c => c.type === 'playback').at(-1).samples, 80 * 24000);
  assert.deepEqual(controls.filter(c => c.type === 'played'), [{ type: 'played', id: 1 }]);
  assert.equal(controls.filter(c => c.type === 'cancel').length, 0);

  client.accepted = 2;
  client.receive({ type: 'audio', id: 2, data });
  const late = scheduled.at(-1).onended;
  client.interrupt();
  const count = controls.length;
  late(); // A callback already queued when interruption happened.
  assert.equal(controls.length, count);
});

test('VAD rejects silence and a short click; speech retains pre-roll and ends after silence', () => {
  const gate = new VoiceGate();
  for (let i = 0; i < 30; i++) assert.equal(gate.feed(frame(.003), false, .016).began, false);
  assert.equal(gate.feed(frame(.4), false, .016).began, false);
  gate.feed(frame(), false, .016);
  let began = false;
  for (let i = 0; i < 8; i++) began ||= gate.feed(frame(.08), false, .016).began;
  assert.ok(began);
  let result;
  for (let i = 0; i < 30; i++) result = gate.feed(frame(), false, .016);
  assert.ok(result.complete.length >= 40);
  assert.equal(gate.active, false);
});
test('speaking interruption requires sustained onset; buffer stops before 30 seconds', () => {
  const gate = new VoiceGate();
  for (let i = 0; i < 6; i++) assert.equal(gate.feed(frame(.1), true, .016).began, false);
  assert.equal(gate.feed(frame(.1), true, .016).began, true);
  let result;
  for (let i = 0; i < 1500 && !result?.complete; i++) result = gate.feed(frame(.1), true, .016);
  assert.ok(result.complete);
  assert.ok(result.complete.length <= 1490);
});
test('microphone WAV is mono PCM16 at 24 kHz and clamps input samples', () => {
  const data = Buffer.from(encodeWav([new Float32Array(4800).fill(2)], 48000), 'base64');
  assert.equal(data.toString('ascii', 0, 4), 'RIFF');
  assert.equal(data.readUInt16LE(20), 1);
  assert.equal(data.readUInt16LE(22), 1);
  assert.equal(data.readUInt32LE(24), 24000);
  assert.equal(data.readUInt16LE(34), 16);
  assert.equal(data.readUInt32LE(40), 4800);
  assert.equal(data.readInt16LE(44), 32767);
  assert.throws(() => encodeWav([new Float32Array(48000 * 31)], 48000), /30 seconds/);
});

test('voice preferences keep only supported choices and recover from broken browser storage', () => {
  const source = fs.readFileSync(path.join(here, '../src/lib/voice-preferences.ts'), 'utf8');
  const preferences = {};
  let stored = '{"engine":"lfm","mode":"handsfree","microphoneEnabled":true,"unrelated":"ignored"}';
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, {
    exports: preferences, localStorage: { getItem: () => stored, setItem: (_key, value) => { stored = value; } },
  });
  const read = () => ({ ...preferences.readVoicePreferences() });
  assert.deepEqual(read(), { engine: 'lfm', mode: 'handsfree', microphoneEnabled: true });
  preferences.saveVoicePreferences({ mode: 'ptt' });
  assert.deepEqual(JSON.parse(stored), { engine: 'lfm', mode: 'ptt', microphoneEnabled: true });
  stored = '{broken';
  assert.deepEqual(read(), { engine: 'chat', mode: 'ptt', microphoneEnabled: false });
  stored = '{"engine":"remote","mode":"always","microphoneEnabled":"true"}';
  assert.deepEqual(read(), { engine: 'chat', mode: 'ptt', microphoneEnabled: false });
});

test('a microphone the OS refused names its recovery; restricted and unknown answers are not refusals', () => {
  const { microphoneProblem, microphoneAccessAllows, microphoneSettingsHelp, MicrophoneAccessError } = exportsObject;
  assert.equal(microphoneAccessAllows('granted', 'darwin'), true);
  assert.equal(microphoneAccessAllows('unknown', 'darwin'), false);
  assert.equal(microphoneAccessAllows('unknown', 'win32'), true);
  assert.equal(microphoneAccessAllows('not-determined', 'win32'), true);
  assert.equal(microphoneAccessAllows('unmanaged', 'linux'), true);
  for (const status of ['denied', 'restricted', 'error']) assert.equal(microphoneAccessAllows(status, 'win32'), false);
  const denied = new MicrophoneAccessError('denied');
  assert.match(microphoneProblem('permission', denied, 'darwin'), /quit and reopen Imprint/);
  assert.equal(microphoneSettingsHelp('permission', denied, 'win32'), true);
  assert.equal(microphoneSettingsHelp('permission', denied, 'linux'), false);
  assert.match(microphoneProblem('permission', new MicrophoneAccessError('restricted'), 'win32'), /system policy/);
  assert.equal(microphoneSettingsHelp('permission', new MicrophoneAccessError('restricted'), 'win32'), false);
  assert.match(microphoneProblem('device', Object.assign(new Error('x'), { name: 'NotSupportedError' })), /127\.0\.0\.1/);
});

test("Echo's robot effect stays bounded, keeps every sample and is continuous across streamed chunks", () => {
  const { robotVoice } = exportsObject;
  const input = Float32Array.from({ length: 3000 }, (_, i) => Math.sin(i * .2) * .9);
  const full = input.slice(); robotVoice(full);
  assert.notDeepEqual(full, input);
  assert.equal(full.length, input.length);
  assert.ok(full.every(value => Number.isFinite(value) && Math.abs(value) <= 1));
  const split = input.slice(); robotVoice(split.subarray(0, 1403)); robotVoice(split.subarray(1403), 1403);
  assert.deepEqual(split, full);
});
