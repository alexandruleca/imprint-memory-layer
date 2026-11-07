'use strict';
// Pages that may use the microphone: Echo's, and meeting recordings (ADR 0131).
const AUDIO_PAGES = ['/voice', '/companion', '/recordings'];
function onPage(value, origin, pages) {
  try {
    const url = new URL(value);
    return url.origin === origin && pages.includes(url.pathname.replace(/\/$/, ''));
  } catch { return false; }
}
function voicePage(value, origin) {
  return onPage(value, origin, AUDIO_PAGES);
}
// The hidden "Hey Echo" listener (ADR 0137) is the one window whose page is
// /wake; that page gets the microphone only in that window.
function listenerPage(value, origin) {
  return onPage(value, origin, ['/wake']);
}
function audioPermission(contents, permission, details, origin, windows, listener = null) {
  const listening = !!contents && !!listener && listener.webContents === contents;
  const page = listening ? listenerPage : voicePage;
  if (!contents || permission !== 'media' || (!listening && !windows.some(window => window?.webContents === contents))
      || !page(contents.getURL(), origin) || details?.isMainFrame === false) return false;
  if (details?.requestingUrl && !page(details.requestingUrl, origin)) return false;
  if (details?.mediaTypes) return details.mediaTypes.length === 1 && details.mediaTypes[0] === 'audio';
  return details?.mediaType === 'audio';
}
// Recording a meeting takes what this computer plays (ADR 0131): the
// dashboard window's Recordings page asks for a screen capture with its audio,
// and gets the system's audio loopback. Electron asks the session's permission
// handler first, as "display-capture"; this is the one page it may grant.
function displayCapturePermission(contents, permission, details, origin, dashboard) {
  if (permission !== 'display-capture' || !contents || !dashboard || dashboard.webContents !== contents
      || !onPage(contents.getURL(), origin, ['/recordings']) || details?.isMainFrame === false) return false;
  if (details?.requestingUrl && !onPage(details.requestingUrl, origin, ['/recordings'])) return false;
  return !details?.mediaTypes || details.mediaTypes.includes('audio');
}
function systemAudioCapture(request, origin, dashboard) {
  const frame = request?.frame;
  return !!frame && !!dashboard && frame === dashboard.webContents?.mainFrame
    && onPage(frame.url, origin, ['/recordings']) && request.audioRequested === true;
}
// What answers that request: the system's audio, and as the video, which the
// page stops at once, the dashboard page itself. Capturing the page needs no
// screen-recording permission on macOS and no screen picker on Linux.
function systemAudioStreams(request, origin, dashboard) {
  return systemAudioCapture(request, origin, dashboard) ? { video: request.frame, audio: 'loopback' } : null;
}
function clipboardWritePermission(contents, permission, details, origin, windows) {
  if (permission !== 'clipboard-sanitized-write' || !contents
      || !windows.some(window => window?.webContents === contents) || details?.isMainFrame === false) return false;
  try {
    return new URL(contents.getURL()).origin === origin
      && (!details?.requestingUrl || new URL(details.requestingUrl).origin === origin);
  } catch { return false; }
}
// The OS consent check behind Enable microphone (ADR 0069). macOS asks only
// while consent is undetermined; Windows reports its desktop privacy setting;
// Linux has no OS consent API, so capture itself decides there. A failed query
// is an error, never an invented grant.
async function requestMicrophoneAccess(platform, preferences) {
  if (!['darwin', 'win32'].includes(platform)) return { status: 'unmanaged' };
  try {
    const status = preferences.getMediaAccessStatus('microphone');
    if (platform === 'darwin' && status === 'not-determined') {
      return { status: await preferences.askForMediaAccess('microphone') ? 'granted' : 'denied' };
    }
    return { status };
  } catch { return { status: 'error' }; }
}
async function macMicrophoneConsent(preferences) {
  return (await requestMicrophoneAccess('darwin', preferences)).status === 'granted';
}
function microphoneSettingsURL(platform) {
  return platform === 'darwin' ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone'
    : platform === 'win32' ? 'ms-settings:privacy-microphone' : null;
}
module.exports = { voicePage, listenerPage, audioPermission, displayCapturePermission, systemAudioCapture, systemAudioStreams,
  clipboardWritePermission, macMicrophoneConsent, requestMicrophoneAccess, microphoneSettingsURL };
