'use strict';

const fs = require('node:fs');
const path = require('node:path');

// The job holding Imprint's queue reports how far it is in
// <data dir>/progress.json (ADR 0089). The desktop app reads it directly:
// setup restarts the dashboard server while it replaces the runtime, and the
// server's launchers wait for setup before starting it again.
function readActivity(dataDir, { readFile = fs.readFileSync, alive = processAlive } = {}) {
  if (!dataDir) return null;
  try {
    const record = JSON.parse(readFile(path.join(dataDir, 'progress.json'), 'utf8'));
    return Number.isInteger(record?.pid) && record.pid > 0 && typeof record.command === 'string' && alive(record.pid)
      ? record : null;
  } catch {
    return null;
  }
}

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM'; // It runs, as another user.
  }
}

// "setup: installing llama.cpp (1 of 3 steps)"
function describeActivity(record) {
  const counts = record.total > 0 ? ` (${record.processed} of ${record.total} ${record.unit || 'files'})` : '';
  return `${record.command}${record.phase ? `: ${record.phase}` : ''}${counts}`;
}

module.exports = { readActivity, describeActivity };
