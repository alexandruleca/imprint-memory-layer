import { test, expect } from '@playwright/test';
import { PAGES } from '../src/lib/pages';
import { base } from './ui-fixtures';

// The simple and the advanced dashboard (ADR 0182): one set of pages shown
// two ways, chosen by the `ui.mode` setting.

const SIMPLE = ['Home', 'Chat', 'Search', 'Echo', 'Recordings', 'Reminders', 'Library', 'About you', 'Skills', 'Devices', 'Settings'];
const nav = (page: import('@playwright/test').Page) => page.getByRole('navigation', { name: 'Pages' });

test('the simple dashboard lists the everyday pages under their plain names', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await page.goto('/search/');
  await expect(nav(page).getByRole('link')).toHaveText(SIMPLE);
  await expect(nav(page).getByRole('link', { name: 'Search' })).toHaveAttribute('aria-current', 'page');
  // Its groups are headings, not buttons that fold.
  await expect(nav(page).getByText('Your memory')).toBeVisible();
  await expect(nav(page).getByRole('button')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Dashboard: Simple' })).toBeVisible();
});

test('switching to advanced saves the setting and shows every page', async ({ page }) => {
  const writes = await base(page, { mode: 'simple', tutorials: false });
  await page.goto('/search/');
  await page.getByRole('button', { name: 'Dashboard: Simple' }).click();
  await page.getByRole('menuitemradio', { name: /Advanced/ }).click();
  await expect(page.getByRole('button', { name: 'Dashboard: Advanced' })).toBeVisible();
  expect(writes).toEqual([{ key: 'ui.mode', value: 'advanced' }]);
  // Folded groups keep their links in the page for the narrow sidebar.
  await expect(nav(page).locator('a')).toHaveCount(PAGES.length);
  await expect(nav(page).getByRole('link', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(nav(page).getByRole('button', { name: 'Memory' })).toBeVisible();

  // The next load draws it at once, before the service has answered.
  await page.route('**/api/ui', () => {});
  await page.reload();
  await expect(nav(page).getByRole('link', { name: 'Dashboard', exact: true })).toBeVisible();
});

test('a switch that cannot be saved is put back', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await page.route('**/api/config/ui.mode', route => route.fulfill({ status: 500, json: { error: 'config.json is read-only' } }));
  await page.goto('/search/');
  await page.getByRole('button', { name: 'Dashboard: Simple' }).click();
  await page.getByRole('menuitemradio', { name: /Advanced/ }).click();
  await expect(page.getByText('Could not switch the dashboard')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Dashboard: Simple' })).toBeVisible();
  await expect(nav(page).getByRole('link')).toHaveText(SIMPLE);
});

test('a service that names no mode leaves the advanced dashboard', async ({ page }) => {
  await base(page);
  await page.goto('/search/');
  await expect(page.getByRole('button', { name: 'Dashboard: Advanced' })).toBeVisible();
  await expect(nav(page).locator('a')).toHaveCount(PAGES.length);
});

test('an advanced page opened in the simple dashboard says so and offers the switch', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await page.goto('/queue/');
  const notice = page.getByRole('note');
  await expect(notice).toContainText('Queue is a page of the advanced dashboard');
  await expect(page.getByRole('heading', { name: 'Queue', level: 1 })).toBeVisible();
  await notice.getByRole('button', { name: 'Switch to Advanced' }).click();
  await expect(notice).toHaveCount(0);
  await expect(nav(page).getByRole('link', { name: 'Queue' })).toHaveAttribute('aria-current', 'page');

  // A page of both dashboards has no notice.
  await page.getByRole('button', { name: 'Dashboard: Advanced' }).click();
  await page.getByRole('menuitemradio', { name: /Simple/ }).click();
  await page.goto('/search/');
  await expect(nav(page).getByRole('link')).toHaveText(SIMPLE);
  await expect(page.getByRole('note')).toHaveCount(0);
});

test('another window of the device follows the switch', async ({ context }) => {
  const ui = { mode: 'simple' as const, tutorials: false };
  const first = await context.newPage();
  const second = await context.newPage();
  await base(first, ui);
  await base(second, ui);
  await first.goto('/search/');
  await second.goto('/chat/');
  await expect(nav(second).getByRole('link')).toHaveText(SIMPLE);
  await first.getByRole('button', { name: 'Dashboard: Simple' }).click();
  await first.getByRole('menuitemradio', { name: /Advanced/ }).click();
  await expect(nav(second).locator('a')).toHaveCount(PAGES.length);
});
