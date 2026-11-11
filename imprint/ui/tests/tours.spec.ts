import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// Tutorials (ADR 0183): the welcome on a new installation, then each page's
// steps the first time it opens; every one can be skipped and replayed.

const card = (page: Page) => page.locator('[data-tour-card]');
const seen = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem('imprint.tours.seen') ?? '[]'));

test('a new installation is welcomed, chooses its dashboard and is shown around', async ({ page }) => {
  const writes = await base(page, { mode: 'simple', tutorials: true });
  await page.goto('/search/');
  await expect(card(page).getByRole('heading', { name: 'Welcome to Imprint' })).toBeVisible();
  await expect(card(page).getByRole('button', { name: 'Next' })).toBeFocused();
  await expect(card(page).getByRole('button', { name: /^Simple/ })).toHaveAttribute('aria-pressed', 'true');

  // The choice applies as it is made: the pages behind the card change.
  await card(page).getByRole('button', { name: /^Advanced/ }).click();
  await expect(page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Graph' })).toHaveCount(1);
  await card(page).getByRole('button', { name: /^Simple/ }).click();
  await expect(page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Graph' })).toHaveCount(0);
  expect(writes.map(write => write.value)).toEqual(['advanced', 'simple']);

  await card(page).getByRole('button', { name: 'Next' }).click();
  await expect(card(page).getByRole('heading', { name: 'Your pages' })).toBeVisible();
  await expect(card(page)).toContainText('Home shows what is in your memory');
  await expect(card(page)).toContainText('2 of 4');
  await card(page).getByRole('button', { name: 'Next' }).click();
  await expect(card(page).getByRole('heading', { name: 'Simple or advanced' })).toBeVisible();
  await card(page).getByRole('button', { name: 'Back' }).click();
  await expect(card(page).getByRole('heading', { name: 'Your pages' })).toBeVisible();
  await card(page).getByRole('button', { name: 'Next' }).click();
  await card(page).getByRole('button', { name: 'Next' }).click();
  await expect(card(page).getByRole('heading', { name: 'A short tutorial on every page' })).toBeVisible();
  await card(page).getByRole('button', { name: 'Done' }).click();
  await expect(card(page)).toHaveCount(0);
  expect(await seen(page)).toContain('welcome');

  await page.reload();
  await expect(page.getByRole('button', { name: 'Help and tutorials' })).toBeVisible();
  await page.waitForTimeout(800);
  await expect(card(page).getByRole('heading', { name: 'Welcome to Imprint' })).toHaveCount(0);
});

test('a step sits beside the control it points at and the page stays usable', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: true });
  await page.goto('/search/');
  await card(page).getByRole('button', { name: 'Next' }).click();
  await card(page).getByRole('button', { name: 'Next' }).click();
  await expect(card(page).getByRole('heading', { name: 'Simple or advanced' })).toBeVisible();
  const control = await page.getByRole('button', { name: 'Dashboard: Simple' }).boundingBox();
  await expect.poll(async () => (await card(page).boundingBox())!.y).toBeGreaterThan(control!.y + control!.height);
  const box = (await card(page).boundingBox())!;
  const width = page.viewportSize()!.width;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width);
  // Nothing under the tutorial is blocked.
  await page.getByRole('searchbox').or(page.getByRole('textbox').first()).first().fill('still typing');
});

test('Escape and Skip end a tutorial, and nothing else starts until the next page', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: true });
  await page.goto('/search/');
  await expect(card(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(card(page)).toHaveCount(0);
  expect(await seen(page)).toEqual(['welcome']);
  await page.waitForTimeout(800);
  await expect(card(page)).toHaveCount(0);
});

test('Skip all tutorials turns them off, and the help menu replays and turns them on', async ({ page }) => {
  const writes = await base(page, { mode: 'simple', tutorials: true });
  await page.goto('/search/');
  await card(page).getByRole('button', { name: 'Skip all tutorials' }).click();
  await expect(card(page)).toHaveCount(0);
  await expect(page.getByText('Tutorials are off')).toBeVisible();
  expect(writes).toEqual([{ key: 'ui.tutorials', value: false }]);

  const help = page.getByRole('button', { name: 'Help and tutorials' });
  await help.click();
  await expect(page.getByRole('menuitemcheckbox', { name: 'Start tutorials by themselves' })).toHaveAttribute('aria-checked', 'false');
  await page.getByRole('menuitem', { name: 'Show the welcome again' }).click();
  await expect(card(page).getByRole('heading', { name: 'Welcome to Imprint' })).toBeVisible();
  // Replayed while tutorials are off: there is nothing to turn off.
  await expect(card(page).getByRole('button', { name: 'Skip all tutorials' })).toHaveCount(0);
  await card(page).getByRole('button', { name: 'Skip this tutorial' }).click();

  await help.click();
  await page.getByRole('menuitem', { name: 'Show every tutorial again' }).click();
  expect(writes.at(-1)).toEqual({ key: 'ui.tutorials', value: true });
  expect(await seen(page)).toEqual([]);
  await expect(card(page).getByRole('heading', { name: 'Welcome to Imprint' })).toBeVisible();
});

test('with tutorials off nothing starts by itself', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await page.goto('/search/');
  await expect(page.getByRole('button', { name: 'Help and tutorials' })).toBeVisible();
  await page.waitForTimeout(800);
  await expect(card(page)).toHaveCount(0);
});

test('a tutorial waits behind an open dialog', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: true });
  await page.goto('/search/');
  await expect(card(page)).toBeVisible();
  await page.getByRole('button', { name: 'default' }).click();
  await page.getByRole('menuitem', { name: 'New workspace…' }).click();
  await expect(page.getByRole('dialog', { name: /workspace/i })).toBeVisible();
  await expect(card(page)).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(card(page)).toBeVisible();
});

test('on a phone the step is a sheet at the bottom of the window', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await base(page, { mode: 'simple', tutorials: true });
  await page.goto('/search/');
  await card(page).getByRole('button', { name: 'Next' }).click();
  await expect(card(page).getByRole('heading', { name: 'Your pages' })).toBeVisible();
  const box = (await card(page).boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y + box.height).toBeGreaterThan(780 - 24);
});
