'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

// The service exits with this status when asked to restart (`imprint ui
// restart`, or `imprint update` once a release is installed). Starting it again
// serves the installed code under the same owner (ADR 0050). EX_TEMPFAIL.
const RESTART_EXIT_CODE = 75;

class DesktopService {
  constructor({ python, projectDir, dataDir, port, spawnProcess = spawn }) {
    Object.assign(this, { python, projectDir, dataDir, port, spawnProcess });
    this.logPath = path.join(dataDir, 'imprint_ui.log');
    this.proc = null;
    this.failure = null;
    this.stopping = false;
  }

  start(onFailure) {
    fs.mkdirSync(this.dataDir, { recursive: true });
    const log = fs.openSync(this.logPath, 'a', 0o600);
    try {
      fs.writeSync(log, `\n--- Imprint desktop start @ ${new Date().toISOString()} ---\n`);
      this.proc = this.spawnProcess(
        this.python,
        ['-m', 'imprint.desktop_server', '--port', String(this.port)],
        {
          cwd: this.projectDir,
          env: { ...process.env, PYTHONPATH: this.projectDir, IMPRINT_DATA_DIR: this.dataDir },
          windowsHide: true,
          detached: false,
          // EOF is the cross-platform shutdown signal. Keep the pipe open
          // for the app's lifetime, and retain diagnostics outside a console.
          stdio: ['pipe', log, log],
        },
      );
    } finally {
      fs.closeSync(log);
    }
    const failed = message => {
      if (this.stopping || this.failure) return;
      this.failure = message;
      onFailure(message);
    };
    // Closing an already-exited child's pipe can raise EPIPE.
    this.proc.stdin.on('error', () => {});
    this.closed = new Promise(resolve => {
      this.proc.once('error', err => { failed(err.message); resolve(); });
      this.proc.once('exit', (code, signal) => {
        if (code !== RESTART_EXIT_CODE || this.stopping) failed(`Local service stopped (${signal || `exit ${code}`}).`);
        else {
          try { this.start(onFailure); } catch (err) { failed(err.message); }
        }
        resolve();
      });
    });
  }

  async stop() {
    if (!this.proc) return;
    this.stopping = true;
    this.proc.stdin.end();
    let timer;
    await Promise.race([
      this.closed,
      new Promise(resolve => {
        timer = setTimeout(() => {
          // The Python parent watcher normally enforces its own 10s bound.
          // Avoid process-tree kills: Qdrant may serve other Imprint clients.
          this.proc.kill();
          resolve();
        }, 12000);
      }),
    ]);
    clearTimeout(timer);
  }
}

module.exports = { DesktopService, RESTART_EXIT_CODE };
