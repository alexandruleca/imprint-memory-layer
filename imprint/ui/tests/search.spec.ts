import { test, expect } from '@playwright/test';

const memory = {
  id: '1234567890abcdef', label: 'Installer decision', content: 'Use native runners for installers.',
  project: 'imprint', source: 'imprint/docs/adr.md', type: 'decision', chunk_index: 0,
  tags: { lang: 'markdown', layer: 'docs', kind: 'source', domain: [], topics: [] },
  similarity: .76, match_type: 'hybrid', source_hash: 'abc1234567890', source_mtime: 1700000000,
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
});

test('empty search, filters, full memory, keyboard and responsive layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/search?**', async route => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get('project')).toBe('imprint');
    expect(params.get('type')).toBe('decision');
    expect(params.get('mode')).toBe('hybrid');
    await route.fulfill({ json: { nodes: params.get('q') === 'missing' ? [] : [memory] } });
  });
  await page.route('**/api/memory/*', route => route.fulfill({ json: { ...memory, content: 'Complete durable decision and its reasoning.' } }));
  await page.goto('/search/');
  await expect(page.getByText('What would you like to remember?')).toBeVisible();
  await page.getByLabel('Filter by project').fill('imprint');
  await page.getByLabel('Memory type', { exact: true }).selectOption('decision');
  await page.getByLabel('Search query', { exact: true }).fill('missing');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('No matching memories')).toBeVisible();
  await page.getByLabel('Search query', { exact: true }).fill('installer');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '1 result' })).toBeVisible();
  const full = page.getByRole('button', { name: 'Open full memory' });
  await full.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByText('Complete durable decision and its reasoning.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(full).toBeFocused();
  await page.keyboard.press('Control+k');
  await expect(page.getByLabel('Search query', { exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test('service errors are visible and retry succeeds', async ({ page }) => {
  let fail = true;
  await page.route('**/api/search?**', route => route.fulfill(fail
    ? { status: 503, json: { detail: 'The model is still loading' } }
    : { json: { nodes: [memory] } }));
  await page.goto('/search/');
  await page.getByLabel('Search query', { exact: true }).fill('installer');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('alert', { name: 'Search error' })).toContainText('The model is still loading');
  fail = false;
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('alert', { name: 'Search error' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /1. Installer decision/ })).toBeVisible();
});

test('an older search cannot overwrite a newer result', async ({ page }) => {
  await page.route('**/api/search?**', async route => {
    const query = new URL(route.request().url()).searchParams.get('q');
    if (query === 'older') await new Promise(resolve => setTimeout(resolve, 500));
    await route.fulfill({ json: { nodes: [{ ...memory, label: query === 'older' ? 'Old result' : 'New result' }] } }).catch(() => {});
  });
  await page.goto('/search/');
  await page.getByLabel('Search query', { exact: true }).fill('older');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByLabel('Search query', { exact: true }).fill('newer');
  await page.getByRole('button', { name: 'Search again' }).click();
  await expect(page.getByRole('button', { name: /1. New result/ })).toBeVisible();
  await page.waitForTimeout(600);
  await expect(page.getByText('Old result')).toHaveCount(0);
});

test('disconnection is visible and can be retried', async ({ page }) => {
  let online = false;
  await page.route('**/api/ping', route => route.fulfill({ status: online ? 200 : 503, json: {} }));
  await page.goto('/search/');
  await expect(page.getByText('Local service is unavailable.', { exact: false })).toBeVisible();
  online = true;
  await page.getByRole('button', { name: 'Retry connection' }).click();
  await expect(page.getByText('Local service is unavailable.', { exact: false })).toHaveCount(0);
});
