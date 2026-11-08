import { test, expect, type Page } from '@playwright/test';

// Light, Dark or System from the top bar, kept per device and applied before
// first paint by the inline script in app/layout.tsx.

async function base(page: Page) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
}
const dark = (page: Page) => page.evaluate(() => document.documentElement.classList.contains('dark'));

test('a chosen theme applies at once and on the next load before paint', async ({ page }) => {
  await base(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/search/');
  await page.getByRole('button', { name: 'Theme: System' }).click();
  await page.getByRole('menuitemradio', { name: 'Dark' }).click();
  expect(await dark(page)).toBe(true);
  await expect(page.getByRole('button', { name: 'Theme: Dark' })).toBeVisible();

  // The class is there when the HTML has been parsed, before any script bundle runs.
  await page.route('**/_next/static/**', route => route.abort());
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  expect(await dark(page)).toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
});

test('System follows the operating system as it changes', async ({ page }) => {
  await base(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/search/');
  await expect(page.getByRole('button', { name: 'Theme: System' })).toBeVisible();
  expect(await dark(page)).toBe(false);
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect.poll(() => dark(page)).toBe(true);

  await page.getByRole('button', { name: 'Theme: System' }).click();
  await page.getByRole('menuitemradio', { name: 'Light' }).click();
  expect(await dark(page)).toBe(false);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Theme: Light' })).toBeVisible();
  expect(await dark(page)).toBe(false);
});

test('windows without the theme menu follow a choice made in another, and the desktop app is told', async ({ context }) => {
  // The orb's ring is a page of its own in the desktop app (ADR 0154).
  const dashboard = await context.newPage();
  const halo = await context.newPage();
  for (const page of [dashboard, halo]) {
    await base(page);
    await page.emulateMedia({ colorScheme: 'light' });
  }
  await dashboard.addInitScript(() => {
    const calls: string[] = [];
    Object.assign(window, { themeCalls: calls, electronAPI: { isElectron: true, platform: 'linux', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {}, setTheme: (choice: string) => calls.push(choice) } });
  });
  await dashboard.goto('/search/');
  await halo.goto('/launcher/halo/');
  expect(await dark(halo)).toBe(false);
  await dashboard.getByRole('button', { name: 'Theme: System' }).click();
  await dashboard.getByRole('menuitemradio', { name: 'Dark' }).click();
  await expect.poll(() => dark(halo)).toBe(true);
  await expect.poll(() => dashboard.evaluate(() => (window as unknown as { themeCalls: string[] }).themeCalls.at(-1))).toBe('dark');
  // The ring's palette keys on the same class.
  const panel = await halo.evaluate(() => {
    const ring = document.createElement('div');
    ring.className = 'imprint-halo';
    document.body.append(ring);
    return getComputedStyle(ring).getPropertyValue('--panel-bg').trim();
  });
  expect(panel).toBe('#0a1822f7');
});
