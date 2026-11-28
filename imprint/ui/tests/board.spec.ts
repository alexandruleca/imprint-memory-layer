import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// The Board tab of the Agents page (ADRs 0208-0212): the workspace's Tasks by
// status, moved by dragging or from a card's menu, filtered and grouped, and
// one Task's drawer. The API is a small in-memory Board that keeps its rules:
// Ready needs an acceptance criterion.

const now = Date.now() / 1000;
const STATUSES = ['backlog', 'ready', 'in_progress', 'review', 'done', 'cancelled'] as const;
type Status = (typeof STATUSES)[number];
type Task = Record<string, unknown> & { id: string; title: string; status: Status; project: string; tags: string[];
  acceptance: string[]; comments: { id: string; text: string; to: string[]; by: { kind: string }; at: number }[] };

function task(over: Partial<Task> & { id: string; title: string }): Task {
  return {
    description: '', project: 'shop', status: 'backlog', priority: 'P2', position: null, acceptance: [], tags: [], assignees: [],
    feature: null, feature_title: null, blockers: [], open_blockers: [], tasks: [], blocking: [], blocked: null, holder: null,
    previous_holder: null, stalling: false, stale: false, worked_by: null, last_checkpoint: null, checkpoints: 0, comments: [],
    comment_count: 0, created_at: now - 3600, created_by: { kind: 'user', device: 'laptop' }, updated_at: now - 60, ended_at: null,
    status_at: now - 60, ...over,
  };
}

const holder = { session: 'conversation:c1', name: 'grimforge', client: 'claude-code', device: 'laptop', since: now - 1800, progress_at: now - 300 };

/** The /api/tasks routes over an in-memory Board; returns what was asked of it. */
async function mockBoard(page: Page, tasks: Task[], inbox: { comments?: unknown[]; review?: Task[] } = {}) {
  const asked: { method: string; path: string; body: Record<string, unknown> | null; query: URLSearchParams }[] = [];
  const respond = (route: Parameters<Parameters<Page['route']>[1]>[0]) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.postData() ? JSON.parse(request.postData()!) : null;
    asked.push({ method: request.method(), path: url.pathname, body, query: url.searchParams });
    const parts = url.pathname.split('/').slice(3);  // after /api/tasks
    if (parts[0] === 'board') {
      const tag = url.searchParams.get('tag');
      const shown = tasks.filter(t => !tag || t.tags.includes(tag));
      const columns = Object.fromEntries(STATUSES.map(s => [s, shown.filter(t => t.status === s)]));
      return route.fulfill({ json: { workspace: 'default', columns, total: shown.length, autonomy: 'ready', options: {
        projects: [...new Set(tasks.map(t => t.project))], tags: [...new Set(tasks.flatMap(t => t.tags))], assignees: [], holders: ['grimforge'],
        features: [] } } });
    }
    if (parts[0] === 'inbox') return route.fulfill({ json: { comments: inbox.comments ?? [], review: inbox.review ?? [] } });
    if (parts[0] === 'progress') {
      const entry = (name: string, total: number, done: number, days_left: number | null) => ({ name, total, done, cancelled: 0, blocked: 0, stale: 0,
        counts: { backlog: 0, ready: total - done, in_progress: 0, review: 0, done }, finished_recently: done, pace: done / 14, days_left });
      return route.fulfill({ json: {
        projects: [entry('shop', 12, 7, 2.4), entry('site', 3, 1, null)],
        features: [{ ...entry('', 2, 1, null), id: 'T-feat1', title: 'Checkout redesign', project: 'shop' }],
      } });
    }
    if (parts.length === 0 && request.method() === 'POST') {
      const made = task({ id: 'T-new01', title: String(body.title), project: String(body.project), status: body.status ?? 'backlog',
        acceptance: body.acceptance ?? [], tags: body.tags ?? [] });
      tasks.push(made);
      return route.fulfill({ json: { task: made } });
    }
    const found = tasks.find(t => t.id === parts[0]);
    if (!found) return route.fulfill({ status: 404, json: { error: `No task ${parts[0]}` } });
    if (parts.length === 1) {
      const waits = (t: Task) => (t.blockers as string[]).includes(found.id);
      const linked = tasks.filter(t => t.id !== found.id
        && (t.id === found.feature || t.feature === found.id || (found.blockers as string[]).includes(t.id) || waits(t)));
      return route.fulfill({ json: { task: { ...found, blocking: tasks.filter(waits).map(t => t.id) },
        history: [{ id: 'e1', kind: 'created', data: {}, hlc: '1', device: 'laptop', actor: 'user',
        session: null, agent: null, client: null, recorded_at: now - 3600 }], handoff: { previous_holder: null, last_checkpoint: null,
        checkpoints: [], branch: null, commit: null, files: [], conversation: null },
        related: Object.fromEntries(linked.map(t => [t.id, { title: t.title, status: t.status, priority: t.priority, project: t.project,
          stale: !!t.stale, blocked: !!t.blocked }])) } });
    }
    if (parts[1] === 'move') {
      if (body.status === 'ready' && !found.acceptance.length) {
        return route.fulfill({ status: 400, json: { error: 'A task moves to ready only with at least one acceptance criterion' } });
      }
      found.status = body.status;
    }
    if (parts[1] === 'edit' && body.project) found.project = body.project;
    if (parts[1] === 'link' && body.add_blockers) found.blockers = [...(found.blockers as string[]), ...body.add_blockers];
    if (parts[1] === 'comment') {
      found.comments = [...found.comments, { id: `c${found.comments.length}`, text: body.text, to: body.to, by: { kind: 'user' }, at: now }];
      found.comment_count = found.comments.length;
    }
    return route.fulfill({ json: { task: found } });
  };
  await page.route('**/api/tasks/**', respond);
  await page.route('**/api/tasks', respond);
  return asked;
}

const board = () => [
  task({ id: 'T-aaaa1', title: 'Fix the tax rounding', tags: ['cart'], acceptance: ['Totals include tax'], status: 'ready', priority: 'P1' }),
  task({ id: 'T-bbbb2', title: 'Rewrite the search box', project: 'site', tags: ['search'] }),
  task({ id: 'T-cccc3', title: 'Upload retries', status: 'in_progress', holder, comment_count: 2 }),
  task({ id: 'T-dddd4', title: 'Cache warmup', status: 'in_progress', stale: true, previous_holder: { ...holder, name: 'quill', client: 'codex' } }),
];

test('the Board tab opens from its address and lays Tasks out by status', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, board());
  await page.goto('/agents/#board');
  await expect(page.getByRole('tab', { name: 'Board' })).toHaveAttribute('aria-selected', 'true');
  for (const [status, count] of [['Backlog', 1], ['Ready', 1], ['In progress', 2], ['Review', 0]] as const) {
    await expect(page.getByRole('region', { name: `${status}, ${count} tasks` })).toBeVisible();
  }
  const working = page.locator('li[data-task="T-cccc3"]');
  await expect(working).toContainText('grimforge');
  await expect(page.getByRole('region', { name: 'Done, 0 tasks, folded' })).toBeVisible();
  await expect(page.locator('li[data-task="T-dddd4"]')).toContainText('Stale');
  await page.getByRole('tab', { name: 'Sessions' }).click();
  await expect(page).toHaveURL(/#sessions$/);
});

test('a new Task is created from the dialog and opens', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, board());
  await page.goto('/agents/#board');
  await page.getByRole('button', { name: 'New task' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New task' });
  await dialog.getByLabel('Title').fill('Add a coupon field');
  await dialog.getByLabel('Project').fill('shop');
  await dialog.getByRole('checkbox', { name: 'Ready for agents' }).click();
  await expect(dialog.getByText('Add at least one acceptance criterion first.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Create task' })).toBeDisabled();
  await dialog.getByLabel('Acceptance criteria').fill('A valid coupon lowers the total');
  await dialog.getByRole('button', { name: 'Create task' }).click();
  await expect(page.getByRole('dialog', { name: /Add a coupon field/ })).toBeVisible();
  const created = asked.find(a => a.method === 'POST' && a.path === '/api/tasks');
  expect(created?.body).toMatchObject({ title: 'Add a coupon field', project: 'shop', status: 'ready', acceptance: ['A valid coupon lowers the total'] });
});

test('dragging moves a Task, and a move the Board refuses says why and stays put', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, board());
  await page.goto('/agents/#board');
  const backlog = page.getByRole('region', { name: /^Backlog/ });
  const ready = page.getByRole('region', { name: /^Ready/ });
  const review = page.getByRole('region', { name: /^Review/ });
  await page.locator('li[data-task="T-bbbb2"]').dragTo(ready);
  await expect(page.getByText('T-bbbb2 stays in Backlog')).toBeVisible();
  await expect(page.getByText('at least one acceptance criterion')).toBeVisible();
  await expect(backlog.locator('li[data-task="T-bbbb2"]')).toBeVisible();
  await page.locator('li[data-task="T-aaaa1"]').dragTo(review);
  await expect(review.locator('li[data-task="T-aaaa1"]')).toBeVisible();
  expect(asked.filter(a => a.path.endsWith('/move')).map(a => a.body?.status)).toEqual(['ready', 'review']);
});

test('the card menu moves a Task without dragging', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, board());
  await page.goto('/agents/#board');
  await page.getByRole('button', { name: 'Move T-aaaa1' }).click();
  await page.getByRole('menuitem', { name: 'Review' }).click();
  await expect(page.getByRole('region', { name: /^Review/ }).locator('li[data-task="T-aaaa1"]')).toBeVisible();
  expect(asked.find(a => a.path === '/api/tasks/T-aaaa1/move')?.body).toEqual({ status: 'review' });
});

test('filters and grouping survive a reload', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, board());
  await page.goto('/agents/#board');
  await page.getByRole('button', { name: 'Filters' }).click();
  await page.getByRole('combobox', { name: 'Tag' }).click();
  await page.getByRole('option', { name: '#cart' }).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('li[data-task="T-bbbb2"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Filters, 1 on' })).toBeVisible();
  await expect(page.getByLabel('Filters on')).toContainText('Tag: cart');
  await page.getByRole('combobox', { name: 'Group by' }).click();
  await page.getByRole('option', { name: 'Group by project' }).click();
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Board' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('li[data-task="T-aaaa1"]')).toBeVisible();
  await expect(page.locator('li[data-task="T-bbbb2"]')).toHaveCount(0);
  await expect(page.getByRole('region', { name: /^Ready/ }).getByRole('heading', { name: 'shop' })).toBeVisible();
  await expect(page.getByLabel('Filters on')).toContainText('Tag: cart');
  expect(asked.filter(a => a.path === '/api/tasks/board').at(-1)?.query.get('tag')).toBe('cart');
});

test("the drawer shows a Task and sends a comment to its holder's agent", async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, board());
  await page.goto('/agents/#board');
  await page.getByRole('button', { name: /^T-cccc3: Upload retries/ }).click();
  const drawer = page.getByRole('dialog', { name: /Upload retries/ });
  await expect(drawer.getByText('grimforge (Claude Code)')).toBeVisible();
  await expect(drawer.getByText(/last progress/)).toBeVisible();
  await expect(drawer.getByRole('button', { name: /History/ })).toBeVisible();
  await expect(page).toHaveURL(/\?task=T-cccc3#board$/);
  await drawer.getByLabel('Comment', { exact: true }).fill('Use the new retry policy');
  await drawer.getByLabel('To').fill('grimforge');
  await drawer.getByRole('button', { name: 'Send' }).click();
  // In the comment list: for a moment after sending, the box still holds the same text.
  await expect(drawer.getByRole('listitem').getByText('Use the new retry policy')).toBeVisible();
  expect(asked.find(a => a.path === '/api/tasks/T-cccc3/comment')?.body).toEqual({ text: 'Use the new retry policy', to: ['grimforge'] });
});

test('simple mode lists the Board as Tasks, without sessions, history and lease details', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await mockBoard(page, board());
  await page.goto('/agents/');
  await expect(page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'Tasks', level: 1 })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Sessions' })).toHaveCount(0);
  await expect(page.getByRole('note')).toHaveCount(0);
  await page.goto('/agents/?task=T-cccc3');
  const drawer = page.getByRole('dialog', { name: /Upload retries/ });
  await expect(drawer.getByText('grimforge (Claude Code)')).toBeVisible();
  await expect(drawer.getByText(/last progress/)).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: /History/ })).toHaveCount(0);
});

test('a comment addressed to the user is told on any page', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, board(), { comments: [{ id: 'c9', task: 'T-cccc3', title: 'Upload retries', text: 'Which bucket should I use?',
    to: ['user'], by: { kind: 'agent', name: 'grimforge', client: 'claude-code' }, at: now - 10 }] });
  await page.goto('/');
  await expect(page.getByText('grimforge (Claude Code) on T-cccc3')).toBeVisible();
  await expect(page.getByText('Which bucket should I use?')).toBeVisible();
});

test('the Board fits a phone without scrolling the page sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, board());
  await page.goto('/agents/#board');
  await expect(page.locator('li[data-task="T-aaaa1"]')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test('what needs the user is counted and shows only those Tasks', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, [...board(), task({ id: 'T-eeee5', title: 'Order emails', status: 'review' })]);
  await page.goto('/agents/#board');
  const needs = page.getByRole('group', { name: 'What needs you' });
  await expect(needs.getByRole('button', { name: '1 to review' })).toBeVisible();
  await needs.getByRole('button', { name: '1 stale' }).click();
  await expect(page.locator('li[data-task="T-dddd4"]')).toBeVisible();
  await expect(page.locator('li[data-task="T-cccc3"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Show every task' }).click();
  await expect(page.locator('li[data-task="T-cccc3"]')).toBeVisible();
});

test('Done and Cancelled unfold, and stay unfolded after a reload', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, [...board(), task({ id: 'T-ffff6', title: 'Shipped thing', status: 'done', ended_at: now - 60 })]);
  await page.goto('/agents/#board');
  await page.getByRole('button', { name: 'Show Done, 1 tasks' }).click();
  await expect(page.locator('li[data-task="T-ffff6"]')).toBeVisible();
  await page.reload();
  await expect(page.locator('li[data-task="T-ffff6"]')).toBeVisible();
  await page.getByRole('button', { name: 'Fold Done' }).click();
  await expect(page.locator('li[data-task="T-ffff6"]')).toHaveCount(0);
});

test("the drawer says a Feature and its Tasks are taken only when asked (ADR 0219)", async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, [
    ...board(),
    task({ id: 'T-feat1', title: 'Checkout redesign', tasks: ['T-part1'], progress: { done: 0, total: 1 } }),
    task({ id: 'T-part1', title: 'Cart page', status: 'ready', acceptance: ['x'], feature: 'T-feat1', feature_title: 'Checkout redesign' }),
  ]);
  const hints: [string, RegExp, string][] = [
    ['T-aaaa1', /Fix the tax rounding/, 'None: any agent may take it.'],
    ['T-part1', /Cart page/, 'Agents take it when you ask for its Feature, never unasked.'],
    ['T-feat1', /Checkout redesign/, "Agents work this Feature's tasks when you ask for it, never unasked."],
  ];
  for (const [id, title, hint] of hints) {
    await page.goto(`/agents/?task=${id}#board`);
    await expect(page.getByRole('dialog', { name: title }).getByText(hint)).toBeVisible();
  }
  await page.goto('/agents/#board');
  await expect(page.getByRole('combobox', { name: /Agents take unasked/ })).toContainText('Standalone Ready and Stale tasks');
});

test("a Feature's card shows its progress, and Progress says how long is left", async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, [...board(), task({ id: 'T-feat1', title: 'Checkout redesign', tasks: ['T-aaaa1', 'T-bbbb2'], progress: { done: 1, total: 2 } })]);
  await page.goto('/agents/#board');
  const card = page.locator('li[data-task="T-feat1"]');
  await expect(card).toContainText('Feature · 2 tasks');
  await expect(card).toContainText('1 of 2 done');
  await expect(card.getByRole('progressbar', { name: 'Checkout redesign: 1 of 2 done' })).toBeVisible();
  await page.getByRole('button', { name: 'Progress' }).click();
  const panel = page.getByRole('dialog', { name: 'Progress' });
  await expect(panel.getByRole('progressbar', { name: 'shop: 7 of 12 done' })).toBeVisible();
  await expect(panel.getByText('About 3 days left')).toBeVisible();
  await expect(panel.getByText('Not enough finished work to estimate yet').first()).toBeVisible();
  await expect(panel.getByText('Checkout redesign · shop')).toBeVisible();
});

test('a card copies its id without opening, and its menu copies a link to it', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await base(page, { mode: 'advanced', tutorials: false });
  await mockBoard(page, board());
  await page.goto('/agents/#board');
  await page.locator('li[data-task="T-aaaa1"]').getByRole('button', { name: 'Copy T-aaaa1' }).click();
  await expect(page.getByText('Copied T-aaaa1')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('T-aaaa1');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Move T-aaaa1' }).click();
  await page.getByRole('menuitem', { name: 'Copy link' }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/\/agents\/\?task=T-aaaa1#board$/);
});

test('the drawer links a Task to its Feature, the Feature\'s Tasks and what waits on what, and goes back', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, [
    ...board(),
    task({ id: 'T-feat1', title: 'Checkout redesign', tasks: ['T-part1', 'T-part2'], progress: { done: 1, working: 0, total: 2 },
      description: 'Start with T-part1, then the summary.' }),
    task({ id: 'T-part1', title: 'Cart page', status: 'done', feature: 'T-feat1', feature_title: 'Checkout redesign' }),
    task({ id: 'T-part2', title: 'Summary page', feature: 'T-feat1', feature_title: 'Checkout redesign', blockers: ['T-part1'] }),
  ]);
  await page.goto('/agents/#board');
  // The card's Feature line opens the Feature.
  await page.locator('li[data-task="T-part2"]').getByRole('button', { name: 'Checkout redesign' }).click();
  const feature = page.getByRole('dialog', { name: /Checkout redesign/ });
  await expect(feature.getByRole('heading', { name: 'Tasks in this Feature (2)' })).toBeVisible();
  await expect(feature.getByRole('button', { name: 'T-part1: Cart page, Done. Open' })).toBeVisible();
  // An id in the description opens that Task; Back returns.
  await feature.getByRole('button', { name: 'T-part1', exact: true }).click();
  const cart = page.getByRole('dialog', { name: /Cart page/ });
  await expect(cart.getByRole('heading', { name: 'Waiting on it' })).toBeVisible();
  await cart.getByRole('button', { name: 'T-part2: Summary page, Backlog. Open' }).click();
  const summary = page.getByRole('dialog', { name: /Summary page/ });
  await expect(summary.getByRole('heading', { name: 'Waits on' })).toBeVisible();
  await expect(page).toHaveURL(/\?task=T-part2#board$/);
  await summary.getByRole('button', { name: 'Back to T-part1' }).click();
  await page.getByRole('dialog', { name: /Cart page/ }).getByRole('button', { name: 'Back to T-feat1' }).click();
  await expect(page.getByRole('dialog', { name: /Checkout redesign/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Back to/ })).toHaveCount(0);
  // A Task it waits on is added by id.
  await page.goto('/agents/?task=T-bbbb2#board');
  const search = page.getByRole('dialog', { name: /Rewrite the search box/ });
  await search.getByRole('button', { name: 'Add a task it waits on' }).click();
  await search.getByLabel('Task it waits on').fill('T-aaaa1');
  await search.getByRole('button', { name: 'Add', exact: true }).click();
  expect(asked.find(a => a.path === '/api/tasks/T-bbbb2/link')?.body).toEqual({ add_blockers: ['T-aaaa1'] });
  await expect(search.getByRole('button', { name: 'T-aaaa1: Fix the tax rounding, Ready. Open' })).toBeVisible();
});

test('the drawer moves a Task to another project, a known one or a new one', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockBoard(page, board());
  await page.goto('/agents/?task=T-aaaa1#board');
  const drawer = page.getByRole('dialog', { name: /Fix the tax rounding/ });
  await drawer.getByRole('combobox', { name: 'Project' }).click();
  await page.getByRole('option', { name: 'site' }).click();
  await drawer.getByRole('combobox', { name: 'Project' }).click();
  await page.getByRole('option', { name: 'Another project…' }).click();
  await drawer.getByLabel('Project name').fill('checkout');
  await drawer.getByRole('button', { name: 'Save' }).click();
  expect(asked.filter(a => a.path === '/api/tasks/T-aaaa1/edit').map(a => a.body)).toEqual([{ project: 'site' }, { project: 'checkout' }]);
});
