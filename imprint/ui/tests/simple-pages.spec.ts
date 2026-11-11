import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// What the simple dashboard leaves out of the pages it shares with the
// advanced one (ADR 0182). Each test loads the same page in both.

const simple = (page: Page) => base(page, { mode: 'simple', tutorials: false });
const advanced = (page: Page) => base(page, { mode: 'advanced', tutorials: false });

const memory = {
  id: '1234567890abcdef', label: 'Where we stay in Lisbon', content: 'The hotel near the river, booked for May.',
  project: 'trip-planning', source: 'chat/lisbon.jsonl', type: 'decision', chunk_index: 0,
  tags: { lang: 'markdown', layer: 'docs', kind: 'source', domain: [], topics: [] },
  similarity: .76, match_type: 'hybrid', source_hash: 'abc1234567890', source_mtime: 1700000000,
};

for (const mode of ['simple', 'advanced'] as const) {
  test(`${mode}: Search ${mode === 'simple' ? 'keeps the project filter and plain results' : 'shows every filter and ranking detail'}`, async ({ page }) => {
    await (mode === 'simple' ? simple : advanced)(page);
    await page.route('**/api/search?**', route => route.fulfill({ json: { nodes: [memory] } }));
    await page.goto('/search/');
    await expect(page.getByLabel('Filter by project')).toBeVisible();
    const technical = mode === 'simple' ? 0 : 1;
    await expect(page.getByRole('combobox', { name: 'Memory type' })).toHaveCount(technical);
    await expect(page.getByRole('combobox', { name: 'Search method' })).toHaveCount(technical);
    await page.getByLabel('Search query', { exact: true }).fill('lisbon');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '1 result' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Open in graph' })).toHaveCount(technical);
    await expect(page.getByText('Content fingerprint:')).toHaveCount(technical);
    await expect(page.getByText('Semantic similarity:')).toHaveCount(technical);
    await expect(page.getByText(mode === 'simple' ? 'Last changed:' : 'Source modified:')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open full memory' })).toBeVisible();
  });

  test(`${mode}: Reminders ${mode === 'simple' ? 'leaves scheduled jobs out' : 'offers scheduled jobs'}`, async ({ page }) => {
    await (mode === 'simple' ? simple : advanced)(page);
    await page.route('**/api/schedule**', route => route.fulfill({ json: { entries: [], total: 0, next: null } }));
    await page.goto('/reminders/');
    await page.getByRole('button', { name: 'New' }).click();
    const kinds = page.getByRole('combobox', { name: 'Kind' }).getByRole('option');
    await expect(kinds).toHaveText(mode === 'simple' ? ['Reminder', 'Timer', 'Question for Imprint'] : ['Reminder', 'Timer', 'Scheduled job', 'Question for Imprint']);
  });
}

const core = (memories: unknown[]) => ({ memories, budget: 3000, used: 120, kinds: ['preference', 'instruction', 'fact'], max_text: 500 });
const guideline = {
  id: 'g1', text: 'Prefers short answers.', kind: 'preference', scope: '*', state: 'active', tier: 'guideline', amends: '', covers: [],
  conflicts: [], evidence: [], origin: 'user', created_at: 1700000000, updated_at: 1700000000, approved_at: 1700000000, device: 'laptop',
};

test('simple: Core memories is About you, with three plain tabs and no session budget', async ({ page }) => {
  await simple(page);
  await page.route('**/api/core', route => route.fulfill({ json: core([guideline]) }));
  await page.goto('/core/');
  await expect(page.getByRole('heading', { name: 'About you', level: 1 })).toBeVisible();
  await expect(page.getByRole('tab')).toHaveText([/To settle/, /Known about you/, /Suggested/]);
  await expect(page.getByRole('progressbar', { name: 'Session budget used' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tell Imprint something' })).toBeVisible();
  await expect(page.getByText('Prefers short answers.')).toBeVisible();
  // A link to a tab the simple dashboard does not list still opens it.
  await page.goto('/core/#recorded');
  await expect(page.getByRole('tab', { name: /Recorded/ })).toHaveAttribute('aria-selected', 'true');
});

test('advanced: Core memories keeps its five tabs and the session budget', async ({ page }) => {
  await advanced(page);
  await page.route('**/api/core', route => route.fulfill({ json: core([guideline]) }));
  await page.goto('/core/');
  await expect(page.getByRole('heading', { name: 'Core memories', level: 1 })).toBeVisible();
  await expect(page.getByRole('tab')).toHaveCount(5);
  await expect(page.getByRole('progressbar', { name: 'Session budget used' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Write a guideline/ })).toBeVisible();
});

test('simple: Sync is Devices, pairing by a code, without relays or snapshots', async ({ page }) => {
  await simple(page);
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: [] } }));
  await page.goto('/sync/');
  await expect(page.getByRole('heading', { name: 'Devices' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Paired devices' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Snapshots' })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Connect a device' }).click();
  await expect(page.getByRole('tab', { name: 'Show a code' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Enter a code' })).toBeVisible();
  await expect(page.getByLabel(/^Relay/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Show a code' })).toBeVisible();
  // The address of the snapshots still opens them.
  await page.goto('/sync/#snapshots');
  await expect(page.getByRole('tab', { name: 'Snapshots' })).toHaveAttribute('aria-selected', 'true');
});

test('advanced: Sync keeps the relay and snapshots', async ({ page }) => {
  await advanced(page);
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: [] } }));
  await page.goto('/sync/');
  await expect(page.getByRole('heading', { name: 'Sync' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Snapshots' })).toBeVisible();
  await page.getByRole('tab', { name: 'Live sync' }).click();
  await expect(page.getByLabel(/^Relay/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start Serving' })).toBeVisible();
});

const project = (name: string, count: number) => ({ id: `proj_${name}`, name, count, types: {}, color: '#4ecdc4', topDomains: [], topLangs: [] });
async function library(page: Page) {
  await page.route('**/api/{overview,projects}', route => route.fulfill({ json: { projects: [project('trip-planning', 7)], total: 7, version: 1, facets: {} } }));
  await page.route('**/api/project/*', route => route.fulfill({ json: {
    project: 'trip-planning', count: 7, color: '#4ecdc4', types: [{ name: 'conversation', count: 5 }], domains: [{ name: 'travel', count: 4 }],
    langs: [{ name: 'markdown', count: 2 }], topics: [{ name: 'flights', count: 3 }],
  } }));
  await page.route('**/api/sources?**', route => route.fulfill({ json: { sources: [
    { source: 'trip-planning/itinerary.pdf', chunks: 5 }, { source: 'trip-planning/packing.md', chunks: 2 },
  ], total: 2 } }));
}

test('simple: Projects is the Library, for reading and moving', async ({ page }) => {
  await simple(page);
  await library(page);
  await page.goto('/projects/');
  await expect(page.getByRole('heading', { name: 'Library', level: 1 })).toBeVisible();
  await expect(page.getByRole('button', { name: /Project associations/ })).toHaveCount(0);
  const open = page.getByRole('region', { name: 'trip-planning' });
  await expect(open.getByText('2 documents and conversations').first()).toBeVisible();
  await expect(open.getByRole('button', { name: 'Graph' })).toHaveCount(0);
  await expect(open.getByRole('button', { name: /Copy to workspace/ })).toHaveCount(0);
  await expect(open.getByRole('button', { name: /Move or merge/ })).toBeVisible();
  await expect(open.getByRole('heading', { name: 'What it is about' })).toBeVisible();
  await expect(open.getByRole('heading', { name: 'Languages' })).toHaveCount(0);

  await open.getByRole('tab', { name: /Documents and conversations/ }).click();
  const row = open.getByRole('listitem').filter({ hasText: 'itinerary.pdf' });
  await expect(row.getByRole('button', { name: 'Read' })).toBeVisible();
  await expect(row.getByRole('button', { name: 'Move' })).toBeVisible();
  await expect(row.getByRole('button', { name: /^More for/ })).toHaveCount(0);
  await expect(open.getByText(/chunks?/)).toHaveCount(0);
});

test('advanced: Projects keeps its facets, graph, workspace copies and chunk counts', async ({ page }) => {
  await advanced(page);
  await library(page);
  await page.goto('/projects/');
  await expect(page.getByRole('heading', { name: 'Projects', level: 1 })).toBeVisible();
  await expect(page.getByRole('button', { name: /Project associations/ })).toBeVisible();
  const open = page.getByRole('region', { name: 'trip-planning' });
  await expect(open.getByRole('button', { name: 'Graph' })).toBeVisible();
  await expect(open.getByRole('heading', { name: 'Languages' })).toBeVisible();
  await open.getByRole('tab', { name: /Sources/ }).click();
  const row = open.getByRole('listitem').filter({ hasText: 'itinerary.pdf' });
  await expect(row.getByRole('button', { name: /^More for/ })).toBeVisible();
  await expect(row.getByText('5 chunks', { exact: true })).toBeVisible();
});
