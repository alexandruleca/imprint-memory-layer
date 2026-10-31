import { test, expect, type Page } from '@playwright/test';
import { slack } from './slack';

const REPORT = {
  current: 'v0.13.1', latest: 'v0.14.0', name: 'v0.14.0', url: 'https://example.invalid/releases/v0.14.0',
  published_at: '2026-10-24T00:00:00Z', update_available: true, method: 'windows-setup',
};

// The server's version is what the restarted server reports; the release
// check then sees the installation as current.
async function mockApi(page: Page, report: Record<string, unknown>) {
  const state = { version: 'v0.13.1', running: false, failed: false, log: [] as string[], posts: 0 };
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 1, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total: 1, version: 1, facets: {} } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: state.version } }));
  await page.route('**/api/update', async route => {
    if (route.request().method() === 'POST') {
      state.posts += 1;
      state.running = true;
      state.failed = false;
      state.log = ['[*] Downloading imprint-windows-amd64-setup.exe'];
      return route.fulfill({ json: { started: true, target: 'v0.14.0' } });
    }
    await route.fulfill({ json: {
      ...report, current: state.version, update_available: report.update_available && state.version !== report.latest,
      running: state.running, failed: state.failed, log: state.log,
    } });
  });
  return state;
}

test('no notice when the installation is current', async ({ page }) => {
  await mockApi(page, { ...REPORT, update_available: false });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Workspace: default' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Update to/ })).toHaveCount(0);
});

test('an update waits through the installer handover and reloads into the new version', async ({ page }) => {
  const state = await mockApi(page, REPORT);
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('v0.13.1 → v0.14.0');
  await expect(dialog).toContainText('runs it in the background. The dashboard restarts when it finishes.');
  await expect(dialog.getByRole('link', { name: 'Release notes' })).toHaveAttribute('href', REPORT.url);
  await dialog.getByRole('button', { name: 'Update now' }).click();
  await expect(dialog.getByRole('status')).toContainText('Updating to v0.14.0');
  await expect(dialog.getByLabel('Updater log')).toContainText('Downloading imprint-windows-amd64-setup.exe');
  expect(state.posts).toBe(1);
  // The updater exits once the installer runs; the update is not over.
  state.running = false;
  state.log = [...state.log, '[+] Installer started: it updates Imprint to v0.14.0 and restarts the dashboard.'];
  await expect(dialog.getByLabel('Updater log')).toContainText('Installer started');
  await page.waitForTimeout(slack(2500)); // at least one more poll
  await expect(dialog.getByRole('status')).toContainText('Updating to v0.14.0');
  await expect(dialog.getByRole('button', { name: 'Try again' })).toHaveCount(0);
  // The restarted server runs the new release: the page reloads by itself.
  const reloaded = page.waitForEvent('load');
  state.version = 'v0.14.0';
  await reloaded;
  await expect(page.getByRole('button', { name: 'Workspace: default' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Update Imprint' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Update to/ })).toHaveCount(0);
});

test('a failed updater shows its log and can be retried', async ({ page }) => {
  const state = await mockApi(page, { ...REPORT, method: 'archive' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('restarts the dashboard');
  await dialog.getByRole('button', { name: 'Update now' }).click();
  state.running = false;
  state.failed = true;
  state.log = ['[x] Download verification failed: checksum mismatch'];
  await expect(dialog.getByRole('alert')).toContainText('The update to v0.14.0 failed');
  await expect(dialog.getByLabel('Updater log')).toContainText('checksum mismatch');
  await dialog.getByRole('button', { name: 'Try again' }).click();
  await expect.poll(() => state.posts).toBe(2);
});

test('a dev build offered on the dev channel is marked as one', async ({ page }) => {
  await mockApi(page, { ...REPORT, latest: 'v0.14.0-dev.3', name: 'v0.14.0-dev.3', channel: 'dev', prerelease: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0-dev.3' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('v0.13.1 → v0.14.0-dev.3');
  await expect(dialog).toContainText('Dev build');
});

test('a stable release is not marked as a dev build', async ({ page }) => {
  await mockApi(page, { ...REPORT, channel: 'dev', prerelease: false });
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  await expect(page.getByRole('dialog', { name: 'Update Imprint' })).not.toContainText('Dev build');
});

test('a container explains how it updates instead of offering to', async ({ page }) => {
  await mockApi(page, { ...REPORT, method: '', unsupported: 'running in a container: pull the new image and recreate the container (the /data volume is kept)' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Update to v0.14.0' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update Imprint' });
  await expect(dialog).toContainText('pull the new image');
  await expect(dialog.getByRole('button', { name: 'Update now' })).toHaveCount(0);
});
