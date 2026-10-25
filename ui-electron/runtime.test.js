'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { installedCLI, desktopRuntime } = require('./runtime');

test('the CLI is found beside each release format of the desktop app', () => {
  assert.equal(installedCLI({ platform: 'darwin', env: {},
    execPath: '/Applications/Imprint.app/Contents/Resources/imprint/bin/imprint-ui.app/Contents/MacOS/Imprint' }),
  '/Applications/Imprint.app/Contents/Resources/imprint/bin/imprint');
  // Portable Windows and AppImage builds run from a temporary copy.
  assert.equal(installedCLI({ platform: 'win32', execPath: 'C:\\Users\\a\\AppData\\Local\\Temp\\3L9g\\Imprint.exe',
    env: { PORTABLE_EXECUTABLE_FILE: 'C:\\Users\\a\\AppData\\Local\\Programs\\Imprint\\bin\\imprint-ui.exe' } }),
  'C:\\Users\\a\\AppData\\Local\\Programs\\Imprint\\bin\\imprint.exe');
  assert.equal(installedCLI({ platform: 'linux', execPath: '/tmp/.mount_imprintX/imprint-ui',
    env: { APPIMAGE: '/home/a/.local/share/imprint/bin/imprint-ui' } }), '/home/a/.local/share/imprint/bin/imprint');
});

function cli(stdout, error = null) {
  const calls = [];
  const execFile = (file, args, options, callback) => { calls.push({ file, args, options }); queueMicrotask(() => callback(error, stdout)); };
  return { calls, execFile };
}
const darwin = { platform: 'darwin', env: {}, execPath: '/opt/imprint/bin/imprint-ui.app/Contents/MacOS/Imprint' };

test('a runtime named by the launcher is used without asking the CLI', async () => {
  const fake = cli('');
  const named = { projectDir: '/source', dataDir: '/data', python: '/venv/python' };
  assert.deepEqual(await desktopRuntime(named, { ...darwin, execFile: fake.execFile }), named);
  assert.equal(fake.calls.length, 0);
});

test('without launcher arguments the app asks its CLI for the runtime', async () => {
  const fake = cli(JSON.stringify({ project_dir: '/opt/imprint', data_dir: '/home/a/.local/share/imprint/data',
    python: '/home/a/.local/share/imprint/.venv/bin/python' }) + '\n');
  const runtime = await desktopRuntime({ projectDir: null, dataDir: null, python: null }, { ...darwin, execFile: fake.execFile });
  assert.deepEqual(runtime, { projectDir: '/opt/imprint', dataDir: '/home/a/.local/share/imprint/data',
    python: '/home/a/.local/share/imprint/.venv/bin/python' });
  assert.deepEqual(fake.calls.map(call => [call.file, call.args]), [['/opt/imprint/bin/imprint', ['ui', 'runtime']]]);
  assert.equal(fake.calls[0].options.windowsHide, true);
  // A partial set of arguments is not a runtime; the CLI answers for all three.
  const partial = cli(JSON.stringify({ project_dir: '/p', data_dir: '/d', python: '/py' }));
  assert.equal((await desktopRuntime({ projectDir: '/x', dataDir: null, python: null }, { ...darwin, execFile: partial.execFile })).projectDir, '/p');
});

test('a missing or broken CLI explains that the runtime could not be located', async () => {
  const none = { projectDir: null, dataDir: null, python: null };
  await assert.rejects(desktopRuntime(none, { ...darwin, execFile: cli('', Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' })).execFile }),
    /could not locate its runtime \(\/opt\/imprint\/bin\/imprint: spawn ENOENT\)/);
  await assert.rejects(desktopRuntime(none, { ...darwin, execFile: cli('usage: imprint ...').execFile }), /printed no runtime/);
  await assert.rejects(desktopRuntime(none, { ...darwin, execFile: cli('{"project_dir":"/p"}').execFile }), /incomplete runtime/);
});
