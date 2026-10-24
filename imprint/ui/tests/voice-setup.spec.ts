import { test, expect, type Page } from '@playwright/test';

async function prepareSetup(page: Page) {
  let state = { stage: 'idle', busy: false, installed: false, supported: true,
    downloaded: 0, total: 1074794688, problem: null as string | null, platform: 'win32' };
  let starts = 0;
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/voice/setup/cancel') {
      state = { ...state, stage: 'cancelled', busy: false };
      return route.fulfill({ json: state });
    }
    if (url.pathname === '/api/voice/setup') {
      if (route.request().method() === 'POST') { starts++; state = { ...state, stage: 'downloading', busy: true, problem: null }; }
      return route.fulfill({ json: state });
    }
    if (url.pathname === '/api/voice/status') return route.fulfill({ json: { ...state, configured: false, chat_provider: 'local', workspace: 'default' } });
    if (url.pathname === '/api/voice/sessions') return route.fulfill({ json: { token: 'fixture', workspace: 'default' } });
    if (url.pathname === '/api/config') return route.fulfill({ json: { settings: [] } });
    if (url.pathname === '/api/workspaces') return route.fulfill({ json: { active: 'default', known: ['default'], workspaces: ['default'] } });
    return route.fulfill({ json: { ok: true, jobs: [], requests: [], version: 'test' } });
  });
  await page.routeWebSocket('**/api/voice/stream', route => route.onMessage(() => route.send(JSON.stringify({ type: 'ready', workspace: 'default' }))));
  return { update: (values: Partial<typeof state>) => { state = { ...state, ...values }; }, starts: () => starts };
}

test('first-time setup explains size and saved downloads before an explicit action', async ({ page }, testInfo) => {
  const fixture = await prepareSetup(page);
  await page.goto('/voice/');
  await expect(page.getByRole('heading', { name: 'Set up Echo, once' })).toBeVisible();
  await expect(page.getByText('1.07 GB', { exact: true })).toBeVisible();
  await expect(page.getByText('Allow 4 GB free for setup')).toBeVisible();
  await expect(page.getByText('Mic stays off', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start conversation' })).toHaveCount(0);
  expect(fixture.starts()).toBe(0);
  await page.screenshot({ path: testInfo.outputPath('echo-first-setup.png'), fullPage: true });
  await page.getByRole('button', { name: 'Download & set up Echo' }).click();
  await expect(page.getByRole('progressbar', { name: 'Voice model download' })).toHaveAttribute('aria-valuenow', '0');
  expect(fixture.starts()).toBe(1);
});

test('download bytes remain visible across navigation; preparation has no fabricated percentage', async ({ page }) => {
  const fixture = await prepareSetup(page);
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Download & set up Echo' }).click();
  fixture.update({ downloaded: 537397344 });
  await expect(page.getByRole('progressbar', { name: 'Voice model download' })).toHaveAttribute('aria-valuenow', '50');
  await expect(page.getByText('537 MB of 1.07 GB')).toBeVisible();
  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  const indicator = page.getByRole('link', { name: /Echo · 50%.*View voice setup/ });
  await expect(indicator).toBeVisible();
  await indicator.click();
  await expect(page.getByRole('button', { name: 'Cancel setup' })).toBeVisible();
  expect(fixture.starts()).toBe(1);
  fixture.update({ stage: 'building', downloaded: 1074794688 });
  await expect(page.getByRole('progressbar', { name: 'Echo preparation' })).not.toHaveAttribute('aria-valuenow');
  await expect(page.getByRole('status').filter({ hasText: 'Building the speech engine' })).toBeVisible();
  await expect(page.getByText('In progress', { exact: true })).toBeVisible();
  fixture.update({ stage: 'ready', busy: false, installed: true });
  await expect(page.getByRole('button', { name: 'Start conversation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Echo ready. View voice setup' })).toBeVisible();
});

test('missing tools, cancellation and retries keep setup actionable', async ({ page }) => {
  const fixture = await prepareSetup(page);
  fixture.update({ stage: 'blocked', problem: 'compiler' });
  await page.goto('/voice/');
  await expect(page.locator('.echo-setup [role=alert]')).toContainText('C++ build tool');
  await page.getByText('Build tools & download details').click();
  await expect(page.getByText(/Windows: CMake and Visual Studio C\+\+ Build Tools/)).toBeVisible();
  await page.getByRole('button', { name: 'Check again' }).click();
  fixture.update({ downloaded: 500000000 });
  await page.getByRole('button', { name: 'Cancel setup' }).click();
  await expect(page.getByText(/current unfinished file downloads again/)).toBeVisible();
  await page.getByRole('button', { name: 'Continue setup' }).click();
  fixture.update({ stage: 'error', busy: false, problem: 'download' });
  await expect(page.locator('.echo-setup [role=alert]')).toContainText('Verified downloads are kept');
  await expect(page.getByRole('button', { name: 'Retry setup' })).toBeEnabled();
});

test('compact first-run setup leaves the head transparent and fits a narrow window', async ({ page }, testInfo) => {
  await prepareSetup(page);
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await expect(page.getByRole('heading', { name: 'Set up Echo, once' })).toBeVisible();
  await expect(page.locator('.voice-compact')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('.voice-compact')).toHaveCSS('border-top-width', '0px');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole('button', { name: 'Download & set up Echo' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('echo-first-setup-compact.png'), fullPage: true });
});
