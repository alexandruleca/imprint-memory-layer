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
  exports: exportsObject, require: () => ({ API_BASE: '' }), Float32Array, Uint8Array, ArrayBuffer, DataView, btoa,
});
const { VoiceGate, encodeWav } = exportsObject;
const frame = (volume = 0) => Float32Array.from({ length: 960 }, (_, i) => Math.sin(i * .15) * volume);

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
