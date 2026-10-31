import { test, expect, type Page } from '@playwright/test';

// The Agents page lists the MCP sessions of every client and each one's tool
// calls (ADR 0117).

const now = Date.now() / 1000;
const session = (over: Record<string, unknown>) => ({
  id: 's1', pid: 4242, client: 'claude-code', client_version: '2.1.0', directory: '/home/user/code/app',
  workspace: 'default', started_at: now - 600, last_seen: now - 5, ended_at: null, connected: true,
  calls: 2, errors: 1, last_tool: 'store', ...over,
});
const call = (over: Record<string, unknown>) => ({
  id: 1, session_id: 's1', tool: 'search', started_at: now - 30, duration_ms: 42.5, ok: 1, error: null,
  arguments: '{"query": "caching"}', result_chars: 900, ...over,
});

async function mockApi(page: Page, sessions: unknown[], calls: Record<string, unknown[]> = {}) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/agents?**', route => route.fulfill({ json: {
    sessions,
    clients: [{ client: 'claude-code', sessions: 2, connected: 1, calls: 5, errors: 1, last_seen: now - 5 },
              { client: 'cursor-vscode', sessions: 1, connected: 0, calls: 0, errors: 0, last_seen: now - 7200 }],
  } }));
  await page.route('**/api/agents/*/calls?**', route => {
    const id = new URL(route.request().url()).pathname.split('/')[3];
    route.fulfill({ json: { session: (sessions as { id: string }[]).find(s => s.id === id), calls: calls[id] ?? [] } });
  });
}

test('lists clients and sessions, connected first, with their calls', async ({ page }) => {
  await mockApi(page, [
    session({}),
    session({ id: 's2', client: 'cursor-vscode', client_version: null, directory: '/home/user/code/site', connected: false,
      ended_at: now - 7200, last_seen: now - 7200, calls: 0, errors: 0, last_tool: null }),
  ], {
    s1: [call({ id: 2, tool: 'store', ok: 0, error: 'ValueError: blank content', arguments: '{}' }), call({})],
  });
  await page.goto('/agents/');

  await expect(page.getByRole('listitem', { name: 'Claude Code' })).toContainText('1 connected');
  await expect(page.getByRole('listitem', { name: 'Claude Code' })).toContainText('2 sessions · 5 calls · 1 error');
  await expect(page.getByRole('listitem', { name: 'Cursor' })).toContainText('Last active 2 h ago');

  const rows = page.getByRole('button', { name: /Show history$/ });
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toHaveAccessibleName('Claude Code in app, connected, 2 calls. Show history');
  await expect(rows.nth(1)).toHaveAccessibleName('Cursor in site, disconnected, 0 calls. Show history');

  await rows.first().click();
  const sheet = page.getByRole('dialog');
  await expect(sheet).toContainText('/home/user/code/app');
  await expect(sheet).toContainText('claude-code 2.1.0');
  const calls = sheet.getByRole('list', { name: 'Calls' }).getByRole('listitem');
  await expect(calls).toHaveCount(2);
  await expect(calls.first()).toHaveAccessibleName('store, failed');
  await expect(calls.first()).toContainText('ValueError: blank content');
  await expect(calls.nth(1)).toHaveAccessibleName('search, succeeded');
  await expect(calls.nth(1)).toContainText('{"query": "caching"}');
  await expect(calls.nth(1)).toContainText('43 ms');
});

test('says how sessions appear before any agent has used Imprint', async ({ page }) => {
  await mockApi(page, []);
  await page.goto('/agents/');
  await expect(page.getByText('No agent has used Imprint yet.')).toBeVisible();
});

test('the sidebar links to the Agents page', async ({ page }) => {
  await mockApi(page, []);
  await page.goto('/queue/');
  await page.getByRole('link', { name: 'Agents' }).click();
  await expect(page).toHaveURL(/\/agents\/?$/);
});

test('the sidebar groups pages and remembers which groups are open', async ({ page }) => {
  await mockApi(page, []);
  await page.goto('/queue/');
  const pages = page.getByRole('navigation', { name: 'Pages' });
  const system = pages.getByRole('button', { name: 'System' });
  // Activity holds the open page, so it stays open; System starts collapsed.
  await expect(pages.getByRole('button', { name: 'Activity' })).toBeDisabled();
  await expect(pages.getByRole('link', { name: 'Agents' })).toBeVisible();
  await expect(system).toHaveAttribute('aria-expanded', 'false');
  await expect(pages.getByRole('link', { name: 'Settings' })).toBeHidden();
  await system.click();
  await expect(pages.getByRole('link', { name: 'Settings' })).toBeVisible();
  await pages.getByRole('button', { name: 'Memory' }).click();
  await expect(pages.getByRole('link', { name: 'Graph' })).toBeHidden();
  await page.reload();
  await expect(pages.getByRole('link', { name: 'Settings' })).toBeVisible();
  await expect(pages.getByRole('link', { name: 'Graph' })).toBeHidden();
});
