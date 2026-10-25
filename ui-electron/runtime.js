'use strict';

const path = require('node:path');

// Releases put the CLI beside the desktop app: bin/imprint next to
// bin/imprint-ui.app, bin/imprint-ui.exe or bin/imprint-ui. Portable Windows
// and AppImage builds run from a temporary copy and name the original file.
function installedCLI({ platform, execPath, env }) {
  const paths = platform === 'win32' ? path.win32 : path.posix;
  const app = env.PORTABLE_EXECUTABLE_FILE || env.APPIMAGE
    || (platform === 'darwin' ? paths.resolve(execPath, '..', '..', '..') : execPath);
  return paths.join(paths.dirname(app), platform === 'win32' ? 'imprint.exe' : 'imprint');
}

// `imprint ui open` and the installed shortcuts name the runtime. When the OS
// starts the app itself (Dock, Reopen after a crash, a pinned taskbar icon),
// the app asks its CLI, which owns these paths (`imprint ui runtime`).
function desktopRuntime(named, { platform, execPath, env, execFile }) {
  if (named.projectDir && named.dataDir && named.python) return Promise.resolve(named);
  const cli = installedCLI({ platform, execPath, env });
  const failed = reason => new Error(`Imprint could not locate its runtime (${reason}). `
    + 'Open Imprint with its installed shortcut or “imprint ui open”.');
  return new Promise((resolve, reject) => {
    execFile(cli, ['ui', 'runtime'], { timeout: 15000, windowsHide: true }, (error, stdout) => {
      if (error) { reject(failed(`${cli}: ${error.message}`)); return; }
      let printed;
      try { printed = JSON.parse(stdout); } catch { reject(failed(`${cli} printed no runtime`)); return; }
      const runtime = { projectDir: printed?.project_dir, dataDir: printed?.data_dir, python: printed?.python };
      if (Object.values(runtime).every(value => typeof value === 'string' && value)) resolve(runtime);
      else reject(failed(`${cli} printed an incomplete runtime`));
    });
  });
}

module.exports = { installedCLI, desktopRuntime };
