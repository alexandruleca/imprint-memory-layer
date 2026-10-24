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

// Chromium hit-tests app-region boxes as rectangles in document order, ignoring
// stacking, and app-region is inherited: anything computing `drag` outside the
// handles, or anything floating above a handle without `no-drag`, moves the
// window instead of taking the click.
test('desktop drag handles never reach dialogs, popovers or their internals', async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { electronAPI: { isElectron: true, platform: 'win32', close() {}, quit() {}, minimize() {}, maximize() {} } });
  });
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 0, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total_memories: 0, total_facts: 0 } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'alpha', workspaces: ['default', 'alpha'] } }));
  await page.route('**/api/workspace/roots?**', route => route.fulfill({ json: { workspace: 'alpha', roots: [], exclusions: [] } }));
  await page.route('**/api/fs/roots', route => route.fulfill({ json: { roots: [{ label: 'Home', path: '/home/me' }], platform: 'Linux', wsl: false } }));
  await page.route('**/api/fs/list**', route => route.fulfill({ json: {
    path: '/home/me', parent: '/home', is_file: false, entries: [],
    crumbs: [{ label: '/', path: '/' }, { label: 'home', path: '/home' }, { label: 'me', path: '/home/me' }],
  } }));
  const region = (selector: string, pseudo?: string) => page.evaluate(([s, p]) =>
    getComputedStyle(document.querySelector(s as string)!, p).getPropertyValue('app-region'), [selector, pseudo]);
  // Only the handles themselves compute `drag`: the top bar's empty space and
  // the sidebar header (logo and traffic lights).
  const strayDrag = () => page.evaluate(() => [...document.querySelectorAll('*')]
    .filter(el => getComputedStyle(el).getPropertyValue('app-region') === 'drag')
    .filter(el => !el.matches('header > div:first-child') && !el.closest('aside > div:first-child'))
    .map(el => el.outerHTML.slice(0, 80)));

  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Workspace: alpha' })).toBeVisible();
  expect(await region('header > div:first-child')).toBe('drag');
  expect(await strayDrag()).toEqual([]);

  await page.getByRole('button', { name: 'Workspace: alpha' }).click();
  const menu = page.getByRole('dialog', { name: 'Switch workspace' });
  await expect(menu).toBeVisible();
  expect(await menu.evaluate(el => getComputedStyle(el).getPropertyValue('app-region'))).toBe('no-drag');
  expect(await strayDrag()).toEqual([]);

  await page.getByRole('button', { name: 'Manage workspaces…' }).click();
  const manager = page.getByRole('dialog', { name: 'Manage workspaces' });
  await expect(manager).toBeVisible();
  expect(await region('dialog[open]')).toBe('no-drag');
  expect(await region('dialog[open]', '::backdrop')).toBe('no-drag');
  expect(await strayDrag()).toEqual([]);

  // The folder picker opens over the manager; Escape closes only the picker.
  await manager.getByRole('button', { name: 'Edit alpha' }).click();
  await manager.getByRole('button', { name: 'Pick folder' }).click();
  const picker = page.getByRole('dialog', { name: 'Pick a root folder' });
  await expect(picker).toBeVisible();
  expect(await picker.evaluate(el => getComputedStyle(el).getPropertyValue('app-region'))).toBe('no-drag');
  expect(await strayDrag()).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(picker).toHaveCount(0);
  await expect(manager).toBeVisible();
});
