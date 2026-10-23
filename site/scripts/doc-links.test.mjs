import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { rewriteDocLink } from './doc-links.mjs';

const source = path => fileURLToPath(new URL('../../docs/' + path, import.meta.url));

test('repository Markdown links become actual routes including custom slugs and anchors', () => {
  assert.equal(rewriteDocLink('sync.md#direction-who-sends', source('README.md')), '/sync/#direction-who-sends');
  assert.equal(rewriteDocLink('benchmarks.md', source('README.md')), '/docs/benchmarks/');
  assert.equal(rewriteDocLink('adr/README.md', source('README.md')), '/adr/readme/');
  assert.equal(rewriteDocLink('../sync.md', source('adr/0020-explicit-pairing-and-automatic-workspace-sync.md')), '/sync/');
});

test('source references point to GitHub; external URLs, page anchors and absent files stay intact', () => {
  assert.equal(rewriteDocLink('../imprint/sync_ws.py', source('sync.md')), 'https://github.com/alexandruleca/imprint-memory-layer/blob/main/imprint/sync_ws.py');
  for (const url of ['https://example.com/docs.md', '/benchmarks/', '#section', 'missing.md']) {
    assert.equal(rewriteDocLink(url, source('sync.md')), url);
  }
});
