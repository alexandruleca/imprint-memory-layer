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
module.exports = { voicePage, audioPermission, clipboardWritePermission };
