'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DesktopService } = require('./service');

function fixture(t, spawnProcess) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'imprint desktop '));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return new DesktopService({
    python: '/runtime with spaces/python', projectDir: root,
    dataDir: path.join(root, 'data'), port: 18420, spawnProcess,
  });
}

function child() {
  const proc = new EventEmitter();
  proc.stdin = new PassThrough();
  proc.kill = () => assert.fail('graceful shutdown should not kill a process');
  return proc;
}

test('service is hidden, retains logs and receives resolved paths without a shell', async t => {
  const proc = child();
  const service = fixture(t, (python, args, options) => {
    assert.equal(python, '/runtime with spaces/python');
    assert.deepEqual(args, ['-m', 'imprint.desktop_server', '--port', '18420']);
    assert.equal(options.windowsHide, true);
    assert.equal(options.detached, false);
    assert.equal(options.shell, undefined);
    assert.equal(options.cwd, options.env.PYTHONPATH);
    assert.equal(options.env.IMPRINT_DATA_DIR, service.dataDir);
    assert.equal(options.stdio[0], 'pipe');
    fs.writeSync(options.stdio[1], 'startup diagnostics\n');
    return proc;
  });
  service.start(() => assert.fail('unexpected failure'));
  assert.match(fs.readFileSync(service.logPath, 'utf8'), /startup diagnostics/);
  const stopped = service.stop();
  proc.emit('exit', 0);
  await stopped;
});

test('explicit quit closes the lifetime pipe and waits for service exit', async t => {
  const proc = child();
  const service = fixture(t, () => proc);
  service.start(() => assert.fail('quit should not report a startup failure'));
  const stopped = service.stop();
  assert.equal(proc.stdin.writableEnded, true);
  let complete = false;
  stopped.then(() => { complete = true; });
  await Promise.resolve();
  assert.equal(complete, false);
  proc.emit('exit', 0);
  await stopped;
});

test('spawn and runtime failures become actionable errors', async t => {
  for (const event of ['error', 'exit']) {
    const proc = child();
    const service = fixture(t, () => proc);
    const failures = [];
    service.start(message => failures.push(message));
    if (event === 'error') proc.emit('error', new Error('Python was removed'));
    else proc.emit('exit', 1);
    assert.equal(failures.length, 1);
    assert.match(failures[0], event === 'error' ? /Python was removed/ : /exit 1/);
    await service.stop();
  }
});
