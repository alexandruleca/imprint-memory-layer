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

// "retag: llm tagging (340 of 1,200 memories, 28%)"
function describeActivity(record) {
  const n = value => Number(value || 0).toLocaleString('en-US');
  const percent = record.total > 0 ? Math.floor((100 * Math.min(record.processed, record.total)) / record.total) : null;
  const counts = percent === null ? '' : ` (${n(record.processed)} of ${n(record.total)} ${record.unit || 'files'}, ${percent}%)`;
  return `${record.command}${record.phase ? `: ${record.phase.replace(/_/g, ' ')}` : ''}${counts}`;
}

module.exports = { readActivity, describeActivity };
