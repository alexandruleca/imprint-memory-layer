import { test, expect, type Page } from '@playwright/test';

// Curating a large workspace (ADR 0150): topics are searched, sorted and
// paged on the server, a topic's sources come a page at a time and can be
// picked to move to a project or another workspace, the copy/move dialog
// previews what it takes before it runs, and paired devices filter and page.
// Shapes follow build_topic_overview/build_topic_detail (imprint/cli_viz.py),
// cli_migrate.preview and /api/sync/peers.

const sse = (events: object[]) => events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
const now = Date.now() / 1000;
const TOPICS = Array.from({ length: 3200 }, (_, i) => ({
  id: `t${i}`, name: i === 0 ? 'react' : `topic-${String(i).padStart(4, '0')}`, count: 9000 - i,
  topProjects: ['imprint', 'storefront'], topLangs: [], color: '#000',
}));
const chunk = (i: number) => ({ id: `m${i}`, label: `Passage ${i}`, type: 'finding', chunk_index: i, tags: { topics: ['react'] }, content: `Passage ${i}` });
const SOURCES = Array.from({ length: 45 }, (_, s) => ({
  source: `${s % 3 ? 'imprint' : 'storefront'}/src/file_${s}.ts`, project: s % 3 ? 'imprint' : 'storefront',
  source_type: 'file', title: '', chunks: [chunk(s * 2), chunk(s * 2 + 1)],
}));

async function mockApi(page: Page) {
  const calls = { topics: [] as URLSearchParams[], detail: [] as URLSearchParams[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default', 'team'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/topics?**', route => {
    const params = new URL(route.request().url()).searchParams;
    calls.topics.push(params);
    const q = params.get('q') ?? '';
    let list = TOPICS.filter((t) => t.name.includes(q));
    if (params.get('sort') === 'name') list = [...list].sort((a, b) => b.name.localeCompare(a.name));
    const offset = Number(params.get('offset') ?? 0), limit = Number(params.get('limit') ?? 100);
    return route.fulfill({ json: { topics: list.slice(offset, offset + limit), total: 1, matched: list.length,
      topic_count: TOPICS.length, offset, limit, sort: params.get('sort') ?? 'count', q, facets: {} } });
  });
  await page.route('**/api/topic/*', route => {
    const params = new URL(route.request().url()).searchParams;
    calls.detail.push(params);
    const project = params.get('project');
    const list = SOURCES.filter((s) => project === null || s.project === project);
    const offset = Number(params.get('offset') ?? 0), limit = Number(params.get('limit') ?? 1000);
    return route.fulfill({ json: { topic: 'react', project, total: 90, projects: [['imprint', 60], ['storefront', 30]],
      sources: list.slice(offset, offset + limit), source_count: list.length, offset, limit } });
  });
  return calls;
}

test('topics page on the server: the true count, search, sort and pages', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/topics/');
  await expect(page.getByRole('heading', { name: /Topics\s*3,200/ })).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Topic pages' });
  await expect(nav).toContainText('1–50 of 3,200');
  await nav.getByRole('button', { name: 'Next topics' }).click();
  await expect(nav).toContainText('51–100 of 3,200');
  expect(calls.topics.at(-1)!.get('offset')).toBe('50');
  await page.getByRole('button', { name: 'A to Z' }).click();
  await expect(nav).toContainText('1–50 of 3,200');
  expect(calls.topics.at(-1)!.get('sort')).toBe('name');
  await page.getByLabel('Search topics').fill('topic-31');
  await expect(page.getByText('100 matches')).toBeVisible();
  expect(calls.topics.at(-1)!.get('q')).toBe('topic-31');
  await page.getByLabel('Search topics').fill('nothing-like-this');
  await expect(page.getByText('No topic matches “nothing-like-this”.')).toBeVisible();
  await page.getByRole('button', { name: 'Clear the search' }).first().click();
  await expect(nav).toContainText('of 3,200');
});

test('a topic pages its sources and filters them by project on the server', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/topics/?topic=react');
  const sources = page.getByRole('list', { name: 'Sources' });
  await expect(page.getByText('90 memories from 45 sources in 2 projects')).toBeVisible();
  await expect(sources.getByRole('listitem')).toHaveCount(20);
  expect(calls.detail.at(-1)!.get('limit')).toBe('20');
  await page.getByRole('navigation', { name: 'Pagination' }).getByRole('button', { name: 'Page 3' }).click();
  await expect(sources.getByRole('listitem')).toHaveCount(5);
  await page.getByRole('group', { name: 'Show sources from' }).getByRole('button', { name: /storefront/ }).click();
  await expect(page.getByText('15 sources in storefront')).toBeVisible();
  expect(calls.detail.at(-1)!.get('project')).toBe('storefront');
  expect(calls.detail.at(-1)!.get('offset')).toBeNull();
});

test('picked sources move to another project or copy to a workspace', async ({ page }) => {
  await mockApi(page);
  let moved: Record<string, unknown> = {};
  await page.route('**/api/{overview,projects}', route => route.fulfill({ json: { projects: [{ name: 'imprint', count: 60 }, { name: 'storefront', count: 30 }], total: 90, facets: {} } }));
  await page.route('**/api/projects/move', async route => {
    moved = route.request().postDataJSON();
    await route.fulfill({ json: { workspace: 'default', project: 'imprint', target: 'storefront', whole: false, chunks: 4,
      sources: 2, target_exists: true, sample: ['imprint/src/file_1.ts', 'imprint/src/file_2.ts'], applied: !!moved.apply } });
  });
  await page.goto('/topics/?topic=react');
  const sources = page.getByRole('list', { name: 'Sources' });
  await sources.getByRole('checkbox', { name: 'Select imprint/src/file_1.ts' }).check();
  await sources.getByRole('checkbox', { name: 'Select storefront/src/file_3.ts' }).check();
  const bar = page.getByRole('toolbar', { name: 'Selected sources' });
  await expect(bar).toContainText('2 sources selected');
  // Two projects: a move to a project takes one project's sources at a time.
  await expect(bar.getByRole('button', { name: 'Move to project…' })).toBeDisabled();
  await sources.getByRole('checkbox', { name: 'Select storefront/src/file_3.ts' }).uncheck();
  await sources.getByRole('checkbox', { name: 'Select imprint/src/file_2.ts' }).check();
  await bar.getByRole('button', { name: 'Move to project…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Move sources to another project' });
  await dialog.getByRole('combobox', { name: 'Existing project' }).click();
  await page.getByRole('option', { name: 'storefront' }).click();
  await dialog.getByRole('button', { name: 'Review move' }).click();
  await expect(dialog).toContainText('4 chunks from 2 sources move from imprint to storefront');
  await dialog.getByRole('button', { name: 'Move 4 chunks' }).click();
  expect(moved).toMatchObject({ project: 'imprint', target: 'storefront', sources: ['imprint/src/file_1.ts', 'imprint/src/file_2.ts'], apply: true });
  await expect(dialog).toHaveCount(0);

  await sources.getByRole('checkbox', { name: 'Select imprint/src/file_4.ts' }).check();
  await page.getByRole('toolbar', { name: 'Selected sources' }).getByRole('button', { name: 'Copy to workspace…' }).click();
  const migrate = page.getByRole('dialog', { name: 'Copy or move this source to another workspace' });
  await expect(migrate.getByRole('list', { name: 'Selected sources' })).toContainText('imprint/src/file_4.ts');
});

test('copy or move several topics: preview, progress, result and move back', async ({ page }) => {
  await mockApi(page);
  const previews: Record<string, unknown>[] = [];
  const jobs: Record<string, unknown>[] = [];
  await page.route('**/api/migrate/preview', async route => {
    const body = route.request().postDataJSON();
    previews.push(body);
    const values = body.values as string[];
    await route.fulfill({ json: { from: body.from, to: body.to, mode: body.mode, target_exists: body.to !== 'shared-with-bob',
      total: 150, existing: body.to === 'team' ? 20 : 0, sources: 12, projects: [['imprint', 100], ['storefront', 50]],
      items: values.map((value, i) => ({ value, memories: 100 - i * 50, sources: 6 })) } });
  });
  await page.route('**/api/commands/migrate', async route => {
    jobs.push(route.request().postDataJSON());
    await route.fulfill({ json: { job_id: `j${jobs.length}`, position: 0 } });
  });
  await page.route('**/api/jobs/*/stream', route => route.fulfill({ headers: { 'content-type': 'text/event-stream' }, body: sse([
    { type: 'output', text: '  migrate: moving · 75/75 memories\n' },
    { type: 'output', text: 'Done: 75 moved of 75 scanned (removed from source) + 0 new KG facts\n' }, { type: 'done' },
  ]) }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [] } }));
  await page.goto('/settings/');
  await page.getByRole('button', { name: 'Copy or move memories…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Copy or move to another workspace' });
  await dialog.getByRole('button', { name: 'topics' }).click();
  await dialog.getByLabel('Search topics').fill('react');
  await dialog.getByRole('checkbox', { name: /^react/ }).check();
  await dialog.getByLabel('Search topics').fill('topic-000');
  await dialog.getByRole('checkbox', { name: /^topic-0001/ }).check();
  await expect(dialog.getByText('2 topics selected')).toBeVisible();

  // A new workspace is named, validated, and only created by the run.
  await dialog.getByRole('combobox', { name: 'To', exact: true }).click();
  await page.getByRole('option', { name: 'New workspace…' }).click();
  await dialog.getByLabel('New workspace name').fill('Bad Name');
  await expect(dialog.getByText(/Lowercase letters, digits and hyphens/)).toBeVisible();
  await dialog.getByLabel('New workspace name').fill('shared-with-bob');
  const preview = dialog.getByRole('status', { name: 'Preview' });
  await expect(preview).toContainText('Copies 150 memories from 12 sources to shared-with-bob, a new workspace.');
  await expect(preview.getByRole('list', { name: 'Per selection' })).toContainText('react');
  expect(previews.at(-1)).toEqual({ from: 'default', to: 'shared-with-bob', mode: 'topic', values: ['react', 'topic-0001'] });

  await dialog.getByRole('radio', { name: /^Move/ }).check();
  await expect(preview).toContainText('They are removed from default');
  await dialog.getByRole('button', { name: 'Move 150 memories to shared-with-bob' }).click();
  await expect(dialog.getByText('Done: 150 memories moved from default to shared-with-bob.')).toBeVisible();
  expect(jobs).toEqual([
    { from: 'default', to: 'shared-with-bob', topic: 'react', copy: false },
    { from: 'default', to: 'shared-with-bob', topic: 'topic-0001', copy: false },
  ]);

  // Undo the move: the same topics, the other way, previewed again.
  await dialog.getByRole('button', { name: 'Move back…' }).click();
  await expect(preview).toContainText('Moves 150 memories from 12 sources to default.');
  expect(previews.at(-1)).toEqual({ from: 'shared-with-bob', to: 'default', mode: 'topic', values: ['react', 'topic-0001'] });
});

const peer = (i: number) => ({
  id: `pair-${i}`, peer: { hostname: `host-${String(i).padStart(2, '0')}`, fingerprint: `fp${i}`, user: 'bob', os: 'linux' },
  workspace: i % 2 ? 'team' : 'default', remote_workspace: 'default', relay: 'wss://relay.example.com', direction: 'both',
  enabled: i % 4 !== 1, state: 'active', created_at: now - 86400 * 30, last_sync: now - 600, last_seen: i === 3 ? now - 86400 * 9 : now - 60,
  last_error: '', last_result: null,
});

test('many paired devices are counted, searched, filtered and paged', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: Array.from({ length: 19 }, (_, i) => peer(i)) } }));
  await page.route('**/api/desktop-learn/history', route => route.fulfill({ json: { seen: {}, count: 0 } }));
  await page.goto('/sync/');
  await expect(page.getByLabel('Pairings')).toHaveText(/19 pairings · 19 devices\s*· 1 need attention/);
  const rows = page.locator('section[aria-labelledby="paired-devices-title"] ul > li');
  await expect(rows).toHaveCount(8);
  await page.getByRole('navigation', { name: 'Pagination' }).getByRole('button', { name: 'Page 3' }).click();
  await expect(rows).toHaveCount(3);
  await page.getByRole('button', { name: 'Paused', exact: true }).click();
  await expect(rows).toHaveCount(5);
  await page.getByRole('button', { name: /^Needs attention/ }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText('Not seen for 9 days');
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await page.getByLabel('Search paired devices').fill('host-1');
  await expect(rows).toHaveCount(8);
  await page.getByLabel('Search paired devices').fill('nobody');
  await expect(page.getByText('No pairing matches.')).toBeVisible();
});
