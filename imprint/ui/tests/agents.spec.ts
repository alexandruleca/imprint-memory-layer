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

type Session = ReturnType<typeof session>;
const PAGE = 50;

/** The API of ADR 0151: running sessions on the first page, the rest paged
 * by (last_seen, id) with a cursor, filtered by client and text. */
async function mockApi(page: Page, sessions: Session[], calls: Record<string, unknown[]> = {}) {
  const requests: URL[] = [];
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/agents?**', route => {
    const url = new URL(route.request().url());
    requests.push(url);
    const limit = Number(url.searchParams.get('limit') ?? PAGE);
    const before = url.searchParams.get('before');
    const client = url.searchParams.get('client');
    const q = url.searchParams.get('q')?.toLowerCase();
    const match = (s: Session) => (!client || (s.client ?? 'Unknown client') === client)
      && (!q || [s.directory, s.client, s.workspace].some(v => v?.toLowerCase().includes(q)));
    const order = (a: Session, b: Session) => b.last_seen - a.last_seen || (a.id < b.id ? 1 : -1);
    let history = sessions.filter(s => !s.connected && match(s)).sort(order);
    const total = history.length;
    if (before) {
      const [seen, id] = before.split('|');
      history = history.filter(s => s.last_seen < Number(seen) || (s.last_seen === Number(seen) && s.id < id));
    }
    const shown = history.slice(0, limit);
    const last = shown.at(-1);
    route.fulfill({ json: {
      running: before ? [] : sessions.filter(s => s.connected && match(s)),
      sessions: shown,
      next: history.length > limit && last ? `${last.last_seen}|${last.id}` : null,
      total,
      clients: [{ client: 'claude-code', sessions: 2, connected: 1, calls: 5, errors: 1, last_seen: now - 5 },
                { client: 'cursor-vscode', sessions: 1, connected: 0, calls: 0, errors: 0, last_seen: now - 7200 }],
    } });
  });
  await page.route('**/api/agents/*/calls?**', route => {
    const id = new URL(route.request().url()).pathname.split('/')[3];
    route.fulfill({ json: { session: sessions.find(s => s.id === id), calls: calls[id] ?? [] } });
  });
  return requests;
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

  await expect(page.getByRole('listitem', { name: 'Claude Code' })).toContainText('1 running');
  await expect(page.getByRole('listitem', { name: 'Claude Code' })).toContainText('2 sessions · 5 calls · 1 error');
  await expect(page.getByRole('listitem', { name: 'Cursor' })).toContainText('Last active 2 h ago');

  const running = page.getByRole('region', { name: /Running now/ }).getByRole('button', { name: /Show calls$/ });
  await expect(running).toHaveCount(1);
  await expect(running).toHaveAccessibleName('app, Claude Code, running, 2 calls, 1 error. Show calls');
  const history = page.getByRole('region', { name: /History/ }).getByRole('button', { name: /Show calls$/ });
  await expect(history).toHaveAccessibleName('site, Cursor, finished, 0 calls. Show calls');
  const rows = page.getByRole('button', { name: /Show calls$/ });

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

test.describe('session history', () => {
  // Wednesday 18 November 2026, noon in Bucharest; en-GB weeks start on Monday.
  test.use({ timezoneId: 'Europe/Bucharest', locale: 'en-GB' });
  const noon = new Date('2026-11-18T12:00:00+02:00').getTime() / 1000;
  const at = (iso: string) => new Date(iso).getTime() / 1000;
  const ended = (id: string, seen: number, over: Record<string, unknown> = {}) => session({
    id, connected: false, started_at: seen - 600, last_seen: seen, ended_at: seen, calls: 3, errors: 0, ...over });

  test('groups finished sessions by local calendar periods, newest first', async ({ page }) => {
    await page.clock.setFixedTime(noon * 1000);
    await mockApi(page, [
      session({ id: 'live', last_seen: noon - 5, started_at: noon - 600 }),
      ended('today', at('2026-11-18T00:30:00+02:00')),                 // Just after local midnight
      ended('yesterday', at('2026-11-17T23:50:00+02:00')),
      ended('monday', at('2026-11-16T08:00:00+02:00')),
      ended('last-week', at('2026-11-15T23:00:00+02:00')),             // Sunday before
      ended('last-week-2', at('2026-11-09T07:00:00+02:00')),
      ended('this-month', at('2026-11-02T10:00:00+02:00')),
      ended('last-month', at('2026-10-31T23:59:00+02:00')),
      ended('september', at('2026-09-10T10:00:00+03:00')),
      ended('last-year', at('2025-12-24T10:00:00+02:00')),
    ]);
    await page.goto('/agents/');

    const history = page.getByRole('region', { name: /^History/ });
    const headings = history.getByRole('heading', { level: 3 });
    await expect(headings).toHaveText(['Today1', 'Yesterday1', 'This week1', 'Last week2', 'This month1',
      'Last month1', 'September1', 'December 20251']);
    const today = page.getByRole('region', { name: /^Today/ });
    await expect(today.getByRole('button')).toHaveAccessibleName(/^app, Claude Code, finished/);
    await expect(today.locator('time')).toHaveText('00:30');
    await expect(page.getByRole('region', { name: /^Last week/ }).locator('time').first()).toHaveText('Sun 23:00');
    await expect(page.getByRole('region', { name: /^September/ }).locator('time')).toHaveText('10 Sept');
    // The full moment on hover.
    await expect(today.locator('time')).toHaveAttribute('title', /18 Nov 2026/);
    // Running sessions stay out of the history, above it.
    await expect(page.getByRole('region', { name: /Running now/ }).getByRole('button')).toHaveCount(1);
    await expect(history.getByText('10 sessions')).toHaveCount(0);
    await expect(history.getByText('9 sessions')).toBeVisible();
  });

  test('pages older sessions in, and polling keeps them and adds new ones on top', async ({ page }) => {
    // Without IntersectionObserver the button is the way to the next page.
    await page.addInitScript(() => { delete (window as { IntersectionObserver?: unknown }).IntersectionObserver; });
    await page.clock.setFixedTime(noon * 1000);
    const sessions = Array.from({ length: 120 }, (_, i) =>
      ended(`s${String(i).padStart(3, '0')}`, noon - 3600 * (i + 1), { directory: `/code/project-${i}` }));
    const requests = await mockApi(page, sessions);
    await page.goto('/agents/');

    const rows = page.getByRole('region', { name: /^History/ }).getByRole('button', { name: /Show calls$/ });
    await expect(rows).toHaveCount(50);
    await expect(page.getByText('Showing 50 of 120')).toBeVisible();

    await page.getByRole('button', { name: 'Load older sessions' }).click();
    await expect(rows).toHaveCount(100);
    expect(requests.some(u => u.searchParams.get('before') === `${noon - 3600 * 50}|s049`)).toBe(true);

    // A session ends meanwhile; the next poll puts it on top and keeps the loaded pages.
    sessions.push(ended('fresh', noon - 60, { directory: '/code/fresh' }));
    await expect(rows.first()).toHaveAccessibleName(/^fresh,/, { timeout: 8000 });
    await expect(rows).toHaveCount(101);
    await expect(rows.nth(100)).toHaveAccessibleName(/^project-99,/);

    await page.getByRole('button', { name: 'Load older sessions' }).click();
    await expect(rows).toHaveCount(121);
    await expect(page.getByText('That is every recorded session.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Load older sessions' })).toHaveCount(0);
  });

  test('scrolling to the end of the history loads the next page by itself', async ({ page }) => {
    await page.clock.setFixedTime(noon * 1000);
    await mockApi(page, Array.from({ length: 120 }, (_, i) => ended(`s${String(i).padStart(3, '0')}`, noon - 3600 * (i + 1))));
    await page.goto('/agents/');
    const rows = page.getByRole('region', { name: /^History/ }).getByRole('button', { name: /Show calls$/ });
    await expect(rows).toHaveCount(50);
    await page.locator('main').evaluate(main => main.scrollTo(0, main.scrollHeight));
    await expect(rows).toHaveCount(100);
    await expect(page.getByText('Showing 100 of 120')).toBeVisible();
  });

  test('filters by client and by text, and says when nothing matches', async ({ page }) => {
    await page.clock.setFixedTime(noon * 1000);
    const requests = await mockApi(page, [
      ended('a', noon - 100, { directory: '/code/site', client: 'cursor-vscode' }),
      ended('b', noon - 200, { directory: '/code/app' }),
    ]);
    await page.goto('/agents/');
    const rows = page.getByRole('region', { name: /^History/ }).getByRole('button', { name: /Show calls$/ });
    await expect(rows).toHaveCount(2);

    const cursor = page.getByRole('listitem', { name: 'Cursor' }).getByRole('button');
    await cursor.click();
    await expect(cursor).toHaveAttribute('aria-pressed', 'true');
    await expect(rows).toHaveCount(1);
    await expect(rows).toHaveAccessibleName(/^site, Cursor/);
    expect(requests.at(-1)?.searchParams.get('client')).toBe('cursor-vscode');
    await cursor.click();
    await expect(rows).toHaveCount(2);

    await page.getByRole('textbox', { name: /Filter sessions/ }).fill('nowhere');
    await expect(page.getByText('No finished sessions match')).toBeVisible();
    expect(requests.at(-1)?.searchParams.get('q')).toBe('nowhere');
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await expect(rows).toHaveCount(2);
  });

  test('a failed first load offers a retry', async ({ page }) => {
    await mockApi(page, []);
    let fail = true;
    await page.route('**/api/agents?**', route => (fail ? route.fulfill({ status: 500, json: { error: 'Database is locked' } }) : route.fallback()));
    await page.goto('/agents/');
    await expect(page.getByRole('alert').filter({ hasText: 'Could not load agents' })).toContainText('Database is locked');
    fail = false;
    await page.getByRole('button', { name: 'Retry' }).click();
    await expect(page.getByText('No agent has used Imprint yet.')).toBeVisible();
  });
});
