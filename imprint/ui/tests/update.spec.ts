import { test, expect, type Page } from '@playwright/test';

const REPORT = {
  current: 'v0.13.1', latest: 'v0.14.0', name: 'v0.14.0', url: 'https://example.invalid/releases/v0.14.0',
  published_at: '2026-10-24T00:00:00Z', update_available: true, method: 'windows-setup',
};

async function mockApi(page: Page, report: Record<string, unknown>) {
  const state = { version: 'v0.13.1', running: false, log: [] as string[], posts: 0 };
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 1, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total: 1, version: 1, facets: {} } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: state.version } }));
  await page.route('**/api/update', async route => {
    if (route.request().method() === 'POST') {
      state.posts += 1;
      state.running = true;
      state.log = ['[*] Downloading imprint-windows-amd64-setup.exe'];
      return route.fulfill({ json: { started: true, target: 'v0.14.0' } });
    }
    await route.fulfill({ json: { ...report, running: state.running, log: state.log } });
  });
  return state;
}

test('no notice when the installation is current', async ({ page }) => {
  await mockApi(page, { ...REPORT, update_available: false });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Workspace: default' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Update to/ })).toHaveCount(0);
});

test('an update explains the installer handover and reports the new version', async ({ page }) => {
  const state = await mockApi(page, REPORT);
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('v0.13.1 → v0.14.0');
  await expect(dialog).toContainText('The installer updates Imprint and opens it again.');
  await expect(dialog.getByRole('link', { name: 'Release notes' })).toHaveAttribute('href', REPORT.url);
  await dialog.getByRole('button', { name: 'Update now' }).click();
  await expect(dialog.getByRole('status')).toContainText('Updating to v0.14.0');
  await expect(dialog.getByLabel('Updater log')).toContainText('Downloading imprint-windows-amd64-setup.exe');
  expect(state.posts).toBe(1);
  state.version = 'v0.14.0';
  state.running = false;
  await expect(dialog.getByRole('status')).toContainText('Imprint v0.14.0 is running');
  await expect(dialog.getByRole('button', { name: 'Reload dashboard' })).toBeVisible();
});

test('a stopped updater shows its log and can be retried', async ({ page }) => {
  const state = await mockApi(page, { ...REPORT, method: 'archive' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('restarts the dashboard');
  await dialog.getByRole('button', { name: 'Update now' }).click();
  state.running = false;
  state.log = ['[x] Download verification failed: checksum mismatch'];
  await expect(dialog.getByRole('alert')).toContainText('The updater stopped before v0.14.0 was running');
  await expect(dialog.getByLabel('Updater log')).toContainText('checksum mismatch');
  await dialog.getByRole('button', { name: 'Try again' }).click();
  await expect.poll(() => state.posts).toBe(2);
});

test('a container explains how it updates instead of offering to', async ({ page }) => {
  await mockApi(page, { ...REPORT, method: '', unsupported: 'running in a container: pull the new image and recreate the container (the /data volume is kept)' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('pull the new image');
  await expect(dialog.getByRole('button', { name: 'Update now' })).toHaveCount(0);
});
