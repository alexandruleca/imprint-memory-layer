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
  await quit.hover();
  await expect(page.getByText('Quit Imprint and stop its background service')).toBeVisible();
  await quit.click();
  expect(await page.evaluate(() => Reflect.get(window, 'desktopCalls'))).toEqual(['hide', 'quit']);
});

test('browser dashboard has no desktop quit controls', async ({ page }) => {
  await page.goto('/search/');
  await expect(page.getByRole('button', { name: 'Quit Imprint', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Hide window (keep Imprint running)' })).toHaveCount(0);
});

// Chromium hit-tests app-region boxes ahead of the page, ignoring stacking, and
// no-drag boxes drawn over a handle did not reliably carve it out (v0.19.0: the
// Update dialog moved the window instead of taking clicks). So while any
// dialog, menu, popover or select is open the handles stop being drag regions
// (globals.css); a tooltip takes no clicks and leaves them alone.
test('desktop drag handles stand down while an overlay is open', async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { electronAPI: { isElectron: true, platform: 'win32', close() {}, quit() {}, minimize() {}, maximize() {} } });
  });
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 0, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total_memories: 0, total_facts: 0 } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'alpha', workspaces: ['default', 'alpha', 'beta'] } }));
  await page.route('**/api/workspace/roots?**', route => route.fulfill({ json: { workspace: 'alpha', roots: [], exclusions: [] } }));
  await page.route('**/api/update', route => route.fulfill({ json: {
    current: 'v0.19.0', latest: 'v0.20.0', update_available: true, method: 'windows-setup', running: false, log: [],
  } }));
  await page.route('**/api/graph?**', route => route.fulfill({ json: {
    scope: 'root', center: '', edges: [], nodes: [{ id: 'proj:a', kind: 'project', label: 'a', count: 1, color: '#4ecdc4' }],
  } }));
  // The top bar's empty space and the sidebar header (logo and traffic lights).
  const handles = () => page.evaluate(() => ['header > div:first-child', 'aside > div:first-child']
    .map(s => getComputedStyle(document.querySelector(s)!).getPropertyValue('app-region')));
  // app-region is inherited: nothing outside the handles may compute `drag`.
  const strayDrag = () => page.evaluate(() => [...document.querySelectorAll('*')]
    .filter(el => getComputedStyle(el).getPropertyValue('app-region') === 'drag')
    .filter(el => !el.matches('header > div:first-child') && !el.closest('aside > div:first-child'))
    .map(el => el.outerHTML.slice(0, 80)));
  const standsDown = async (overlay: ReturnType<typeof page.getByRole>) => {
    await expect(overlay).toBeVisible();
    await expect.poll(handles).toEqual(['no-drag', 'no-drag']);
    await page.keyboard.press('Escape');
    await expect(overlay).toHaveCount(0);
    await expect.poll(handles).toEqual(['drag', 'drag']);
  };

  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Workspace: alpha' })).toBeVisible();
  expect(await handles()).toEqual(['drag', 'drag']);
  expect(await strayDrag()).toEqual([]);

  // Hint tooltips take no clicks, so the handles keep dragging under them.
  await page.getByRole('button', { name: 'Minimize' }).hover();
  await expect(page.getByText('Minimize', { exact: true })).toBeVisible();
  expect(await handles()).toEqual(['drag', 'drag']);
  await page.mouse.move(600, 400);

  // Dialog: the Update dialog the window used to swallow clicks for.
  await page.getByRole('button', { name: 'Update to v0.20.0' }).click();
  await standsDown(page.getByRole('dialog', { name: 'Update Imprint' }));

  // Menu.
  await page.getByRole('button', { name: 'Workspace: alpha' }).click();
  await standsDown(page.getByRole('menu'));

  // A dialog, the folder picker nested in it and an alert dialog over it.
  await page.getByRole('button', { name: 'Workspace: alpha' }).click();
  await page.getByRole('menuitem', { name: 'Manage workspaces…' }).click();
  const manager = page.getByRole('dialog', { name: 'Manage workspaces' });
  await expect(manager).toBeVisible();
  expect(await handles()).toEqual(['no-drag', 'no-drag']);
  expect(await strayDrag()).toEqual([]);
  await manager.getByRole('button', { name: 'Edit alpha' }).click();
  await manager.getByRole('button', { name: 'Pick folder' }).click();
  const picker = page.getByRole('dialog', { name: 'Pick a root folder' });
  await expect(picker).toBeVisible();
  expect(await handles()).toEqual(['no-drag', 'no-drag']);
  // Escape closes only the picker.
  await page.keyboard.press('Escape');
  await expect(picker).toHaveCount(0);
  await expect(manager).toBeVisible();
  await manager.getByRole('button', { name: 'Wipe beta' }).click();
  const wipe = page.getByRole('alertdialog', { name: "Wipe workspace 'beta'?" });
  await expect(wipe).toBeVisible();
  expect(await handles()).toEqual(['no-drag', 'no-drag']);
  await page.keyboard.press('Escape');
  await expect(wipe).toHaveCount(0);
  await standsDown(manager);

  // Popover.
  await page.goto('/graph/');
  await page.getByRole('button', { name: 'Graph settings' }).click();
  await standsDown(page.getByRole('dialog', { name: 'Graph settings' }));

  // Select: focusing the trigger mounts the closed list, which must not count.
  await page.goto('/search/');
  const type = page.getByRole('combobox', { name: 'Memory type' });
  await type.focus();
  expect(await handles()).toEqual(['drag', 'drag']);
  await type.click();
  await standsDown(page.getByRole('listbox'));
});
