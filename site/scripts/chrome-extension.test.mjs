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
