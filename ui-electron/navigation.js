'use strict';

function dashboardURL(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password) {
    throw new Error('The Imprint dashboard URL must use HTTP on loopback.');
  }
  return url;
}

function sameOrigin(value, origin) {
  try { return new URL(value).origin === origin; } catch { return false; }
}

function externalURL(value) {
  try { return ['https:', 'http:'].includes(new URL(value).protocol); } catch { return false; }
}

module.exports = { dashboardURL, sameOrigin, externalURL };
