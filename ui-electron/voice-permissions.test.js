'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { voicePage, audioPermission, clipboardWritePermission, macMicrophoneConsent } = require('./voice-permissions');
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
