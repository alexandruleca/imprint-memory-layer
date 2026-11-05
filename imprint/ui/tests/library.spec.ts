import { test, expect, type Page } from '@playwright/test';
import { library, mockLibrary, type Library } from './library-fixtures';

// Sources and Projects browse in paged tabs (ADR 0139), at about the
// maintainer's scale: ~18,800 sources over 51 projects, one holding ~7,500.

const n = (value: number) => value.toLocaleString('en-US');
const pager = (page: Page) => page.getByRole('navigation', { name: 'Pagination' });
const indexed = (page: Page) => page.getByRole('list', { name: 'Indexed sources' }).getByRole('listitem');

test.describe('Sources', () => {
  const lib = library(1);
  // The page's own order: most chunks first, then by key.
  const bySize = [...lib.sources].sort((a, b) => b.chunks - a.chunks || (a.source < b.source ? -1 : 1));
  // Tests that need no scale use a smaller workspace, which keeps the suite quick.
  const few = library(0.05);
  const fewBySize = [...few.sources].sort((a, b) => b.chunks - a.chunks || (a.source < b.source ? -1 : 1));

  test('every source is listed, then paged, searched, filtered and sorted in the browser', async ({ page }) => {
    const sent = await mockLibrary(page, lib);
    await page.goto('/sources/');
    await expect(page.getByRole('heading', { name: `Sources ${n(lib.sources.length)}` })).toBeVisible();
    await expect(page.getByRole('tab', { name: `Indexed, ${lib.sources.length}` })).toHaveAttribute('aria-selected', 'true');
    expect(Object.fromEntries(sent.sources[0])).toEqual({ limit: '100000' });
    await expect(pager(page)).toContainText(`1–20 of ${n(lib.sources.length)}`);
    await expect(indexed(page)).toHaveCount(20);
    // Largest first.
    await expect(indexed(page).first()).toHaveAttribute('aria-label', bySize[0].source);

    const conversations = lib.sources.filter(row => row.kind === 'conversation');
    await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: /Conversations/ }).click();
    await expect(pager(page)).toContainText(`of ${n(conversations.length)}`);
    await expect(indexed(page).first()).toContainText('Conversation');

    const flaky = conversations.filter(row => row.title.includes('flaky'));
    await page.getByRole('searchbox', { name: 'Search' }).fill('flaky');
    await expect(pager(page)).toContainText(`of ${n(flaky.length)}`);
    await expect(page.getByTestId('review-live')).toContainText(`${flaky.length} match`);

    await page.getByRole('searchbox', { name: 'Search' }).fill('');
    await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: 'All' }).click();
    await page.getByRole('combobox', { name: 'Project' }).click();
    const editor = lib.sources.filter(row => row.project === 'editor-2d');
    await page.getByRole('option', { name: `editor-2d (${n(editor.length)})` }).click();
    await expect(pager(page)).toContainText(`of ${n(editor.length)}`);

    await page.getByRole('button', { name: 'Sort' }).click();
    await page.getByRole('menuitemradio', { name: 'Recently updated' }).click();
    const newest = [...editor].sort((a, b) => b.updated - a.updated || b.chunks - a.chunks)[0];
    await expect(indexed(page).first()).toHaveAttribute('aria-label', newest.title || newest.source);

    // Rows per page are remembered in this browser, as on Organize.
    await page.getByRole('combobox', { name: 'Rows per page' }).click();
    await page.getByRole('option', { name: '50' }).click();
    await page.reload();
    await expect(indexed(page)).toHaveCount(50);
    await pager(page).getByRole('button', { name: 'Next page' }).click();
    await expect(pager(page)).toContainText(`51–100 of ${n(lib.sources.length)}`);
  });

  test('a source opens its details and chunks, and reads whole or at a chunk', async ({ page }) => {
    const sent = await mockLibrary(page, few);
    await page.goto('/sources/');
    const row = indexed(page).nth(1);
    const key = fewBySize[1].source;
    await row.click();
    const chunks = row.getByRole('region', { name: 'Chunks' });
    await expect(chunks).toContainText(`1–8 of ${Math.min(500, fewBySize[1].chunks)}`);
    await expect(row).toContainText('Recorded on: Workstation');
    await expect(row.getByRole('link', { name: 'paging' })).toHaveAttribute('href', '/topics/?topic=paging');
    await chunks.getByRole('button', { name: 'Next chunks' }).click();
    await expect(chunks).toContainText('#8');
    await chunks.getByRole('button', { name: 'Read here' }).first().click();
    await expect(page.getByRole('dialog')).toContainText(`The whole text of ${key}`);
    expect(Object.fromEntries(sent.reads[0])).toEqual({ source: key, project: fewBySize[1].project });
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);

    // The keyboard: j to the next row, o reads it, Enter closes and opens details.
    await row.focus();
    await page.keyboard.press('j');
    await expect(indexed(page).nth(2)).toBeFocused();
    await page.keyboard.press('o');
    await expect(page.getByRole('dialog')).toContainText(`The whole text of ${fewBySize[2].source}`);
  });

  test('a source is purged from its row and allowed back from Excluded', async ({ page }) => {
    const sent = await mockLibrary(page, few);
    await page.goto('/sources/');
    const key = fewBySize[0].source;
    await page.getByRole('button', { name: `More for ${key}` }).click();
    await page.getByRole('menuitem', { name: 'Purge from this workspace…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Purge from alpha' });
    // Opened from a row, it previews that source at once.
    await expect(dialog.getByRole('status')).toContainText('4 chunks in 1 source');
    expect(sent.purges[0]).toEqual({ workspace: 'alpha', target: key, kind: 'source', recursive: false });
    await dialog.getByRole('button', { name: 'Purge from alpha…' }).click();
    await page.getByLabel('Type alpha to confirm').fill('alpha');
    await page.getByRole('button', { name: 'Purge indexed copies' }).click();
    await expect(page.getByText('Removed 4 chunks from 1 source')).toBeVisible();
    expect(sent.purges[1]).toMatchObject({ workspace: 'alpha', target: key, kind: 'source', apply: true });
    await expect(page.getByRole('tab', { name: 'Excluded, 1' })).toBeVisible();

    await page.getByRole('tab', { name: 'Excluded, 1' }).click();
    await expect(page).toHaveURL(/#excluded$/);
    await page.getByRole('listitem', { name: key }).getByRole('button', { name: 'Allow re-ingestion' }).click();
    await expect(page.getByText('Nothing is excluded.')).toBeVisible();
    expect(sent.roots[0]).toEqual({ workspace: 'alpha', path: key, action: 'restore', kind: 'source' });
    await expect(page.getByRole('tab', { name: 'Excluded, 0' })).toBeVisible();
  });

  test('Move takes sources of one project, and data changes wait for Refresh', async ({ page }) => {
    const small = library(0.02);
    const sent = await mockLibrary(page, small);
    let announce!: () => void;
    const ready = new Promise<void>(resolve => { announce = resolve; });
    await page.route('**/api/stream', async route => {
      await ready;
      await route.fulfill({ contentType: 'text/event-stream', body: 'event: update\ndata: {"version":1}\n\n' });
    });
    await page.goto('/sources/');
    const rows = indexed(page);
    await expect(rows.first()).toBeVisible();
    const keys = await rows.evaluateAll(items => items.map(item => item.getAttribute('aria-label') ?? ''));
    const projectOf = (key: string) => small.sources.find(row => (row.title || row.source) === key)?.project;
    const first = keys.findIndex(key => projectOf(key));
    const other = keys.findIndex(key => projectOf(key) && projectOf(key) !== projectOf(keys[first]));
    await page.getByRole('checkbox', { name: `Select ${keys[first]}` }).click();
    await page.getByRole('checkbox', { name: `Select ${keys[other]}` }).click();
    await expect(page.getByRole('button', { name: 'Move 2 to…' })).toBeDisabled();
    await page.getByRole('checkbox', { name: `Select ${keys[other]}` }).click();
    await page.getByRole('button', { name: 'Move 1 to…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Move sources to another project' });
    await expect(dialog).toContainText(`Moves every memory of 1 source from ${projectOf(keys[first])}.`);
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    // A change after the list loaded asks before reshuffling it.
    await page.waitForTimeout(4200);
    announce();
    await expect(page.getByText('Sources changed since this list was loaded.')).toBeVisible();
    const before = sent.sources.length;
    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(page.getByText('Sources changed since this list was loaded.')).toHaveCount(0);
    expect(sent.sources.length).toBe(before + 1);
  });

  test('tabs switch by key and link by address', async ({ page }) => {
    await mockLibrary(page, library(0.02), { exclusions: [{ kind: 'path', path: '/home/me/code/vendor', recursive: true }] });
    await page.goto('/sources/#roots');
    await expect(page.getByRole('tab', { name: 'Roots, 1' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByText('/home/me/code', { exact: true })).toBeVisible();
    await page.locator('body').press('2');
    await expect(page.getByRole('tab', { name: 'Excluded, 1' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('listitem', { name: '/home/me/code/vendor' })).toContainText('File path · with everything under it');
    await page.locator('body').press('1');
    await expect(page).toHaveURL(/#indexed$/);
    await page.locator('body').press('/');
    await expect(page.getByRole('searchbox', { name: 'Search' })).toBeFocused();
    await page.keyboard.press('Escape');
    await page.locator('body').press('?');
    await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toContainText('Move to another project');
  });
});

test.describe('Projects', () => {
  const lib: Library = library(1);
  const few: Library = library(0.05);

  test('the address, then the last project opened, then the largest opens; J and K step through', async ({ page }) => {
    const sent = await mockLibrary(page, few);
    await page.goto('/projects/');
    const rail = page.getByRole('navigation', { name: 'Projects' });
    await expect(page.getByRole('heading', { name: 'storefront', exact: true })).toBeVisible();
    await expect(rail.getByRole('button', { name: /storefront/ })).toHaveAttribute('aria-pressed', 'true');
    await rail.getByRole('button', { name: /^api/ }).click();
    await expect(page).toHaveURL(/\?project=api$/);
    await expect(page.getByRole('heading', { name: 'api', exact: true })).toBeVisible();
    expect(sent.sources.map(params => params.get('project'))).toContain('api');
    await page.goto('/projects/');
    await expect(page.getByRole('heading', { name: 'api', exact: true })).toBeVisible();
    await page.goto('/projects/?project=editor-2d#sources');
    await expect(page.getByRole('heading', { name: 'editor-2d', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: /^Sources, / })).toHaveAttribute('aria-selected', 'true');
    const at = few.projects.findIndex(p => p.name === 'editor-2d');
    await page.locator('body').press('J');
    await expect(page.getByRole('heading', { name: few.projects[at + 1].name, exact: true })).toBeVisible();
    await page.locator('body').press('K');
    await page.locator('body').press('K');
    await expect(page.getByRole('heading', { name: few.projects[at - 1].name, exact: true })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`\\?project=${few.projects[at - 1].name}#sources$`));
  });

  test('the overview shows the project\'s profile and opens its sources by kind', async ({ page }) => {
    await mockLibrary(page, few, { profiles: [{ project: 'storefront', text: 'Project profile: storefront\n\nThe shop\'s storefront.', updated: Date.now() / 1000 - 60 }] });
    await page.goto('/projects/?project=storefront');
    await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('region', { name: 'About this project' })).toContainText('The shop\'s storefront.');
    const own = few.sources.filter(row => row.project === 'storefront');
    const documents = own.filter(row => row.kind === 'extracted').length;
    await expect(page.getByRole('heading', { name: 'storefront', exact: true })).toBeVisible();
    await expect(page.getByText(`${n(few.projects[0].count)} memories · ${n(own.length)} sources`)).toBeVisible();
    await page.getByRole('region', { name: 'Sources by kind' }).getByRole('button', { name: /Documents/ }).click();
    await expect(page.getByRole('tab', { name: `Sources, ${own.length}` })).toHaveAttribute('aria-selected', 'true');
    await expect(pager(page)).toContainText(`1–20 of ${n(documents)}`);
    await page.getByRole('tab', { name: 'Overview' }).click();
    await page.getByRole('button', { name: /^layout/ }).click();
    await expect(page).toHaveURL(/\/topics\/\?topic=layout$/);
  });

  test('a large project\'s sources page and search, and the keyboard moves one', async ({ page }) => {
    const sent = await mockLibrary(page, lib);
    await page.goto('/projects/?project=storefront#sources');
    const own = lib.sources.filter(row => row.project === 'storefront');
    const list = page.getByRole('list', { name: 'Sources of storefront' }).getByRole('listitem');
    await expect(pager(page)).toContainText(`1–20 of ${n(own.length)}`);
    await pager(page).getByRole('button', { name: `Page ${Math.ceil(own.length / 20)}` }).click();
    await expect(pager(page)).toContainText(`of ${n(own.length)}`);
    await page.getByRole('searchbox', { name: 'Search' }).fill('handbook');
    await expect(pager(page)).toContainText(`of ${n(own.filter(row => row.source.includes('handbook')).length)}`);
    await list.first().focus();
    await page.keyboard.press('x');
    await expect(page.getByText('1 selected')).toBeVisible();
    await page.keyboard.press('m');
    const dialog = page.getByRole('dialog', { name: 'Move sources to another project' });
    await expect(dialog).toContainText('Moves every memory of 1 source from storefront.');
    await dialog.getByRole('combobox', { name: 'Existing project' }).click();
    await page.getByRole('option', { name: 'api', exact: true }).click();
    await dialog.getByRole('button', { name: 'Review move' }).click();
    await dialog.getByRole('button', { name: 'Move 6 chunks' }).click();
    await expect(dialog).toHaveCount(0);
    expect(sent.moves[1]).toMatchObject({ project: 'storefront', target: 'api', apply: true });
    expect((sent.moves[1].sources as string[])).toHaveLength(1);
    await expect(page.getByText('1 selected')).toHaveCount(0);
  });

  test('at 800x600 the rail is a select and nothing scrolls sideways', async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 600 });
    await mockLibrary(page, few);
    await page.goto('/projects/#sources');
    await expect(page.getByRole('navigation', { name: 'Projects' })).toBeHidden();
    const select = page.getByRole('combobox', { name: 'Project', exact: true });
    await expect(select).toContainText(`storefront (${n(few.projects[0].count)})`);
    await select.click();
    await page.getByRole('option', { name: /^chat-archive/ }).click();
    await expect(select).toContainText('chat-archive');
    await expect(page.getByRole('list', { name: 'Sources of chat-archive' })).toBeVisible();
    // Row actions fold to icons that keep their names.
    await expect(page.getByRole('button', { name: 'Move or merge…' })).toBeVisible();
    const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    expect(scroll).toBeLessThanOrEqual(width);
  });
});
