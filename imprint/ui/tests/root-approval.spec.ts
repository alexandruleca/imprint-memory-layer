import { test, expect, type Page } from '@playwright/test';

const ROOT = '/home/me/other-project';

// Mirrors POST /api/commands: an ingest or refresh outside the workspace's
// roots is refused with 409 root_not_approved until the root is added.
async function mockApi(page: Page, overlaps: string[] = []) {
  const state = { approved: false, commands: [] as unknown[], roots: [] as unknown[] };
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 1, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total_memories: 1, total_facts: 0 } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'alpha', workspaces: ['alpha', 'beta'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [] } }));
  await page.route('**/api/commands/*', async route => {
    state.commands.push({ command: route.request().url().split('/').pop(), body: route.request().postDataJSON() });
    await route.fulfill(state.approved ? { json: { job_id: 'j1', position: 0 } } : { status: 409, json: {
      error: `${ROOT} is not an approved root of workspace 'alpha'. Approve it with imprint workspace roots add <directory>.`,
      code: 'root_not_approved', workspace: 'alpha', root: ROOT, overlaps,
    } });
  });
  await page.route('**/api/workspace/roots', async route => {
    const body = route.request().postDataJSON();
    state.roots.push(body);
    state.approved = true;
    await route.fulfill({ json: { workspace: 'alpha', roots: ['/home/me/notes', ROOT], exclusions: [], overlaps } });
  });
  await page.route('**/api/jobs/j1/stream', route => route.fulfill({
    headers: { 'content-type': 'text/event-stream' },
    body: `data: ${JSON.stringify({ type: 'output', text: 'Refreshed 3 files\n' })}\n\ndata: ${JSON.stringify({ type: 'done', exit_code: 0, status: 'done' })}\n\n`,
  }));
  return state;
}

async function ingestDirectory(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: /Ingest Directory/ }).click();
  await page.getByLabel('Directory', { exact: true }).fill(`${ROOT}/src`);
  await page.getByRole('button', { name: 'Run' }).click();
}

test('an ingest outside the roots asks to approve the root, then runs', async ({ page }) => {
  const state = await mockApi(page);
  await ingestDirectory(page);
  const dialog = page.getByRole('alertdialog', { name: `Approve ${ROOT} as a root of workspace alpha?` });
  await expect(dialog).toBeVisible();
  await expect(dialog).not.toContainText('also a root of');
  await dialog.getByRole('button', { name: 'Approve root' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText('Started now.')).toBeVisible();
  expect(state.roots).toEqual([{ workspace: 'alpha', path: ROOT, action: 'add', allow_overlap: false }]);
  expect(state.commands).toEqual([
    { command: 'ingest', body: { dir: `${ROOT}/src` } },
    { command: 'ingest', body: { dir: `${ROOT}/src` } },
  ]);
});

test('a root another workspace owns is approved only as confirmed sharing', async ({ page }) => {
  const state = await mockApi(page, ['beta']);
  await ingestDirectory(page);
  const dialog = page.getByRole('alertdialog', { name: `Approve ${ROOT} as a root of workspace alpha?` });
  await expect(dialog).toContainText('It is also a root of beta');
  await dialog.getByRole('button', { name: 'Share and approve' }).click();
  await expect(page.getByText('Started now.')).toBeVisible();
  expect(state.roots).toEqual([{ workspace: 'alpha', path: ROOT, action: 'add', allow_overlap: true }]);
});

test('declining leaves the roots alone and reports the refusal', async ({ page }) => {
  const state = await mockApi(page);
  await ingestDirectory(page);
  const dialog = page.getByRole('alertdialog');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('alert').filter({ hasText: `${ROOT} is not an approved root of workspace 'alpha'` })).toBeVisible();
  expect(state.roots).toEqual([]);
  expect(state.commands).toHaveLength(1);
});

test('the commands page shares the approval and streams the retried job', async ({ page }) => {
  const state = await mockApi(page);
  await page.goto('/commands/');
  await page.getByRole('button', { name: /Refresh files/ }).click();
  await page.getByLabel('Directory').fill(ROOT);
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Approve root' }).click();
  await expect(page.getByText('Refreshed 3 files')).toBeVisible();
  expect(state.commands).toEqual([
    { command: 'refresh', body: { dir: ROOT } },
    { command: 'refresh', body: { dir: ROOT } },
  ]);
});
