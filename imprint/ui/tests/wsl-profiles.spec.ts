import { test, expect, type Page } from '@playwright/test';

// Mirrors GET /api/wsl (`imprint wsl list --json` on a Windows runtime) and
// the queued `wsl` commands. The page's code names no target: the section
// shows whatever targets the CLI's client table reports.
const listing = (enrolledCursor: boolean) => ({
  supported: true,
  targets: [
    { name: 'claude-code', label: 'Claude Code' },
    { name: 'cursor', label: 'Cursor' },
    { name: 'cline', label: 'Cline' },
  ],
  distributions: [
    { distribution: 'Ubuntu', home: '/home/me', clients: [
      { name: 'claude-code', profile: '/home/me/.claude', enrolled: true },
      { name: 'cursor', profile: '/home/me/.cursor', enrolled: enrolledCursor },
      { name: 'cline', profile: '', enrolled: false },
    ] },
    { distribution: 'Debian', error: 'WSL Debian is unavailable', clients: [] },
  ],
});

async function mockApi(page: Page, wsl: Record<string, unknown> | (() => Record<string, unknown>)) {
  const state = { probes: 0, posts: [] as unknown[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/fs/roots', route => route.fulfill({ json: { platform: 'Windows', roots: [], wsl: false } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [] } }));
  await page.route('**/api/wsl', route => {
    state.probes++;
    return route.fulfill({ json: typeof wsl === 'function' ? wsl() : wsl });
  });
  await page.route('**/api/commands/wsl', async route => {
    state.posts.push(route.request().postDataJSON());
    await route.fulfill({ json: { job_id: 'w1', position: 0 } });
  });
  await page.route('**/api/jobs/w1/stream', route => route.fulfill({
    headers: { 'content-type': 'text/event-stream' },
    body: `data: ${JSON.stringify({ type: 'output', text: 'Registered for learn\n' })}\n\ndata: ${JSON.stringify({ type: 'done', exit_code: 0, status: 'done' })}\n\n`,
  }));
  return state;
}

const section = (page: Page) => page.locator('#wsl');

test('distributions are probed only when asked, with every target the CLI reports', async ({ page }) => {
  const state = await mockApi(page, listing(false));
  await page.goto('/settings/#wsl');
  await expect(section(page)).toContainText('starts any that are stopped');
  await page.waitForTimeout(300);
  expect(state.probes).toBe(0);

  await section(page).getByRole('button', { name: 'Check distributions' }).click();
  const ubuntu = page.getByRole('region', { name: 'WSL Ubuntu' });
  await expect(ubuntu.getByRole('listitem').filter({ hasText: 'Claude Code' })).toContainText('Enrolled');
  await expect(ubuntu.getByRole('listitem').filter({ hasText: 'Cursor' })).toContainText('Not enrolled');
  await expect(ubuntu.getByRole('listitem').filter({ hasText: 'Cline' })).toContainText('No profile');
  await expect(ubuntu.getByRole('button', { name: 'Enroll Cline in Ubuntu' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'WSL Debian' })).toContainText('WSL Debian is unavailable');
  expect(state.probes).toBe(1);
});

test('enrolling queues wsl setup and shows the new state', async ({ page }) => {
  let enrolled = false;
  const state = await mockApi(page, () => listing(enrolled));
  await page.goto('/settings/#wsl');
  await section(page).getByRole('button', { name: 'Check distributions' }).click();
  enrolled = true;
  await page.getByRole('button', { name: 'Enroll Cursor in Ubuntu' }).click();
  await expect(section(page)).toContainText('Registered for learn');
  await expect(section(page).getByText('Succeeded')).toBeVisible();
  expect(state.posts).toEqual([{ action: 'setup', distribution: 'Ubuntu', target: 'cursor' }]);
  await expect.poll(() => state.probes).toBe(2);
  await expect(page.getByRole('region', { name: 'WSL Ubuntu' }).getByRole('listitem').filter({ hasText: 'Cursor' })).toContainText('Enrolled');
});

test('removing asks first', async ({ page }) => {
  const state = await mockApi(page, listing(false));
  await page.goto('/settings/#wsl');
  await section(page).getByRole('button', { name: 'Check distributions' }).click();
  const remove = page.getByRole('button', { name: 'Remove Claude Code in Ubuntu' });

  await remove.click();
  const dialog = page.getByRole('alertdialog', { name: 'Remove Claude Code in Ubuntu?' });
  await expect(dialog).toContainText('Memories and other profiles stay');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.posts).toEqual([]);

  await remove.click();
  await dialog.getByRole('button', { name: 'Remove' }).click();
  await expect(section(page).getByText('Succeeded')).toBeVisible();
  expect(state.posts).toEqual([{ action: 'remove', distribution: 'Ubuntu', target: 'claude-code' }]);
});

for (const platform of ['Darwin', 'Linux']) {
  test(`${platform} hides WSL navigation, deep links and search results`, async ({ page }) => {
    const state = await mockApi(page, { supported: false, platform });
    await page.route('**/api/fs/roots', route => route.fulfill({ json: { platform, roots: [], wsl: false } }));
    await page.goto('/settings/#wsl');
    await expect(page.getByRole('link', { name: 'Workspaces', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'WSL profiles' })).toHaveCount(0);
    await expect(section(page)).toHaveCount(0);
    await page.getByRole('textbox', { name: 'Filter settings' }).fill('wsl');
    await expect(page.getByText('No matching settings')).toBeVisible();
    expect(state.probes).toBe(0);
  });
}
