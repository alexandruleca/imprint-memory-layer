'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { wakeIntent, readWakeIntent, wakeChoice, pauseReason, wakeLabel } = require('./wake');

test('the stored choice keeps only known values and is off when unreadable', () => {
  assert.deepEqual(readWakeIntent(() => '{"enabled":true,"pausedUntil":1700000000000,"battery":"pause"}'),
    { enabled: true, pausedUntil: 1700000000000, battery: 'pause' });
  for (const text of ['', '{', 'null', '{"enabled":"yes","pausedUntil":-4,"battery":"maybe"}', '{"x":1,"y":2}']) {
    assert.deepEqual(readWakeIntent(() => text), { enabled: false, pausedUntil: 0, battery: 'listen' }, text);
  }
  assert.deepEqual(readWakeIntent(() => { throw new Error('missing'); }), wakeIntent(null));
  assert.deepEqual(wakeChoice({ enabled: true, battery: 'pause' }), { enabled: true, battery: 'pause' });
  assert.deepEqual(wakeChoice({ enabled: false }), { enabled: false });
  for (const bad of [null, 'on', {}, { enabled: 1 }, { enabled: true, battery: 'eco' }]) assert.equal(wakeChoice(bad), null);
});

test('the listener runs only while chosen, ready and nothing else holds the microphone or the moment', () => {
  const base = { intent: { enabled: true, pausedUntil: 0, battery: 'listen' }, blocked: false, phase: 'ready', now: 1000,
    recording: false, conversation: false, locked: false, asleep: false, onBattery: false, thermal: 'nominal', retryAt: 0 };
  assert.equal(pauseReason(base), '');
  const cases = [
    [{ intent: { ...base.intent, enabled: false } }, 'off'], [{ blocked: true }, 'blocked'], [{ phase: 'loading' }, 'starting'],
    [{ intent: { ...base.intent, pausedUntil: 5000 } }, 'paused'], [{ recording: true }, 'recording'],
    [{ conversation: true }, 'conversation'], [{ locked: true }, 'locked'], [{ asleep: true }, 'asleep'],
    [{ thermal: 'critical' }, 'hot'], [{ retryAt: 2000 }, 'retrying'],
  ];
  for (const [change, reason] of cases) assert.equal(pauseReason({ ...base, ...change }), reason, reason);
  // On battery it keeps listening unless the user chose to pause there.
  assert.equal(pauseReason({ ...base, onBattery: true }), '');
  assert.equal(pauseReason({ ...base, onBattery: true, intent: { ...base.intent, battery: 'pause' } }), 'battery');
  assert.equal(pauseReason({ ...base, intent: { ...base.intent, pausedUntil: 900 } }), '', 'an ended pause');
});

test('the tray and the orb say whether Imprint listens', () => {
  assert.equal(wakeLabel({ enabled: false, listening: false }), '');
  assert.equal(wakeLabel({ enabled: true, listening: true, reason: 'conversation' }), 'Listening for “Hey Echo”');
  assert.equal(wakeLabel({ enabled: true, listening: false, reason: 'recording' }), '“Hey Echo” paused while recording');
  assert.match(wakeLabel({ enabled: true, listening: false, reason: 'paused', pausedUntil: new Date(2026, 10, 5, 15, 40).getTime() }),
    /paused until 15:40/);
});
