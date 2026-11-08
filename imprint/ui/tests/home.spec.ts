import { test, expect, type Page } from '@playwright/test';
import { slack } from './slack';

// The Home page reads one summary (GET /api/dashboard) through the
// stale-while-revalidate cache in src/lib/use-cached.ts (ADR 0152).

type Summary = Record<string, unknown>;
const now = () => Date.now() / 1000;
const summary = (over: Summary = {}): Summary => ({
  workspace: 'default', total: 120_481, project_count: 60, source_count: 4_079, topic_count: 400, lang_count: 8,
  domain_count: 40, tag_pending: 0, added_7d: 2_100,
  projects: [['imprint', 29_052], ['checkout-service', 11_635]], topics: [['qdrant', 17_116]], types: [['conversation', 30_352]],
  langs: [['python', 30_203]], domains: [['backend', 22_520]], layers: [['api', 38_191]],
  timeline: [{ month: '2026-10', count: 9_320 }, { month: '2026-11', count: 2_198 }],
  recent: [{ source: 'notes/standup.md', project: 'imprint', type: 'finding', timestamp: now() - 120, memories: 2, title: '' }],
  built_at: now(), age: 0, stale: false, refreshing: false, ...over,
});

async function base(page: Page, workspace = { active: 'default' }) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: workspace.active, workspaces: ['default', 'work'] } }));
}

const hero = (page: Page) => page.getByRole('region', { name: 'Overview' });

test('the overview shows totals, links and what needs attention', async ({ page }) => {
  await base(page);
  await page.route('**/api/dashboard**', route => route.fulfill({ json: summary({ tag_pending: 3_500 }) }));
  await page.goto('/');
  await expect(hero(page).getByText('120,481')).toBeVisible();
  await expect(page.getByRole('link', { name: /Projects\s*60/ })).toHaveAttribute('href', '/projects/');
  await expect(page.getByRole('link', { name: 'imprint: 29,052 memories, 24%' })).toHaveAttribute('href', '/projects/?project=imprint');
  await expect(page.getByText('3,500 memories wait for tagging')).toBeVisible();
  await expect(page.getByRole('table', { name: 'Memories added per month' })).toHaveCount(1);
  await expect(page.getByText('Updated just now')).toBeVisible();
});

test('a reload shows the last summary at once and refreshes it in the background', async ({ page }) => {
  await base(page);
  let total = 100;
  await page.route('**/api/dashboard**', route => route.fulfill({ json: summary({ total }) }));
  await page.goto('/');
  await expect(hero(page).getByText('100', { exact: true })).toBeVisible();

  // The next answer is slow: the cached one shows meanwhile, marked as updating.
  let release: () => void = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  total = 250;
  await page.unroute('**/api/dashboard**');
  await page.route('**/api/dashboard**', async route => { await held; await route.fulfill({ json: summary({ total }) }); });
  await page.reload();
  await expect(hero(page).getByText('100', { exact: true })).toBeVisible();
  await expect(page.getByText('Updating…')).toBeVisible();
  await expect(page.getByRole('status', { name: 'Loading dashboard' })).toHaveCount(0);
  release();
  await expect(hero(page).getByText('250', { exact: true })).toBeVisible();
  await expect(page.getByText('Updated just now')).toBeVisible();
});

test("a summary the server marks stale is asked for again until it is current", async ({ page }) => {
  await base(page);
  const asked: string[] = [];
  await page.route('**/api/dashboard**', route => {
    asked.push(route.request().url());
    const first = asked.length === 1;
    return route.fulfill({ json: summary(first ? { total: 10, stale: true, refreshing: true } : { total: 11 }) });
  });
  await page.goto('/');
  await expect(hero(page).getByText('10', { exact: true })).toBeVisible();
  await expect(page.getByText('Updating…')).toBeVisible();
  await expect(hero(page).getByText('11', { exact: true })).toBeVisible({ timeout: slack(6_000) });
  await expect(page.getByText(/^Updated /)).toBeVisible();
});

test("another workspace's cached summary is never shown", async ({ page }) => {
  const workspace = { active: 'default' };
  await base(page, workspace);
  await page.route('**/api/dashboard**', route => route.fulfill({ json: summary({ workspace: workspace.active, total: workspace.active === 'default' ? 111 : 222 }) }));
  await page.goto('/');
  await expect(hero(page).getByText('111', { exact: true })).toBeVisible();

  // Switched elsewhere (another tab or the CLI); the next load is slow.
  workspace.active = 'work';
  let release: () => void = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.unroute('**/api/dashboard**');
  await page.route('**/api/dashboard**', async route => { await held; await route.fulfill({ json: summary({ workspace: 'work', total: 222 }) }); });
  await page.reload();
  await expect(page.getByRole('status', { name: 'Loading dashboard' })).toBeVisible();
  await expect(page.getByText('111', { exact: true })).toHaveCount(0);
  release();
  await expect(hero(page).getByText('222', { exact: true })).toBeVisible();
});

test('a finished job and the refresh button fetch the summary again', async ({ page }) => {
  await base(page);
  const queue = { active: null as Record<string, unknown> | null, queued: [], recent: [] as Record<string, unknown>[] };
  await page.route('**/api/queue?**', route => route.fulfill({ json: queue }));
  const asked: string[] = [];
  await page.route('**/api/dashboard**', route => {
    asked.push(new URL(route.request().url()).search);
    return route.fulfill({ json: summary({ total: asked.length }) });
  });
  await page.goto('/');
  await expect(hero(page).getByText('1', { exact: true })).toBeVisible();

  queue.recent = [{ id: 'j1', command: 'ingest', label: 'Ingest folder', status: 'done', body: {}, created_at: now(), started_at: now(), ended_at: now() }];
  await expect(hero(page).getByText('2', { exact: true })).toBeVisible({ timeout: slack(8_000) });

  await page.getByRole('button', { name: 'Refresh dashboard' }).click();
  await expect(hero(page).getByText('3', { exact: true })).toBeVisible();
  expect(asked.at(-1)).toBe('?fresh=1');
});

test('the phone layout keeps the overview inside the screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await base(page);
  await page.route('**/api/dashboard**', route => route.fulfill({ json: summary() }));
  await page.goto('/');
  await expect(hero(page).getByText('120,481')).toBeVisible();
  const overflow = await page.locator('#main-content').evaluate(main => main.scrollWidth - main.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
