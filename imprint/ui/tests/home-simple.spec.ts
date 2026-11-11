import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// Home in the simple dashboard (ADR 0182): the same summary, the review
// inbox in place of the job notices, and no technical breakdowns.

const now = () => Date.now() / 1000;
const summary = (over: Record<string, unknown> = {}) => ({
  workspace: 'default', total: 1_204, project_count: 3, source_count: 41, topic_count: 12, lang_count: 2,
  domain_count: 4, tag_pending: 900, added_7d: 30,
  projects: [['trip-planning', 800]], topics: [['flights', 90]], types: [['conversation', 400]],
  langs: [['python', 12]], domains: [['travel', 300]], layers: [['api', 10]],
  timeline: [{ month: '2026-11', count: 30 }],
  recent: [{ source: 'notes/packing.md', project: 'trip-planning', type: 'finding', timestamp: now() - 120, memories: 2, title: '' }],
  built_at: now(), age: 0, stale: false, refreshing: false, ...over,
});

const proposal = (source: string, title: string, over: Record<string, unknown> = {}) => ({
  workspace: 'default', origin: '', source, target: 'trip-planning', method: 'name', strength: 'weak',
  evidence: [{ quote: 'the trip-planning itinerary' }], preview: '', title, chunks: 3, status: 'pending',
  created_at: now(), decided_at: null, ...over,
});
const memory = (id: string, text: string, over: Record<string, unknown> = {}) => ({
  id, text, kind: 'preference', scope: '*', state: 'active', tier: 'record', amends: '', covers: [], conflicts: [], evidence: [],
  origin: 'organize', created_at: now(), updated_at: now(), approved_at: null, device: 'laptop', ...over,
});

async function home(page: Page, options: { proposals?: unknown[]; memories?: unknown[]; queue?: unknown } = {}) {
  await base(page, { mode: 'simple', tutorials: false });
  const calls: { url: string; body: unknown }[] = [];
  const state = { proposals: options.proposals ?? [], memories: options.memories ?? [] };
  await page.route('**/api/dashboard**', route => route.fulfill({ json: summary() }));
  await page.route('**/api/queue**', route => route.fulfill({ json: options.queue ?? { active: null, queued: [], recent: [] } }));
  await page.route('**/api/organize', route => route.fulfill({ json: { workspace: 'default', proposals: state.proposals, profiles: [], last_run: null } }));
  await page.route('**/api/organize/*', async route => {
    const body = route.request().postDataJSON();
    calls.push({ url: new URL(route.request().url()).pathname, body });
    const sources = (body.items as { source: string }[]).map(item => item.source);
    const undo = route.request().url().endsWith('/reopen');
    state.proposals = (state.proposals as { source: string; status: string }[]).map(p =>
      sources.includes(p.source) ? { ...p, status: undo ? 'pending' : body.accept ? 'accepted' : 'rejected' } : p);
    await route.fulfill({ json: { decided: 1, reopened: 1, failed: [] } });
  });
  await page.route('**/api/core', route => route.fulfill({ json: { memories: state.memories, budget: 3000, used: 0, kinds: ['preference'], max_text: 500 } }));
  await page.route('**/api/core/*', async route => {
    const body = route.request().postDataJSON();
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!;
    calls.push({ url: new URL(route.request().url()).pathname, body });
    state.memories = (state.memories as { id: string }[]).map(m => (m.id === id ? { ...m, ...body } : m));
    await route.fulfill({ json: {} });
  });
  await page.route('**/api/core/*/resolve', async route => {
    const body = route.request().postDataJSON();
    calls.push({ url: new URL(route.request().url()).pathname, body });
    await route.fulfill({ json: { changed: [] } });
  });
  return calls;
}

test('Home shows the memory, what was added and nothing technical', async ({ page }) => {
  await home(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Home', level: 1 })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Overview' }).getByText('1,204')).toBeVisible();
  await expect(page.getByRole('link', { name: /Documents and conversations\s*41/ })).toHaveAttribute('href', '/projects/#sources');
  await expect(page.getByRole('heading', { name: 'Recently added' })).toBeVisible();
  await expect(page.getByText('Nothing needs you right now.')).toBeVisible();
  // The breakdowns, the tagging backlog and the command line stay in the advanced dashboard.
  await expect(page.getByRole('heading', { name: 'What is in memory' })).toHaveCount(0);
  await expect(page.getByText(/wait for tagging/)).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Topics/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Refresh, retag and more/ })).toHaveCount(0);
  // One workspace: no switcher.
  await expect(page.getByRole('button', { name: /^Workspace/ })).toHaveCount(0);
});

test('adding a web page asks for its address only and reports in plain words', async ({ page }) => {
  await home(page);
  const jobs: unknown[] = [];
  await page.route('**/api/commands/ingest-url', async route => { jobs.push(route.request().postDataJSON()); await route.fulfill({ json: { job_id: 'j1', position: 0 } }); });
  await page.goto('/');
  await page.getByRole('group', { name: 'Add to memory' }).getByRole('button', { name: /Web page/ }).click();
  await expect(page.getByLabel('URL')).toBeVisible();
  await expect(page.getByLabel('Follow links')).toHaveCount(0);
  await expect(page.getByText('imprint ingest-url')).toHaveCount(0);
  await page.getByLabel('URL').fill('https://example.com/guide');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Started. The top of the window shows how far it is.');
  expect(jobs).toHaveLength(1);
});

test('a source Organize would move is decided from Home and can be undone', async ({ page }) => {
  const calls = await home(page, { proposals: [proposal('chat/a.jsonl', 'Where should we stay in Lisbon?'), proposal('chat/b.jsonl', 'Flights in May')] });
  await page.goto('/');
  const inbox = page.getByRole('heading', { name: 'Needs your review (2)' });
  await expect(inbox).toBeVisible();
  const row = page.getByRole('listitem').filter({ hasText: 'Where should we stay in Lisbon?' });
  await expect(row).toContainText('Looks like it belongs to the project “trip-planning”');
  await row.getByRole('button', { name: 'Move to trip-planning' }).click();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Needs your review (1)' })).toBeVisible();
  expect(calls[0]).toEqual({ url: '/api/organize/decide', body: { workspace: 'default', items: [{ origin: '', source: 'chat/a.jsonl' }], accept: true } });

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: 'Where should we stay in Lisbon?' })).toBeVisible();
  expect(calls[1].url).toBe('/api/organize/reopen');

  await page.getByRole('listitem').filter({ hasText: 'Flights in May' }).getByRole('button', { name: 'Keep where it is' }).click();
  await expect.poll(() => calls.at(-1)).toEqual({ url: '/api/organize/decide', body: { workspace: 'default', items: [{ origin: '', source: 'chat/b.jsonl' }], accept: false } });
});

test('a move the service refuses stays in the inbox with its reason', async ({ page }) => {
  await home(page, { proposals: [proposal('chat/a.jsonl', 'Where should we stay in Lisbon?')] });
  await page.route('**/api/organize/decide', route => route.fulfill({ json: { decided: 0, failed: [{ origin: '', source: 'chat/a.jsonl', error: 'A job is running; try again when it ends' }] } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Move to trip-planning' }).click();
  await expect(page.getByText('A job is running; try again when it ends')).toBeVisible();
  await expect(page.getByRole('listitem').filter({ hasText: 'Where should we stay in Lisbon?' })).toBeVisible();
});

test('guideline drafts and contradictions are settled from Home', async ({ page }) => {
  const calls = await home(page, { memories: [
    memory('g1', 'Prefers window seats on flights.', { tier: 'guideline', state: 'proposed' }),
    memory('old', 'Lives in Bucharest.'),
    memory('new', 'Lives in Lisbon.', { conflicts: [{ id: 'old', reason: 'moved', resolution: '' }] }),
  ] });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Needs your review (2)' })).toBeVisible();
  const draft = page.getByRole('listitem').filter({ hasText: 'Imprint noticed something about you' });
  await expect(draft).toContainText('Prefers window seats on flights.');
  await draft.getByRole('button', { name: 'Yes, remember this' }).click();
  await expect.poll(() => calls[0]).toEqual({ url: '/api/core/g1', body: { state: 'active' } });

  const conflict = page.getByRole('listitem').filter({ hasText: 'Two things Imprint knows about you disagree' });
  await expect(conflict).toContainText('Newer: “Lives in Lisbon.” Earlier: “Lives in Bucharest.”');
  await conflict.getByRole('button', { name: 'Use the newer' }).click();
  await expect.poll(() => calls.at(-1)).toEqual({ url: '/api/core/new/resolve', body: { keep: 'newer' } });
});

test('a job that did not finish can be tried again from Home', async ({ page }) => {
  const failed = { id: 'f1', command: 'ingest', label: 'Ingest directory', body: {}, status: 'failed', pid: null, pgid: null, exit_code: 1,
    error: 'The folder no longer exists', created_at: now() - 600, started_at: now() - 590, ended_at: now() - 500 };
  await home(page, { queue: { active: null, queued: [], recent: [failed] } });
  let retried = '';
  await page.route('**/api/jobs/f1/retry', route => { retried = route.request().method(); return route.fulfill({ json: { job_id: 'f2', position: 0, retry_of: 'f1' } }); });
  await page.goto('/');
  const row = page.getByRole('listitem').filter({ hasText: 'Ingest directory did not finish' });
  await expect(row).toContainText('The folder no longer exists');
  await expect(row.getByRole('link', { name: 'Details' })).toHaveAttribute('href', '/queue/');
  await row.getByRole('button', { name: 'Try again' }).click();
  await expect.poll(() => retried).toBe('POST');
});

test('a job that ends is announced in plain words', async ({ page }) => {
  const job = { id: 'r1', command: 'ingest', label: 'Ingest directory', body: { dir: '/home/me/trip' }, params: [{ label: 'Folder', value: '/home/me/trip' }],
    status: 'running', pid: 1, pgid: 1, exit_code: null, error: null, created_at: now() - 60, started_at: now() - 50, ended_at: null };
  let queue: unknown = { active: job, queued: [], recent: [] };
  await home(page);
  await page.route('**/api/queue**', route => route.fulfill({ json: queue }));
  await page.goto('/');
  await expect(page.getByRole('region', { name: 'Running: Reading files' })).toBeVisible();
  queue = { active: null, queued: [], recent: [{ ...job, status: 'done', exit_code: 0, ended_at: now() }] };
  await expect(page.getByText('Imprint finished')).toBeVisible();
  await expect(page.getByText('Reading files · /home/me/trip')).toBeVisible();
  await expect(page.getByText('Ingest directory finished')).toHaveCount(0);
});

test('the top bar says in plain words what Imprint is doing', async ({ page }) => {
  const running = { id: 'r1', command: 'learn', label: 'Learn conversations', body: {}, status: 'running', pid: 1, pgid: 1, exit_code: null, error: null,
    created_at: now() - 60, started_at: now() - 50, ended_at: null, phase: 'embedding', processed: 30, total: 120, unit: 'conversations', percent: 25 };
  const waiting = { ...running, id: 'q1', command: 'ingest', label: 'Ingest directory', status: 'queued', started_at: null, phase: undefined, processed: undefined, total: undefined };
  await home(page, { queue: { active: running, queued: [waiting], recent: [] } });
  await page.goto('/');
  const pill = page.getByRole('button', { name: 'Reading your AI conversations. Show what Imprint is doing' });
  await expect(pill).toContainText('Reading your AI conversations · 25%');
  await expect(page.getByText(/embedding/i)).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Running: Reading your AI conversations' })).toContainText('30 of 120 conversations · 25%');
  await pill.click();
  const panel = page.getByRole('dialog');
  await expect(panel).toContainText('Reading your AI conversations · 30 of 120 conversations');
  await expect(panel).toContainText('Waiting its turn');
  await expect(panel).toContainText('Reading files');
  await expect(panel.getByRole('link', { name: 'Details and cancel' })).toHaveAttribute('href', '/queue/');
});
