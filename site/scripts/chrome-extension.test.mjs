import {test} from 'node:test';
import assert from 'node:assert/strict';
import {latestChromeExtension, extensionAssetName} from '../src/lib/chrome-extension.mjs';

test('latest extension stays downloadable when a newer desktop release has no ZIP', () => {
  const release = {tag: 'v1', publishedAt: '2026-10-22', assets: [{name: extensionAssetName, url: 'https://github.com/release/extension.zip'}]};
  const selected = latestChromeExtension({releases: [release, {tag: 'v2', publishedAt: '2026-10-23', assets: []}]});
  assert.equal(selected.release.tag, 'v1');
  assert.equal(selected.asset.url, release.assets[0].url);
});

test('prereleases and unrelated platform ZIPs are not extension downloads', () => {
  assert.equal(latestChromeExtension({releases: [{prerelease: true, assets: [{name: extensionAssetName}]}, {assets: [{name: 'imprint-windows-amd64.zip'}]}]}), null);
  assert.equal(latestChromeExtension({}), null);
});

test('only the signed Firefox add-on is offered for download', async () => {
  const {latestFirefoxExtension, firefoxAssetName} = await import('../src/lib/chrome-extension.mjs');
  const signed = {tag: 'v1', publishedAt: '2026-11-20', assets: [{name: firefoxAssetName, url: 'https://github.com/release/addon.xpi'}]};
  const unsigned = {tag: 'v2', publishedAt: '2026-11-21', assets: [{name: 'imprint-firefox-extension-unsigned.xpi'}]};
  assert.equal(latestFirefoxExtension({releases: [signed, unsigned]}).release.tag, 'v1');
  assert.equal(latestFirefoxExtension({releases: [unsigned]}), null);
});

test('the Firefox button opens the AMO listing once Mozilla has approved one', async () => {
  const {amoListing, firefoxInstall, firefoxAssetName} = await import('../src/lib/chrome-extension.mjs');
  const listing = 'https://addons.mozilla.org/en-US/firefox/addon/imprint-capture/';
  assert.deepEqual(amoListing({url: listing, status: 'public', current_version: {version: '0.62.2'}}), {url: listing, version: '0.62.2'});
  // Awaiting review, a listing elsewhere, or no approved version: no listing.
  assert.equal(amoListing({url: listing, status: 'nominated', current_version: null}), null);
  assert.equal(amoListing({url: 'https://example.com/addon/', status: 'public', current_version: {version: '1'}}), null);
  assert.equal(amoListing(null), null);
  const signed = {tag: 'v0.60.0', publishedAt: '2026-11-22', assets: [{name: firefoxAssetName, url: 'https://github.com/release/addon.xpi'}]};
  assert.deepEqual(firefoxInstall({releases: [signed], firefoxListing: {url: listing, version: '0.62.2'}}), {url: listing, listed: true});
  assert.deepEqual(firefoxInstall({releases: [signed], firefoxListing: null}), {url: signed.assets[0].url, listed: false});
  assert.equal(firefoxInstall({releases: []}), null);
});

test('the site looks up the add-on ID the package is built with', async () => {
  const {readFileSync} = await import('node:fs');
  const {firefoxAddonId} = await import('../src/lib/chrome-extension.mjs');
  const packager = readFileSync(new URL('../../scripts/package_chrome_extension.py', import.meta.url), 'utf8');
  assert.equal(packager.match(/^GECKO_ID = '([^']+)'/m)[1], firefoxAddonId);
});
