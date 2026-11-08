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

/** A node's position on the page, once the view has laid out and framed its graph. */
async function nodePoint(page: Page, id: string) {
  const view = page.locator('[data-graph-view][data-ready="true"]:not([data-nodes="0"])');
  await expect(view).toBeVisible({ timeout: 15_000 });
  const box = (await view.boundingBox())!;
  const at = await view.evaluate((el, nodeId) => (el as HTMLElement & { graphDebug: { screenOf(id: string): [number, number] | null } }).graphDebug.screenOf(nodeId), id);
  expect(at).not.toBeNull();
  return { x: box.x + at![0], y: box.y + at![1] };
}

test('the canvas shows a tooltip on hover, selects on click and opens on double-click', async ({ page }) => {
  await mockGraph(page);
  await page.goto('/graph/');
  const point = await nodePoint(page, 'proj:billing-api');
  await page.mouse.move(point.x, point.y);
  await expect(page.getByRole('tooltip')).toContainText('billing-api');
  await expect(page.getByRole('tooltip')).toContainText('Double-click to open');
  await page.mouse.click(point.x, point.y);
  await expect(page.getByRole('complementary', { name: 'Project details' }).getByRole('heading', { name: 'billing-api' })).toBeVisible();
  await page.mouse.dblclick(point.x, point.y);
  await expect(page).toHaveURL(/\?scope=project%3Abilling-api$/);
});

test('the star view is a toolbar toggle that is remembered, and old saved settings still load', async ({ page }) => {
  // Settings saved before the star view existed have no mode.
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('seeded')) {
      localStorage.setItem('imprint:graph:v2', JSON.stringify({ settings: { showLabels: false, depth: 2 }, hidden: ['topic'] }));
      sessionStorage.setItem('seeded', '1');
    }
  });
  await mockGraph(page);
  await page.goto('/graph/');
  await expect(page.locator('[data-graph-view="graph"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Graph view' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('2 nodes · 0 connections')).toBeVisible();
  await page.getByRole('button', { name: 'Graph settings' }).click();
  await expect(page.getByRole('switch', { name: 'Labels' })).not.toBeChecked();
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Star view' }).click();
  const stars = page.locator('[data-graph-view="stars"]');
  await expect(stars).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
  await expect(stars).toHaveAttribute('data-renderer', /^webgl/);
  await expect(page.getByRole('button', { name: 'Star view' })).toHaveAttribute('aria-pressed', 'true');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('imprint:graph:v2') || '{}'));
  expect(saved.settings).toMatchObject({ mode: 'stars', showLabels: false, depth: 2 });
  expect(saved.hidden).toEqual(['topic']);

  await page.reload();
  await expect(page.locator('[data-graph-view="stars"]')).toBeVisible();
  await page.getByRole('button', { name: 'Graph view' }).click();
  await expect(page.locator('[data-graph-view="graph"]')).toBeVisible();
  await expect(page.locator('[data-graph-view="stars"]')).toHaveCount(0);
});

test('stars show tooltips, select, open and light up find matches', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('imprint:graph:v2', JSON.stringify({ settings: { mode: 'stars' } })));
  await mockGraph(page);
  await page.goto('/graph/');
  const point = await nodePoint(page, 'proj:web-dashboard');
  await page.mouse.move(point.x, point.y);
  await expect(page.getByRole('tooltip')).toContainText('web-dashboard');
  await page.mouse.click(point.x, point.y);
  const details = page.getByRole('complementary', { name: 'Project details' });
  await expect(details.getByRole('heading', { name: 'web-dashboard' })).toBeVisible();

  await page.getByLabel('Find in graph').fill('auth');
  await page.getByLabel('Find in graph').press('Enter');
  await expect(page.getByRole('complementary', { name: 'Topic details' }).getByRole('heading', { name: 'auth' })).toBeVisible();

  // Finding moved the camera to the match: clear the find and the
  // selection (whose panel may cover the star), fit the view and look for
  // the star again.
  await page.getByLabel('Find in graph').press('Escape');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('complementary', { name: /details$/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Fit to view' }).click();
  const moved = await nodePoint(page, 'proj:web-dashboard');
  await page.mouse.dblclick(moved.x, moved.y);
  await expect(page).toHaveURL(/\?scope=project%3Aweb-dashboard$/);
});

test('with reduced motion the star field stands still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(() => localStorage.setItem('imprint:graph:v2', JSON.stringify({ settings: { mode: 'stars' } })));
  await mockGraph(page);
  await page.goto('/graph/');
  await expect(page.locator('[data-graph-view="stars"]')).toHaveAttribute('data-motion', 'reduced');
  await expect(page.locator('[data-graph-view="stars"]')).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
});

test('without WebGL the star view falls back to the graph and says why', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    // @ts-expect-error -- narrowing the overloads is beside the point here
    HTMLCanvasElement.prototype.getContext = function (type: string, ...rest: unknown[]) {
      return /webgl/.test(type) ? null : original.call(this, type, ...rest);
    };
  });
  await mockGraph(page);
  await page.goto('/graph/');
  await page.getByRole('button', { name: 'Star view' }).click();
  await expect(page.getByText('The star view needs WebGL')).toBeVisible();
  await expect(page.locator('[data-graph-view="graph"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Graph view' })).toHaveAttribute('aria-pressed', 'true');
});

test('a large scope stays responsive: labels are culled, find still reaches every node, and capped kinds offer more', async ({ page }) => {
  const nodes = [project('hub', 5000, true)];
  const edges: ReturnType<typeof edge>[] = [];
  for (let i = 0; i < 1500; i++) {
    nodes.push(topic(`node-${i}`, 1 + (i % 50)));
    edges.push(edge('proj:hub', `topic:node-${i}`, 'contains', 1 + (i % 50)));
  }
  const requested: string[] = [];
  await page.route('**/api/graph?**', async (route) => {
    requested.push(new URL(route.request().url()).searchParams.get('depth') ?? '');
    await route.fulfill({ json: { scope: 'project:hub', center: 'proj:hub', nodes, edges, more: ['topic'] } });
  });
  await page.goto('/graph/?scope=project%3Ahub');
  await expect(page.getByText('1501 nodes · 1500 connections')).toBeVisible();
  const view = page.locator('[data-graph-view="graph"][data-nodes="1501"]');
  await expect(view).toHaveAttribute('data-ready', 'true', { timeout: 20_000 });
  const labels = await view.evaluate((el) => (el as HTMLElement & { graphDebug: { labels: number } }).graphDebug.labels);
  expect(labels).toBeGreaterThan(0);
  expect(labels).toBeLessThan(1501);

  await page.getByLabel('Find in graph').fill('node-1234');
  await expect(page.getByRole('status').filter({ hasText: '0/1' })).toBeVisible();
  await page.getByLabel('Find in graph').press('Enter');
  await expect(page.getByRole('complementary', { name: 'Topic details' }).getByRole('heading', { name: 'node-1234' })).toBeVisible();
  await page.getByLabel('Find in graph').press('Escape');
  await page.keyboard.press('Escape');

  await expect(page.getByText('Largest topics only.')).toBeVisible();
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect.poll(() => requested.at(-1)).toBe('2');
});

// Search hits as /api/search returns them (imprint/api.py, MemoryNode).
const hit = (i: number, project: string, source: string, topics: string[]) => ({
  id: `m${i}`, label: `Hit number ${i}`, project, type: 'finding', source, chunk_index: i,
  tags: { lang: 'en', layer: '', kind: '', domain: [], topics }, content: `Excerpt of hit ${i} about caching.`, match_type: 'hybrid',
});
const hits = [
  hit(1, 'billing-api', 'billing-api/src/cache.ts', ['caching']),
  hit(2, 'billing-api', 'billing-api/src/cache.ts', ['caching', 'auth']),
  hit(3, 'web-dashboard', 'conv/web/7.jsonl', ['caching']),
];

async function mockSearch(page: Page) {
  const asked: URLSearchParams[] = [];
  await page.route('**/api/search?**', async (route) => {
    asked.push(new URL(route.request().url()).searchParams);
    await route.fulfill({ json: { nodes: hits } });
  });
  return asked;
}

test('a query with no names on screen searches all memories as its own scope, linked both ways to its results', async ({ page }) => {
  await mockGraph(page);
  const asked = await mockSearch(page);
  await page.goto('/graph/');
  await expect(page.getByText('4 nodes · 3 connections')).toBeVisible();

  const find = page.getByLabel('Find in graph');
  await find.fill('cache invalidation');
  await expect(page.getByRole('status').filter({ hasText: 'No matches' })).toBeVisible();
  await find.press('Enter');
  await expect(page).toHaveURL(/\?scope=search%3Acache\+invalidation$/);
  expect(asked.at(-1)?.get('q')).toBe('cache invalidation');
  await expect(page.getByRole('navigation', { name: 'Graph scope' }).getByRole('button', { name: '“cache invalidation”' })).toHaveAttribute('aria-current', 'page');

  const results = page.getByRole('region', { name: 'Search results' });
  await expect(results.getByRole('status')).toHaveText('3 results for “cache invalidation”');
  const rows = results.getByRole('listitem');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText('Hit number 1');
  await expect(rows.nth(0)).toContainText('billing-api · cache.ts · #1');
  // Hits, their two sources and projects, and their topics.
  await expect(page.getByText('9 nodes · 9 connections')).toBeVisible();

  await rows.nth(1).getByRole('button').click();
  await expect(page.getByRole('complementary', { name: 'Chunk details' }).getByRole('heading', { name: 'Hit number 2' })).toBeVisible();
  await expect(rows.nth(1).getByRole('button')).toHaveAttribute('aria-current', 'true');

  // A node picked on the canvas marks its result.
  const point = await nodePoint(page, 'chunk:m3');
  await page.mouse.click(point.x, point.y);
  await expect(rows.nth(2).getByRole('button')).toHaveAttribute('aria-current', 'true');
  await expect(rows.nth(1).getByRole('button')).not.toHaveAttribute('aria-current', 'true');

  // Double-clicking a result reads it, as double-clicking its node does.
  await rows.nth(0).getByRole('button').dblclick();
  await expect(page.getByRole('dialog').getByRole('heading', { name: 'Hit number 1' })).toBeVisible();
  await page.keyboard.press('Escape');

  await page.goBack();
  await expect(page.getByRole('navigation', { name: 'Graph scope' }).getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('region', { name: 'Search results' })).toHaveCount(0);
});

test('Ctrl+Enter searches even when names match, and a search URL keeps its filters', async ({ page }) => {
  await mockGraph(page);
  const asked = await mockSearch(page);
  await page.goto('/graph/');
  const find = page.getByLabel('Find in graph');
  await find.fill('billing');
  await expect(page.getByRole('status').filter({ hasText: '0/1' })).toBeVisible();
  await find.press('Enter');
  await expect(page.getByRole('status').filter({ hasText: '1/1' })).toBeVisible();
  await find.press('Control+Enter');
  await expect(page).toHaveURL(/\?scope=search%3Abilling$/);

  await page.goto('/graph/?scope=search%3Aauth&project=billing-api&type=decision&mode=semantic');
  await expect(page.getByRole('region', { name: 'Search results' }).getByRole('status')).toHaveText('3 results for “auth”');
  const last = asked.at(-1)!;
  expect([last.get('q'), last.get('project'), last.get('type'), last.get('mode')]).toEqual(['auth', 'billing-api', 'decision', 'semantic']);
  await expect(page.getByRole('link', { name: 'Open on the Search page' })).toHaveAttribute('href', '/search/?q=auth&project=billing-api&type=decision&mode=semantic');
});

test('in the star view a search lights its hits', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('imprint:graph:v2', JSON.stringify({ settings: { mode: 'stars' } })));
  await mockGraph(page);
  await mockSearch(page);
  await page.goto('/graph/?scope=search%3Acaching');
  await expect(page.locator('[data-graph-view="stars"]')).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
  const point = await nodePoint(page, 'chunk:m1');
  await page.mouse.move(point.x, point.y);
  await expect(page.getByRole('tooltip')).toContainText('Hit number 1');
  await page.getByRole('region', { name: 'Search results' }).getByRole('listitem').nth(2).getByRole('button').click();
  await expect(page.getByRole('complementary', { name: 'Chunk details' }).getByRole('heading', { name: 'Hit number 3' })).toBeVisible();
});

test('the Search page opens its query in the graph, and the graph links back', async ({ page }) => {
  await mockGraph(page);
  const asked = await mockSearch(page);
  await page.goto('/search/');
  await page.getByLabel('Search query').fill('caching');
  await page.getByLabel('Filter by project').fill('billing-api');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('3 results for “caching”')).toBeVisible();
  await page.getByRole('link', { name: 'Open in graph' }).click();
  await expect(page).toHaveURL(/\/graph\/\?scope=search%3Acaching&project=billing-api$/);
  await expect(page.getByRole('region', { name: 'Search results' }).getByRole('listitem')).toHaveCount(3);
  expect(asked.at(-1)?.get('project')).toBe('billing-api');

  await page.getByRole('link', { name: 'Open on the Search page' }).click();
  await expect(page).toHaveURL(/\/search\/\?q=caching&project=billing-api$/);
  await expect(page.getByLabel('Search query')).toHaveValue('caching');
  await expect(page.getByText('3 results for “caching”')).toBeVisible();
});

test('the canvas follows the theme on <html>', async ({ page }) => {
  await mockGraph(page);
  await page.goto('/graph/');
  const view = page.locator('[data-graph-view="graph"][data-ready="true"]:not([data-nodes="0"])');
  await expect(view).toBeVisible();
  const dark = () => view.evaluate((el) => (el as HTMLElement & { graphDebug: { dark?: boolean } }).graphDebug.dark);
  await page.evaluate(() => document.documentElement.classList.remove('dark'));
  await expect.poll(dark).toBe(false);
  await page.evaluate(() => document.documentElement.classList.add('dark'));
  await expect.poll(dark).toBe(true);
  await page.evaluate(() => document.documentElement.classList.remove('dark'));
  await expect.poll(dark).toBe(false);
});
