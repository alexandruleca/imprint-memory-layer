import { test, expect, type Page } from '@playwright/test';

// Shapes follow build_graph_scope (imprint/cli_viz.py).
const project = (name: string, count: number, focus = false) => ({ id: `proj:${name}`, kind: 'project', label: name, count, color: '#4ecdc4', ...(focus ? { focus } : {}) });
const topic = (name: string, count: number) => ({ id: `topic:${name}`, kind: 'topic', label: name, count, color: '#a78bfa' });
const source = (path: string, count: number) => ({ id: `src:${path}`, kind: 'source', label: path.split('/').pop(), fullPath: path, count, color: '#94a3b8' });
const edge = (source: string, target: string, kind: string, weight = 1) => ({ id: `${kind}:${source}->${target}`, source, target, kind, weight });

const scopes: Record<string, object> = {
  root: {
    scope: 'root', center: '',
    nodes: [project('billing-api', 900), project('web-dashboard', 400), topic('auth', 120), topic('caching', 40)],
    edges: [edge('proj:billing-api', 'topic:auth', 'relates', 80), edge('proj:web-dashboard', 'topic:auth', 'relates', 40), edge('proj:billing-api', 'topic:caching', 'relates', 40)],
  },
  'project:billing-api': {
    scope: 'project:billing-api', center: 'proj:billing-api',
    nodes: [project('billing-api', 900, true), topic('auth', 80), source('billing-api/src/auth.ts', 12)],
    edges: [edge('proj:billing-api', 'topic:auth', 'contains', 80), edge('proj:billing-api', 'src:billing-api/src/auth.ts', 'contains', 12)],
  },
};

async function mockGraph(page: Page, scopeData: (scope: string) => object | null = (s) => scopes[s] ?? null) {
  const requested: string[] = [];
  await page.route('**/api/graph?**', async (route) => {
    const scope = new URL(route.request().url()).searchParams.get('scope') ?? 'root';
    requested.push(scope);
    const data = scopeData(scope);
    await route.fulfill(data ? { json: data } : { status: 500, json: { detail: 'graph unavailable' } });
  });
  return requested;
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ping', (route) => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', (route) => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', (route) => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/desktop-learn/history', (route) => route.fulfill({ json: { seen: {}, count: 0 } }));
});

test('legend counts kinds and hides them; find selects a node and opens its scope; back returns', async ({ page }) => {
  const requested = await mockGraph(page);
  await page.goto('/graph/');
  const legend = page.getByRole('list', { name: 'Node kinds' });
  await expect(legend.getByRole('button', { name: /Projects\s*2/ })).toBeVisible();
  await expect(page.getByText('4 nodes · 3 connections')).toBeVisible();

  await legend.getByRole('button', { name: /Topics/ }).click();
  await expect(legend.getByRole('button', { name: /Topics/ })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByText('2 nodes · 0 connections')).toBeVisible();
  await legend.getByRole('button', { name: /Topics/ }).click();

  const find = page.getByLabel('Find in graph');
  await find.fill('billing');
  await expect(page.getByRole('status').filter({ hasText: '0/1' })).toBeVisible();
  await find.press('Enter');
  await expect(page.getByRole('status').filter({ hasText: '1/1' })).toBeVisible();
  const details = page.getByRole('complementary', { name: 'Project details' });
  await expect(details.getByRole('heading', { name: 'billing-api' })).toBeVisible();
  await expect(details.getByText('Project · 900 memories')).toBeVisible();
  await expect(details.getByRole('button', { name: 'auth' })).toBeVisible();

  await details.getByRole('button', { name: 'Open project' }).click();
  await expect(page).toHaveURL(/\?scope=project%3Abilling-api$/);
  const crumbs = page.getByRole('navigation', { name: 'Graph scope' });
  await expect(crumbs.getByRole('button', { name: 'billing-api' })).toHaveAttribute('aria-current', 'page');
  await expect(legend.getByRole('button', { name: /Sources\s*1/ })).toBeVisible();
  expect(requested).toContain('project:billing-api');

  await page.goBack();
  await expect(crumbs.getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
  await expect(legend.getByRole('button', { name: /Projects\s*2/ })).toBeVisible();
});

test('a scope in the URL opens directly, and settings explain where breadth applies', async ({ page }) => {
  await mockGraph(page);
  await page.goto('/graph/?scope=project%3Abilling-api');
  await expect(page.getByRole('navigation', { name: 'Graph scope' }).getByRole('button', { name: 'billing-api' })).toBeVisible();
  await page.getByRole('button', { name: 'Graph settings' }).click();
  await expect(page.getByRole('radio', { name: 'More' })).toBeEnabled();
  await page.getByRole('radio', { name: 'More' }).click();
  await expect(page.getByRole('radio', { name: 'More' })).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('Escape');

  await page.getByRole('navigation', { name: 'Graph scope' }).getByRole('button', { name: 'Overview' }).click();
  await page.getByRole('button', { name: 'Graph settings' }).click();
  await expect(page.getByRole('radio', { name: 'More' })).toBeDisabled();
  await expect(page.getByText('Applies inside a project, topic or source.')).toBeVisible();
});

test('an empty workspace explains how to add memories', async ({ page }) => {
  await mockGraph(page, () => ({ scope: 'root', center: '', nodes: [], edges: [] }));
  await page.goto('/graph/');
  await expect(page.getByText('No memories in this workspace yet')).toBeVisible();
  await expect(page.getByRole('toolbar', { name: 'Graph view' })).toBeHidden();
});

test('a failed load is reported and retried, and the page fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let fail = true;
  await mockGraph(page, (scope) => (fail ? null : scopes[scope]));
  await page.goto('/graph/');
  await expect(page.getByRole('alert', { name: 'Graph error' })).toContainText('The graph could not load');
  fail = false;
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('alert', { name: 'Graph error' })).toHaveCount(0);
  await expect(page.getByText('4 nodes · 3 connections')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
