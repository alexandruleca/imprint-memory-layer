'use strict';
function voicePage(value, origin) {
  try {
    const url = new URL(value);
    return url.origin === origin && ['/voice', '/companion'].includes(url.pathname.replace(/\/$/, ''));
  } catch { return false; }
}
function audioPermission(contents, permission, details, origin, windows) {
  if (!contents || permission !== 'media' || !windows.some(window => window?.webContents === contents)
      || !voicePage(contents.getURL(), origin) || details?.isMainFrame === false) return false;
  if (details?.requestingUrl && !voicePage(details.requestingUrl, origin)) return false;
  if (details?.mediaTypes) return details.mediaTypes.length === 1 && details.mediaTypes[0] === 'audio';
  return details?.mediaType === 'audio';
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
module.exports = { voicePage, audioPermission, clipboardWritePermission, macMicrophoneConsent,
  requestMicrophoneAccess, microphoneSettingsURL };
