'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { dashboardURL, sameOrigin, externalURL } = require('./navigation');

test('dashboard accepts loopback and rejects lookalike hosts and credentials', () => {
  for (const host of ['localhost', '127.0.0.1', '[::1]']) {
    assert.equal(dashboardURL(`http://${host}:8420`).port, '8420');
  }
  for (const url of ['http://localhost.evil.test', 'http://127.0.0.10',
    'https://localhost', 'file:///etc/passwd', 'http://user@localhost']) {
    assert.throws(() => dashboardURL(url));
  }
});

test('navigation stays on the exact dashboard origin', () => {
  const origin = 'http://127.0.0.1:8420';
  assert.ok(sameOrigin(`${origin}/projects`, origin));
  for (const url of ['http://127.0.0.1:8421', 'http://127.0.0.1.evil.test',
    'http://localhost:8420', 'not a URL']) assert.equal(sameOrigin(url, origin), false);
});

test('external links only allow browser protocols', () => {
  assert.ok(externalURL('https://example.com'));
  for (const url of ['file:///tmp/test', 'javascript:alert(1)', 'imprint://run', 'broken']) {
    assert.equal(externalURL(url), false);
  }
});
