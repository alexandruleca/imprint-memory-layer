'use strict';
// "Hey Echo" (ADR 0137). What this desktop profile chose, kept in wake.json in
// the app's user-data folder (never a setting, a memory or synced data), and
// when the hidden listener window should run. The main process derives the
// listener and every indicator from these, so a live microphone always shows.
const WAKE_FILE = 'wake.json';
const HOUR = 60 * 60 * 1000;

/** The stored choice; anything unexpected means off. */
function wakeIntent(value) {
  return {
    enabled: value?.enabled === true,
    pausedUntil: Number.isSafeInteger(value?.pausedUntil) && value.pausedUntil > 0 ? value.pausedUntil : 0,
    battery: value?.battery === 'pause' ? 'pause' : 'listen',
  };
}
function readWakeIntent(read) {
  try { return wakeIntent(JSON.parse(read())); } catch { return wakeIntent(null); }
}
/** A change a voice page asks for: turning on (with the battery choice) or off. */
function wakeChoice(value) {
  if (!value || typeof value !== 'object' || typeof value.enabled !== 'boolean') return null;
  if (value.battery !== undefined && !['listen', 'pause'].includes(value.battery)) return null;
  return { enabled: value.enabled, ...value.battery ? { battery: value.battery } : {} };
}

/** Why the listener does not run now, or '' when it should. */
function pauseReason(s) {
  if (!s.intent.enabled) return 'off';
  if (s.blocked) return 'blocked';
  if (s.phase !== 'ready') return 'starting';
  if (s.intent.pausedUntil > s.now) return 'paused';
  if (s.recording) return 'recording';
  if (s.conversation) return 'conversation';
  if (s.locked) return 'locked';
  if (s.asleep) return 'asleep';
  if (s.onBattery && s.intent.battery === 'pause') return 'battery';
  if (s.thermal === 'critical') return 'hot';
  if (s.retryAt > s.now) return 'retrying';
  return '';
}

const PAUSED = {
  blocked: 'can’t use the microphone', starting: 'starting', recording: 'paused while recording',
  conversation: 'Echo is listening', locked: 'paused while locked', asleep: 'paused while asleep',
  battery: 'paused on battery', hot: 'paused while the computer is hot', retrying: 'reconnecting',
};
function clockTime(ms) {
  const date = new Date(ms);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
/** The tray's and the orb's words for the state; '' while it is off. */
function wakeLabel(view) {
  if (!view.enabled) return '';
  if (view.listening) return 'Listening for “Hey Echo”';
  if (view.reason === 'paused') return `“Hey Echo” paused until ${clockTime(view.pausedUntil)}`;
  return `“Hey Echo” ${PAUSED[view.reason] || 'paused'}`;
}

module.exports = { WAKE_FILE, HOUR, wakeIntent, readWakeIntent, wakeChoice, pauseReason, wakeLabel };
