'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { voicePage, listenerPage, audioPermission, displayCapturePermission, systemAudioCapture, systemAudioStreams,
  clipboardWritePermission, macMicrophoneConsent, requestMicrophoneAccess, microphoneSettingsURL } = require('./voice-permissions');
test('microphone permission is audio-only, main-frame-only, current app voice surface only', () => {
  const origin = 'http://127.0.0.1:8420';
  const contents = { getURL: () => `${origin}/voice/` };
  const windows = [{ webContents: contents }];
  const check = (permission, details, sender = contents) => audioPermission(sender, permission, details, origin, windows);
  assert.ok(check('media', { mediaTypes: ['audio'], isMainFrame: true, requestingUrl: `${origin}/voice/` }));
  assert.ok(check('media', { mediaType: 'audio' }));
  assert.equal(check('media', { mediaTypes: ['audio', 'video'] }), false);
  assert.equal(check('media', { mediaType: 'video' }), false);
  assert.equal(check('media', { mediaType: 'audio', isMainFrame: false }), false);
  assert.equal(check('media', { mediaType: 'audio', requestingUrl: 'https://evil.example/voice/' }), false);
  assert.equal(check('media', { mediaType: 'audio' }, { getURL: contents.getURL }), false);
  assert.equal(check('geolocation', {}), false);
  assert.equal(check('media', {}), false);
  contents.getURL = () => `${origin}/chat/`;
  assert.equal(check('media', { mediaType: 'audio' }), false);
  assert.equal(voicePage('http://127.0.0.1:7777/companion/', origin), false);
  // Meeting recordings use the microphone too (ADR 0131).
  contents.getURL = () => `${origin}/recordings/`;
  assert.ok(check('media', { mediaType: 'audio', requestingUrl: `${origin}/recordings/` }));
});
test('only the dashboard window\'s Recordings page gets the system audio', () => {
  const origin = 'http://127.0.0.1:8420';
  const mainFrame = { url: `${origin}/recordings/` };
  const dashboard = { webContents: { mainFrame } };
  const request = (frame, audioRequested = true) => ({ frame, audioRequested, videoRequested: true });
  assert.equal(systemAudioCapture(request(mainFrame), origin, dashboard), true);
  assert.equal(systemAudioCapture(request(mainFrame, false), origin, dashboard), false);
  assert.equal(systemAudioCapture(request({ url: `${origin}/recordings/` }), origin, dashboard), false);  // another frame
  assert.equal(systemAudioCapture(request({ ...mainFrame }), origin, { webContents: { mainFrame: {} } }), false);
  mainFrame.url = `${origin}/voice/`;
  assert.equal(systemAudioCapture(request(mainFrame), origin, dashboard), false);
  mainFrame.url = 'https://evil.example/recordings/';
  assert.equal(systemAudioCapture(request(mainFrame), origin, dashboard), false);
  assert.equal(systemAudioCapture(request(null), origin, dashboard), false);
  assert.equal(systemAudioCapture(request(mainFrame), origin, null), false);
});
test('the Recordings page may ask for a screen capture, which brings the system audio and its own page, nothing else', () => {
  const origin = 'http://127.0.0.1:8420';
  const contents = { getURL: () => `${origin}/recordings/` };
  const dashboard = { webContents: contents };
  const details = { isMainFrame: true, mediaTypes: ['audio', 'video'], requestingUrl: `${origin}/recordings/` };
  const check = (permission, extra = {}, sender = contents) =>
    displayCapturePermission(sender, permission, { ...details, ...extra }, origin, dashboard);
  // What Electron 45 sends for getDisplayMedia({ video: true, audio: true }).
  assert.equal(check('display-capture'), true);
  assert.equal(check('display-capture', { mediaTypes: undefined }), true);  // a permission check carries no media types
  assert.equal(check('display-capture', { mediaTypes: ['video'] }), false);  // nothing to record
  assert.equal(check('media'), false);
  assert.equal(check('display-capture', { isMainFrame: false }), false);
  assert.equal(check('display-capture', { requestingUrl: 'https://evil.example/recordings/' }), false);
  assert.equal(check('display-capture', {}, { getURL: contents.getURL }), false);  // another window
  assert.equal(displayCapturePermission(contents, 'display-capture', details, origin, null), false);
  contents.getURL = () => `${origin}/voice/`;
  assert.equal(check('display-capture', { requestingUrl: `${origin}/voice/` }), false);
  // The answer: the system's loopback, and the page itself as the video, never the screen.
  const mainFrame = { url: `${origin}/recordings/` };
  const frame = { webContents: { mainFrame } };
  assert.deepEqual(systemAudioStreams({ frame: mainFrame, audioRequested: true }, origin, frame), { video: mainFrame, audio: 'loopback' });
  assert.equal(systemAudioStreams({ frame: mainFrame, audioRequested: false }, origin, frame), null);
  assert.equal(systemAudioStreams({ frame: { url: mainFrame.url }, audioRequested: true }, origin, frame), null);
});
test('existing dashboard copy actions can write text; clipboard reads and foreign windows remain denied', () => {
  const origin = 'http://127.0.0.1:8420';
  const contents = { getURL: () => `${origin}/chat/` };
  const windows = [{ webContents: contents }];
  const check = (permission, details = {}, sender = contents) => clipboardWritePermission(sender, permission, details, origin, windows);
  assert.equal(check('clipboard-sanitized-write'), true);
  assert.equal(check('clipboard-read'), false);
  assert.equal(check('clipboard-sanitized-write', { isMainFrame: false }), false);
  assert.equal(check('clipboard-sanitized-write', { requestingUrl: 'https://evil.test/' }), false);
  assert.equal(check('clipboard-sanitized-write', {}, { getURL: contents.getURL }), false);
});
test('macOS asks only for undetermined microphone consent and honors denied/restricted status', async () => {
  const asked = [];
  for (const status of ['denied', 'restricted', 'granted', 'not-determined']) {
    const granted = await macMicrophoneConsent({ getMediaAccessStatus: () => status,
      askForMediaAccess: async type => { asked.push(type); return true; } });
    assert.equal(granted, ['granted', 'not-determined'].includes(status));
  }
  assert.deepEqual(asked, ['microphone']);
});
test('explicit OS consent preserves denied/restricted/unknown states and only macOS prompts', async () => {
  for (const platform of ['darwin', 'win32']) {
    for (const status of ['granted', 'denied', 'restricted', 'unknown', 'not-determined']) {
      let prompts = 0;
      const result = await requestMicrophoneAccess(platform, {
        getMediaAccessStatus: type => { assert.equal(type, 'microphone'); return status; },
        askForMediaAccess: async () => { prompts++; return false; },
      });
      const shouldPrompt = platform === 'darwin' && status === 'not-determined';
      assert.deepEqual(result, { status: shouldPrompt ? 'denied' : status });
      assert.equal(prompts, Number(shouldPrompt));
    }
  }
  assert.deepEqual(await requestMicrophoneAccess('darwin', {
    getMediaAccessStatus: () => 'not-determined', askForMediaAccess: async () => true,
  }), { status: 'granted' });
  assert.deepEqual(await requestMicrophoneAccess('linux'), { status: 'unmanaged' });
  assert.deepEqual(await requestMicrophoneAccess('win32', {
    getMediaAccessStatus: () => { throw Error('unavailable'); },
  }), { status: 'error' });
  assert.equal(microphoneSettingsURL('linux'), null);
  assert.equal(microphoneSettingsURL('win32'), 'ms-settings:privacy-microphone');
  assert.equal(microphoneSettingsURL('darwin'), 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone');
});

test('the hidden "Hey Echo" listener gets the microphone on /wake only, and /wake gives it to no other window', () => {
  const origin = 'http://127.0.0.1:8420';
  const listener = { webContents: { getURL: () => `${origin}/wake/` } };
  const dashboard = { webContents: { getURL: () => `${origin}/wake/` } };
  const check = (contents, details, windows = [dashboard], ear = listener) => audioPermission(contents, 'media', details, origin, windows, ear);
  assert.equal(check(listener.webContents, { mediaType: 'audio', requestingUrl: `${origin}/wake/` }), true);
  assert.equal(check(listener.webContents, { mediaType: 'audio', isMainFrame: false }), false);
  assert.equal(check(listener.webContents, { mediaTypes: ['audio', 'video'] }), false);
  assert.equal(check(listener.webContents, { mediaType: 'audio', requestingUrl: `${origin}/voice/` }), false);
  assert.equal(check(dashboard.webContents, { mediaType: 'audio' }), false, 'the dashboard on /wake');
  assert.equal(check(listener.webContents, { mediaType: 'audio' }, [dashboard], null), false, 'no listener window');
  listener.webContents.getURL = () => `${origin}/voice/`;
  assert.equal(check(listener.webContents, { mediaType: 'audio' }), false, 'the listener elsewhere');
  assert.equal(listenerPage(`${origin}/wake/`, origin), true);
  assert.equal(listenerPage('http://127.0.0.1:7777/wake/', origin), false);
  assert.equal(voicePage(`${origin}/wake/`, origin), false);
});
