import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
});

test('desktop sidebar distinguishes hiding the window from quitting Imprint', async ({ page }) => {
  await page.addInitScript(() => {
    const calls: string[] = [];
    Object.assign(window, { desktopCalls: calls, electronAPI: {
      isElectron: true, platform: 'win32',
      close: () => calls.push('hide'),
      quit: () => calls.push('quit'),
      minimize: () => calls.push('minimize'),
      maximize: () => calls.push('maximize'),
    } });
  });
  await page.goto('/search/');
  await page.getByRole('button', { name: 'Hide window (keep Imprint running)' }).click();
  expect(await page.evaluate(() => Reflect.get(window, 'desktopCalls'))).toEqual(['hide']);
  const quit = page.getByRole('button', { name: 'Quit Imprint', exact: true });
  await expect(quit).toHaveAttribute('title', 'Quit Imprint and stop its background service');
  await quit.click();
  expect(await page.evaluate(() => Reflect.get(window, 'desktopCalls'))).toEqual(['hide', 'quit']);
});

test('browser dashboard has no desktop quit controls', async ({ page }) => {
  await page.goto('/search/');
  await expect(page.getByRole('button', { name: 'Quit Imprint', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Hide window (keep Imprint running)' })).toHaveCount(0);
});
