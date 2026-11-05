'use strict';

// The floating launcher starts and stops meeting recordings that the dashboard
// window records (ADR 0131). Only these checked values cross between the two
// renderers through the main process.

const PHASES = new Set(['idle', 'starting', 'recording', 'stopping']);
const IDLE = Object.freeze({ phase: 'idle', id: '', title: '', project: '', startedAt: 0, others: false, error: '' });

function text(value, limit) {
  return typeof value === 'string' ? value.slice(0, limit) : '';
}

// What the dashboard reports about its recorder.
function recorderState(value) {
  if (!value || typeof value !== 'object' || !PHASES.has(value.phase)) return null;
  const startedAt = Number.isFinite(value.startedAt) && value.startedAt > 0 ? value.startedAt : 0;
  return { phase: value.phase, id: text(value.id, 64), title: text(value.title, 200), project: text(value.project, 256),
    startedAt, others: value.others === true, error: text(value.error, 500) };
}

// A start the launcher asks for. The user must have confirmed that everyone
// knows the call is recorded; the API refuses a start without it as well.
function recordingStart(value) {
  if (!value || typeof value !== 'object' || value.consent !== true) return null;
  if (typeof value.project !== 'string' || !value.project.trim() || value.project.length > 256) return null;
  if (value.title !== undefined && (typeof value.title !== 'string' || value.title.length > 200)) return null;
  return { title: value.title?.trim() || '', project: value.project.trim(), others: value.others === true, consent: true };
}

// Microphone and computer loudness, each 0 to 1.
function recorderLevels(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  return value.every(level => Number.isFinite(level)) ? value.map(level => Math.max(0, Math.min(1, level))) : null;
}

module.exports = { IDLE, recorderState, recordingStart, recorderLevels };
