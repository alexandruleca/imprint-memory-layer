import { test, expect, type Page } from '@playwright/test';
import { PAGES } from '../src/lib/pages';
import { TOURS, WELCOME, stepsIn } from '../src/lib/tours';
import { base } from './ui-fixtures';

// Every page has a short tutorial (ADR 0183), and every step that is not
// marked optional points at a control the page always shows.

const card = (page: Page) => page.locator('[data-tour-card]');
const welcomed = (page: Page, seen: string[] = ['welcome']) =>
  page.addInitScript((ids) => { if (!localStorage.getItem('imprint.tours.seen')) localStorage.setItem('imprint.tours.seen', JSON.stringify(ids)); }, seen);

test('every page of the dashboard has a tutorial, and no tutorial is for a page that does not exist', () => {
  expect(TOURS.map(tour => tour.page).sort()).toEqual(PAGES.map(page => page.href).sort());
  for (const tour of [WELCOME, ...TOURS]) {
    expect(new Set(TOURS.map(t => t.id)).size).toBe(TOURS.length);
    for (const simple of [true, false]) {
      const steps = stepsIn(tour, simple);
      expect(steps.length, `${tour.id} has steps in ${simple ? 'simple' : 'advanced'}`).toBeGreaterThan(0);
      expect(steps.length, `${tour.id} is short`).toBeLessThanOrEqual(6);
      // The first step never depends on data, so the tutorial can start.
      expect(steps[0].optional, `${tour.id} starts with a step that is always there`).toBeFalsy();
    }
  }
});

for (const mode of ['simple', 'advanced'] as const) {
  for (const tour of TOURS) {
    test(`${mode}: the steps of ${tour.id} point at controls of the page`, async ({ page }) => {
      await base(page, { mode, tutorials: false });
      // Empty, well-formed answers where a page reads fields of its answer.
      await page.route('**/api/schedule**', route => route.fulfill({ json: { entries: [], total: 0, next: null } }));
      await page.route('**/api/core', route => route.fulfill({ json: { memories: [], budget: 3000, used: 0, kinds: ['preference'], max_text: 500 } }));
      await page.route('**/api/graph?**', route => route.fulfill({ json: { scope: 'root', center: '', nodes: [], edges: [] } }));
      await page.goto(`${tour.page}/`.replace('//', '/'));
      for (const step of stepsIn(tour, mode === 'simple')) {
        if (!step.target || step.optional) continue;
        await expect(page.locator(`[data-tour="${step.target}"]`).first(), `${step.title}: ${step.target}`).toBeVisible();
      }
    });
  }
}

test("a page's tutorial starts on the first visit, steps through its controls and is not shown again", async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: true });
  await welcomed(page);
  await page.route('**/api/schedule**', route => route.fulfill({ json: { entries: [], total: 0, next: null } }));
  await page.goto('/reminders/');
  await expect(card(page).getByRole('heading', { name: 'Set a reminder' })).toBeVisible();
  await expect(card(page)).toContainText('a question for Imprint to answer later');
  await expect(card(page)).not.toContainText('scheduled job');
  // The step sits by the button it describes.
  const button = (await page.getByRole('button', { name: 'New' }).boundingBox())!;
  await expect.poll(async () => Math.abs((await card(page).boundingBox())!.y - (button.y + button.height))).toBeLessThan(40);
  await card(page).getByRole('button', { name: 'Next' }).click();
  await expect(card(page).getByRole('heading', { name: 'Upcoming' })).toBeVisible();
  await card(page).getByRole('button', { name: 'Done' }).click();
  await expect(card(page)).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('imprint.tours.seen')!))).toEqual(['welcome', '/reminders']);

  await page.reload();
  await expect(page.getByRole('button', { name: 'New' })).toBeVisible();
  await page.waitForTimeout(800);
  await expect(card(page)).toHaveCount(0);

  // The help menu shows it again.
  await page.getByRole('button', { name: 'Help and tutorials' }).click();
  await page.getByRole('menuitem', { name: 'Show me around Reminders' }).click();
  await expect(card(page).getByRole('heading', { name: 'Set a reminder' })).toBeVisible();
});

test('the advanced dashboard has its own steps for the same page', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: true });
  await welcomed(page);
  await page.route('**/api/schedule**', route => route.fulfill({ json: { entries: [], total: 0, next: null } }));
  await page.goto('/reminders/');
  await expect(card(page)).toContainText('a scheduled job such as re-indexing a folder');
});

test('moving to another page ends a tutorial without marking it seen, and the next page shows its own', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: true });
  await welcomed(page);
  await page.goto('/search/');
  await expect(card(page).getByRole('heading', { name: 'Search your memory' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Chat' }).click();
  await expect(card(page).getByRole('heading', { name: 'Ask about anything you added' })).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('imprint.tours.seen')!))).toEqual(['welcome']);
  // Skipped here, the search tutorial still waits for the next visit there.
  await card(page).getByRole('button', { name: 'Skip', exact: true }).click();
  await page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Search' }).click();
  await expect(card(page).getByRole('heading', { name: 'Search your memory' })).toBeVisible();
});

test('a step whose control is not on the page is left out', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: true });
  await welcomed(page);
  await page.goto('/chat/');
  // A new chat has no "Continue with Echo" yet: two steps, not three.
  await expect(card(page).getByRole('heading', { name: 'Ask about anything you added' })).toBeVisible();
  await expect(card(page)).toContainText('1 of 2');
});
