import { test, expect, type Page } from '@playwright/test';
import { clients, commands, mockCatalog } from './command-catalog';

// The Commands page renders the catalog GET /api/commands serves, including
// the clients `imprint clients` reports, and queues what the user runs.
async function mockApi(page: Page) {
  const posts: { command: string; body: unknown }[] = [];
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/commands/*', async route => {
    posts.push({ command: route.request().url().split('/').pop()!, body: route.request().postDataJSON() });
    await route.fulfill({ json: { job_id: 'j1', position: 0 } });
  });
  await page.route('**/api/jobs/j1/stream', route => route.fulfill({
    headers: { 'content-type': 'text/event-stream' },
    body: `data: ${JSON.stringify({ type: 'output', text: 'Registered imprint MCP server\n' })}\n\ndata: ${JSON.stringify({ type: 'done', exit_code: 0, status: 'done' })}\n\n`,
  }));
  return posts;
}

const nav = (page: Page) => page.getByRole('navigation', { name: 'Commands' });

test('lists the catalog in its groups, with client commands', async ({ page }) => {
  await mockApi(page);
  await mockCatalog(page);
  await page.goto('/commands/');
  for (const command of commands) {
    await expect(nav(page).getByRole('button', { name: new RegExp(command.label) })).toBeVisible();
  }
  await expect(nav(page).getByText('Clients', { exact: true })).toBeVisible();
  await expect(nav(page).getByRole('button', { name: /Learn conversations/ })).toHaveAttribute('aria-pressed', 'true');
});

test('connects the chosen client and shows its new state', async ({ page }) => {
  const posts = await mockApi(page);
  const state = { clients };
  const catalog = await mockCatalog(page, state);
  await page.goto('/commands/');
  await nav(page).getByRole('button', { name: /Connect clients/ }).click();
  await expect(page.getByText('imprint connect', { exact: true })).toBeVisible();
  const select = page.getByRole('combobox', { name: 'Client' });
  await expect(select).toHaveText('All clients');
  await select.click();
  await expect(page.getByRole('option', { name: /Claude Code/ })).toContainText('Connected');
  await expect(page.getByRole('option', { name: /Cursor/ })).toContainText('Not connected');
  await page.getByRole('option', { name: /Cursor/ }).click();
  await expect(select).toHaveText('Cursor');

  state.clients = clients.map(c => c.name === 'cursor' ? { ...c, connected: true } : c);
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByText('Registered imprint MCP server')).toBeVisible();
  await expect(page.getByText('Succeeded')).toBeVisible();
  expect(posts).toEqual([{ command: 'connect', body: { target: 'cursor' } }]);
  // The finished job reloads the catalog, so the client reads as connected.
  await expect.poll(() => catalog.loads()).toBe(2);
  await select.click();
  await expect(page.getByRole('option', { name: /Cursor/ })).toContainText(/^Cursor\s*Connected$/);
});

test('disconnecting asks first and runs only once confirmed', async ({ page }) => {
  const posts = await mockApi(page);
  await mockCatalog(page);
  await page.goto('/commands/');
  await nav(page).getByRole('button', { name: /Disconnect clients/ }).click();
  const run = page.getByRole('button', { name: 'Run', exact: true });

  await run.click();
  const dialog = page.getByRole('alertdialog', { name: 'Disconnect clients?' });
  await expect(dialog).toContainText('Memories, the runtime and this dashboard stay');
  await expect(dialog.getByRole('definition')).toHaveText('All clients');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  expect(posts).toEqual([]);

  await page.getByRole('combobox', { name: 'Client' }).click();
  await page.getByRole('option', { name: /Claude Code/ }).click();
  await run.click();
  await expect(dialog.getByRole('definition')).toHaveText('Claude Code');
  await dialog.getByRole('button', { name: 'Disconnect clients' }).click();
  await expect(page.getByText('Succeeded')).toBeVisible();
  expect(posts).toEqual([{ command: 'disconnect', body: { target: 'claude-code' } }]);
});

test('switches and fixed arguments reach the queued body', async ({ page }) => {
  const posts = await mockApi(page);
  await mockCatalog(page);
  await page.goto('/commands/');
  await nav(page).getByRole('button', { name: /Diagnose/ }).click();
  await page.getByRole('switch', { name: 'Start the MCP server' }).click();
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByText('Succeeded')).toBeVisible();
  await nav(page).getByRole('button', { name: /Export snapshot/ }).click();
  await expect(page.getByText('imprint sync export', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts).toEqual([
    { command: 'doctor', body: { probe_mcp: true } },
    { command: 'sync', body: { action: 'export' } },
  ]);
});

test('a catalog that cannot load says why and retries', async ({ page }) => {
  await mockApi(page);
  let fail = true;
  await page.route('**/api/commands', route => fail
    ? route.fulfill({ status: 502, json: { error: 'Could not list clients: imprint clients failed (exit 1)' } })
    : route.fulfill({ json: { commands, clients } }));
  await page.goto('/commands/');
  const alert = page.getByRole('alert').filter({ hasText: 'Commands could not be loaded' });
  await expect(alert).toContainText('imprint clients failed');
  fail = false;
  await alert.getByRole('button', { name: 'Retry' }).click();
  await expect(alert).toHaveCount(0);
  await expect(nav(page).getByRole('button', { name: /Connect clients/ })).toBeVisible();
});

for (const action of ['navigate', 'cancel'] as const) {
  test(`running project analysis ${action === 'navigate' ? 'survives navigation' : 'can be explicitly cancelled'}`, async ({ page }) => {
    await mockApi(page);
    await page.route('**/api/commands', route => route.fulfill({ json: {
      commands: [{ name: 'associate-projects', label: 'Find project associations', group: 'Index', description: 'Analyze sources', fields: [] }], clients,
    } }));
    await page.route('**/api/queue*', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
    await page.route('**/api/jobs/j1/stream', route => route.fulfill({
      headers: { 'content-type': 'text/event-stream' },
      body: 'data: {"type":"output","text":"Project analysis 1/173"}\n\n',
    }));
    const cancellations: string[] = [];
    await page.route('**/api/jobs/j1/cancel', route => {
      cancellations.push(route.request().url());
      return route.fulfill({ json: { ok: true } });
    });
    await page.goto('/commands/');
    await page.getByRole('button', { name: 'Run', exact: true }).click();
    await expect(page.getByText('Project analysis 1/173')).toBeVisible();
    if (action === 'navigate') {
      await page.getByRole('button', { name: 'Open queue' }).click();
      await expect(page).toHaveURL(/\/queue\//);
      expect(cancellations).toEqual([]);
    } else {
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect.poll(() => cancellations.length).toBe(1);
      await expect(page.getByText('Cancelled', { exact: true })).toBeVisible();
    }
  });
}

for (const action of ['navigate', 'cancel'] as const) {
  test(`${action} while queue submission is pending`, async ({ page }) => {
    await mockApi(page);
    await mockCatalog(page);
    await page.route('**/api/queue*', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
    let release!: () => void;
    const submitted = new Promise<void>(resolve => { release = resolve; });
    let received = false;
    await page.route('**/api/commands/learn', async route => {
      received = true;
      await submitted;
      await route.fulfill({ json: { job_id: 'j1', position: 0 } });
    });
    const cancellations: string[] = [];
    await page.route('**/api/jobs/j1/cancel', route => {
      cancellations.push(route.request().url());
      return route.fulfill({ json: { ok: true } });
    });
    await page.goto('/commands/');
    await page.getByRole('button', { name: 'Run', exact: true }).click();
    await expect.poll(() => received).toBe(true);
    if (action === 'navigate') {
      await page.getByRole('button', { name: 'Open queue' }).click();
      await expect(page).toHaveURL(/\/queue\//);
    } else {
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    }
    const response = page.waitForResponse('**/api/commands/learn');
    release();
    await response;
    if (action === 'cancel') await expect.poll(() => cancellations.length).toBe(1);
    else {
      await page.waitForLoadState('networkidle');
      expect(cancellations).toEqual([]);
    }
  });
}
