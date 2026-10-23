import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'alpha', workspaces: ['alpha', 'beta'] } }));
  await page.route('**/api/sources?**', route => route.fulfill({ json: { sources: [], total: 0 } }));
  await page.route('**/api/workspace/roots?**', route => route.fulfill({ json: { workspace: 'alpha', roots: ['/project'], exclusions: [] } }));
});

test('cleanup previews before confirmation and applies to the reviewed workspace', async ({ page }) => {
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/sources/purge', async route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    await route.fulfill({ json: { ...body, chunks: 2, sources: 1, sample: [{ project: 'project', source: 'project/wrong/file.py' }] } });
  });
  await page.goto('/sources/');
  await page.getByText('Workspace roots and cleanup').click();
  await page.getByLabel('Cleanup target').fill('project/wrong');
  await page.getByLabel('Include directory descendants').check();
  await page.getByRole('button', { name: 'Preview cleanup' }).click();
  await expect(page.getByRole('status')).toContainText('2 chunks in 1 sources');
  expect(requests).toHaveLength(1);
  expect(requests[0].apply).toBeUndefined();
  await page.getByRole('button', { name: 'Purge from alpha…' }).click();
  const apply = page.getByRole('button', { name: 'Purge indexed copies' });
  await expect(apply).toBeDisabled();
  await page.getByLabel('Type alpha to confirm').fill('alpha');
  await apply.click();
  await expect(page.getByRole('status')).toContainText('Removed 2 chunks');
  expect(requests[1]).toMatchObject({ workspace: 'alpha', target: 'project/wrong', recursive: true, apply: true });
});

test('overlapping root requires an explicit sharing confirmation', async ({ page }) => {
  const confirmations: boolean[] = [];
  await page.route('**/api/workspace/roots', async route => {
    const body = route.request().postDataJSON();
    confirmations.push(body.allow_overlap);
    await route.fulfill(body.allow_overlap
      ? { json: { workspace: 'alpha', roots: ['/project', '/shared'] } }
      : { status: 400, json: { error: 'Root overlaps workspace(s): beta. Confirm with --allow-overlap.' } });
  });
  await page.goto('/sources/');
  await page.getByText('Workspace roots and cleanup').click();
  await page.getByLabel('Workspace root directory').fill('/shared');
  await page.getByRole('button', { name: 'Add root', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Share this root across workspaces?' })).toContainText('beta');
  expect(confirmations).toEqual([false]);
  await page.getByRole('button', { name: 'Allow overlap and add root' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(confirmations).toEqual([false, true]);
});
