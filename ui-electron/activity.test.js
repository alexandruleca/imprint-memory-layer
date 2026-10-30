'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { readActivity, describeActivity } = require('./activity');

const setup = { pid: 4242, command: 'setup', phase: 'installing llama.cpp', processed: 1, total: 3, unit: 'steps' };
const files = record => () => JSON.stringify(record);

test('the running job is read while its process lives', () => {
  assert.deepEqual(readActivity('/data', { readFile: files(setup), alive: pid => pid === 4242 }), setup);
  assert.equal(readActivity('/data', { readFile: files(setup), alive: () => false }), null);
});

test('no data directory, no record or a damaged one is no activity', () => {
  assert.equal(readActivity('', { readFile: files(setup), alive: () => true }), null);
  assert.equal(readActivity('/data', { readFile: () => { throw Object.assign(new Error('gone'), { code: 'ENOENT' }); } }), null);
  assert.equal(readActivity('/data', { readFile: () => '{"pid": "x"', alive: () => true }), null);
  assert.equal(readActivity('/data', { readFile: files({ ...setup, pid: 0 }), alive: () => true }), null);
});

test('a description says the step and, when known, how far', () => {
  assert.equal(describeActivity(setup), 'setup: installing llama.cpp (1 of 3 steps, 33%)');
  assert.equal(describeActivity({ command: 'retag', phase: 'llm_tagging', processed: 340, total: 1200, unit: 'memories' }),
    'retag: llm tagging (340 of 1,200 memories, 28%)');
  assert.equal(describeActivity({ command: 'learn', phase: 'extracting', processed: 0, total: 0, unit: 'chunks' }), 'learn: extracting');
});
