import { test, expect, type Page } from '@playwright/test';

type Core = { id: string; text: string; kind: string; scope: string; state: string; tier: 'guideline' | 'record';
  amends: string; covers: string[]; conflicts: { id: string; reason: string; resolution: string }[]; evidence: object[]; origin: string;
  created_at: number; updated_at: number; approved_at: number | null; device: string };

/** A guideline's tokens in a session: "- text" and the line break, about 4 characters a token. */
const tokens = (text: string) => Math.ceil((text.length + 3) / 4);

// Mirrors imprint/api.py's /api/core (ADR 0143): the user writes active
// guidelines (with the records they generalise in `covers`); PATCH edits,
// approves, retires, restores, and returns a proposal to proposed (an undo).
// Approving an amendment retires the guideline it amends; returning it to
// the proposals restores that one. `used` counts active every-project
// guidelines only. POST /api/core/{id}/resolve settles a record's open
// conflicts: "newer" retires the older records, "older" this one, "both"
// nothing; "open" restores what the settlement retired and reopens them.
async function mockCore(page: Page, memories: Core[], budget = 3000) {
  const state = { memories, changes: [] as object[], posts: [] as object[], resolves: [] as object[] };
  let clock = 100;
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 3, projects: [['shop', 2], ['engine', 1]], langs: [], domains: [], types: [], topics: [], layers: [] } }));
  await page.route('**/api/projects', route => route.fulfill({ json: { projects: [{ name: 'shop', count: 2, color: '#888888' }, { name: 'engine', count: 1, color: '#888888' }], total: 3 } }));
  await page.route('**/api/core', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      state.posts.push(body);
      const added: Core = { id: `n${state.memories.length}`, text: body.text, kind: body.kind, scope: body.scope, tier: 'guideline',
        amends: '', covers: body.covers ?? [], conflicts: [], state: 'active', evidence: [], origin: 'user',
        created_at: ++clock, updated_at: clock, approved_at: clock, device: 'laptop' };
      state.memories.push(added);
      return route.fulfill({ json: added });
    }
    const used = state.memories.filter(m => m.tier === 'guideline' && m.state === 'active' && m.scope === '*')
      .reduce((n, m) => n + tokens(m.text), 0);
    await route.fulfill({ json: { memories: state.memories, budget, used, kinds: ['preference', 'instruction', 'fact'], max_text: 600 } });
  });
  await page.route('**/api/core/*', async route => {
    const id = route.request().url().split('/').pop()!;
    const change = route.request().postDataJSON();
    const memory = state.memories.find(m => m.id === id)!;
    if (change.state === 'proposed' && (memory.tier === 'record' || memory.origin === 'user')) {
      return route.fulfill({ status: 400, json: { detail: 'cannot be proposed' } });
    }
    state.changes.push({ id, ...change });
    const amended = memory.amends ? state.memories.find(m => m.id === memory.amends) : undefined;
    if (amended && memory.state === 'proposed' && change.state === 'active' && amended.state === 'active') {
      Object.assign(amended, { state: 'retired', updated_at: ++clock });
    } else if (amended && memory.state === 'active' && change.state === 'proposed' && amended.state === 'retired') {
      Object.assign(amended, { state: 'active', updated_at: ++clock });
    }
    Object.assign(memory, change, { updated_at: ++clock });
    await route.fulfill({ json: memory });
  });
  await page.route('**/api/core/*/resolve', async route => {
    const id = route.request().url().split('/').at(-2)!;
    const { keep } = route.request().postDataJSON();
    state.resolves.push({ id, keep });
    const memory = state.memories.find(m => m.id === id)!;
    const changed = [memory];
    const put = (m: Core | undefined, to: string) => { if (m) { Object.assign(m, { state: to, updated_at: ++clock }); changed.push(m); } };
    if (keep === 'open') {
      for (const c of memory.conflicts.filter(c => c.resolution)) {
        if (c.resolution === 'newer') put(state.memories.find(m => m.id === c.id), 'active');
        if (c.resolution === 'older') Object.assign(memory, { state: 'active' });
        c.resolution = '';
      }
    } else {
      for (const c of memory.conflicts.filter(c => !c.resolution)) {
        if (keep === 'newer') put(state.memories.find(m => m.id === c.id), 'retired');
        c.resolution = keep;
      }
      if (keep === 'older') Object.assign(memory, { state: 'retired' });
    }
    Object.assign(memory, { updated_at: ++clock });
    await route.fulfill({ json: { changed } });
  });
  return state;
}

const proposal: Core = { id: 'p1', text: 'Prefers answers that lead with the result', kind: 'preference', scope: '*', state: 'proposed',
  tier: 'guideline', amends: '', covers: [], conflicts: [], evidence: [{ memory_id: 'm1', quote: 'just give me the answer first' }], origin: 'organize',
  created_at: 1, updated_at: 1, approved_at: null, device: 'laptop' };
const guideline: Core = { ...proposal, id: 'g1', text: 'When suggesting recipes, keep them gluten-free', state: 'active',
  evidence: [], approved_at: 1 };
const record: Core = { ...proposal, id: 'r1', text: 'Prefers approximate portion sizes for 9-month-old baby snacks', kind: 'fact',
  tier: 'record', state: 'active', origin: 'agent', evidence: [{ source: 'conversation/abc', project: 'kitchen', quote: 'roughly a handful is fine' }],
  created_at: 5, updated_at: 5 };

const tab = (page: Page, name: RegExp | string) => page.getByRole('tab', { name });

// A newer record that contradicts an older one (built per test: settling changes it).
const dairy = (): Core => ({ ...record, id: 'r0', text: 'Eats dairy every morning', created_at: 2, updated_at: 2, conflicts: [] });
const lactose = (over: Partial<Core> = {}): Core => ({ ...record, id: 'r2', text: 'Is lactose intolerant', created_at: 6, updated_at: 6,
  conflicts: [{ id: 'r0', reason: 'Lactose intolerance rules out daily dairy', resolution: '' }], ...over });

test('Conflicts opens first while one is open; using the newer retires the earlier, and undo brings it back', async ({ page }) => {
  const state = await mockCore(page, [dairy(), lactose(), { ...proposal }]);
  await page.goto('/core/');
  await expect(tab(page, 'Conflicts, 1')).toHaveAttribute('aria-selected', 'true');
  await expect(tab(page, 'Proposed, 1')).toBeVisible();
  const row = page.getByRole('list', { name: 'Conflicts to settle' }).getByRole('listitem', { name: 'Is lactose intolerant' });
  await expect(row.getByRole('group', { name: 'Earlier' })).toContainText('Eats dairy every morning');
  await expect(row.getByRole('group', { name: 'Newer' })).toContainText('Is lactose intolerant');
  await expect(row).toContainText('Lactose intolerance rules out daily dairy');

  // Recorded marks the newer record too.
  await page.keyboard.press('4');
  await expect(page.getByRole('listitem', { name: 'Is lactose intolerant' })).toContainText('Conflicts with an earlier one');
  await expect(page.getByRole('listitem', { name: 'Eats dairy every morning' })).not.toContainText('Conflicts with an earlier one');
  await page.keyboard.press('1');

  await row.getByRole('button', { name: 'Use the newer' }).click();
  await expect.poll(() => state.resolves).toEqual([{ id: 'r2', keep: 'newer' }]);
  await expect(page.getByText('No conflicts.')).toBeVisible();
  await expect(tab(page, 'Retired, 1')).toBeVisible();
  expect(state.memories.find(m => m.id === 'r0')!.state).toBe('retired');

  await page.keyboard.press('z');
  await expect.poll(() => state.resolves.at(-1)).toEqual({ id: 'r2', keep: 'open' });
  await expect.poll(() => state.memories.find(m => m.id === 'r0')!.state).toBe('active');
  await expect(page.getByRole('listitem', { name: 'Is lactose intolerant' })).toBeVisible();
  await expect(tab(page, 'Conflicts, 1')).toBeVisible();
  await expect(tab(page, 'Retired, 0')).toBeVisible();
});

test('keeping both dismisses a conflict and retires nothing; keeping the earlier retires the newer', async ({ page }) => {
  const state = await mockCore(page, [
    dairy(), lactose(),
    { ...record, id: 'r3', text: 'Lives in Lisbon', created_at: 3, updated_at: 3, conflicts: [] },
    lactose({ id: 'r4', text: 'Moved to Porto', created_at: 7, updated_at: 7,
      conflicts: [{ id: 'r3', reason: 'A different city', resolution: '' }] }),
  ]);
  await page.goto('/core/');
  const rows = page.getByRole('list', { name: 'Conflicts to settle' }).getByRole('listitem');
  await expect(rows).toHaveCount(2);
  // Newest first: Porto, then the lactose one.
  await expect(rows.first()).toHaveAttribute('aria-label', 'Moved to Porto');
  await page.getByRole('listitem', { name: 'Is lactose intolerant' }).focus();
  await page.keyboard.press('b');
  await expect.poll(() => state.resolves).toEqual([{ id: 'r2', keep: 'both' }]);
  await expect(rows).toHaveCount(1);
  expect(state.memories.filter(m => m.state === 'retired')).toEqual([]);

  await page.getByRole('listitem', { name: 'Moved to Porto' }).getByRole('button', { name: 'Keep the earlier' }).click();
  await expect.poll(() => state.resolves.at(-1)).toEqual({ id: 'r4', keep: 'older' });
  await expect(page.getByText('No conflicts.')).toBeVisible();
  expect(state.memories.find(m => m.id === 'r4')!.state).toBe('retired');
  expect(state.memories.find(m => m.id === 'r3')!.state).toBe('active');
  await expect(tab(page, 'Recorded, 3')).toBeVisible();
  await expect(tab(page, 'Retired, 1')).toBeVisible();
});

test('a rewrite from newer information overwrites the guideline, or is left as is', async ({ page }) => {
  const rewrite = 'When suggesting recipes, keep them gluten-free and lactose-free';
  const state = await mockCore(page, [{ ...guideline }, { ...proposal, id: 'p2', text: rewrite, amends: 'g1', created_at: 2,
    conflicts: [{ id: 'g1', reason: 'Newer statements rule out dairy too', resolution: '' }] }]);
  await page.goto('/core/');
  await expect(tab(page, 'Conflicts, 1')).toHaveAttribute('aria-selected', 'true');
  // A rewrite settles under Conflicts, not Proposed.
  await expect(tab(page, 'Proposed, 0')).toBeVisible();
  const row = page.getByRole('listitem', { name: rewrite });
  await expect(row.getByRole('group', { name: 'Your guideline' })).toContainText(guideline.text);
  await expect(row.getByRole('group', { name: 'Rewrite from newer information' })).toContainText(rewrite);
  await expect(row).toContainText('Newer statements rule out dairy too');
  await expect(row.getByRole('button', { name: 'Keep both' })).toHaveCount(0);

  await row.getByRole('button', { name: 'Overwrite' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p2', state: 'active' }]);
  await expect(page.getByText('No conflicts.')).toBeVisible();
  expect(state.memories.find(m => m.id === 'g1')!.state).toBe('retired');
  await page.keyboard.press('2');
  const active = page.getByRole('list', { name: 'Active guidelines' });
  await expect(active.getByRole('listitem', { name: rewrite })).toBeVisible();
  await expect(active.getByRole('listitem', { name: guideline.text, exact: true })).toHaveCount(0);

  await page.keyboard.press('z');
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'p2', state: 'proposed' });
  await expect.poll(() => state.memories.find(m => m.id === 'g1')!.state).toBe('active');
  await expect(tab(page, 'Conflicts, 1')).toBeVisible();
  await page.keyboard.press('1');
  await page.getByRole('listitem', { name: rewrite }).getByRole('button', { name: 'Leave as is' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'p2', state: 'retired' });
  await expect(page.getByText('No conflicts.')).toBeVisible();
  expect(state.memories.find(m => m.id === 'g1')!.state).toBe('active');
  await expect(tab(page, 'Guidelines, 1')).toBeVisible();
  await expect(tab(page, 'Proposed, 0')).toBeVisible();
});

test('the tabs: Proposed opens while any wait, else Guidelines, and 1–5 switch', async ({ page }) => {
  await mockCore(page, [{ ...proposal }, { ...guideline }, { ...record }, { ...record, id: 'r2', text: 'Uses a gas hob', state: 'retired' }]);
  await page.goto('/core/');
  await expect(page.getByRole('heading', { name: /Core memories/ })).toBeVisible();
  await expect(tab(page, 'Proposed, 1')).toHaveAttribute('aria-selected', 'true');
  await expect(tab(page, 'Guidelines, 1')).toBeVisible();
  await expect(tab(page, 'Recorded, 1')).toBeVisible();
  await expect(tab(page, 'Retired, 1')).toBeVisible();
  await page.keyboard.press('2');
  await expect(tab(page, /Guidelines/)).toHaveAttribute('aria-selected', 'true');
  await expect(page).toHaveURL(/#guidelines$/);
  await expect(page.getByRole('list', { name: 'Active guidelines' }).getByRole('listitem', { name: guideline.text })).toBeVisible();
  await page.keyboard.press('4');
  await expect(page).toHaveURL(/#recorded$/);
  const recorded = page.getByRole('list', { name: 'Recorded core memories' }).getByRole('listitem', { name: record.text });
  await expect(recorded).toContainText('“roughly a handful is fine”');
  await expect(recorded).toContainText('From an agent · kitchen');
  await page.keyboard.press('5');
  await expect(page).toHaveURL(/#retired$/);
  await expect(page.getByRole('listitem', { name: 'Uses a gas hob' })).toContainText('Recorded');
  await page.keyboard.press('3');
  await expect(tab(page, /Proposed/)).toHaveAttribute('aria-selected', 'true');

  // Nothing waits: Guidelines opens.
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockCore(page, [{ ...guideline }, { ...record }]);
  await page.goto('/core/');
  await expect(tab(page, 'Guidelines, 1')).toHaveAttribute('aria-selected', 'true');
  await expect(tab(page, 'Proposed, 0')).toBeVisible();
});

test('a proposal is approved with its evidence and token cost, and the approval undone', async ({ page }) => {
  const state = await mockCore(page, [{ ...proposal }]);
  await page.goto('/core/');
  const proposed = page.getByRole('listitem', { name: proposal.text });
  await expect(proposed).toContainText('“just give me the answer first”');
  await expect(proposed).toContainText('From Organize');
  await expect(proposed).toContainText(`+${tokens(proposal.text)} tokens`);
  await tab(page, /Guidelines/).click();
  await expect(page.getByText('No guidelines yet.')).toBeVisible();
  await expect(page.getByText(/Organize drafts guidelines from your recorded core memories/)).toBeVisible();
  await tab(page, /Proposed/).click();
  await proposed.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', state: 'active' }]);
  await expect(tab(page, 'Guidelines, 1')).toBeVisible();
  // The tab stays where the decision was made.
  await expect(tab(page, 'Proposed, 0')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('No proposals.')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', state: 'active' }, { id: 'p1', state: 'proposed' }]);
  await expect(page.getByRole('listitem', { name: proposal.text })).toBeVisible();
  await expect(tab(page, 'Proposed, 1')).toBeVisible();
  await expect(tab(page, 'Guidelines, 0')).toBeVisible();
});

test('an amendment shows what it replaces; approving retires the old one, undo restores it, rejecting leaves it', async ({ page }) => {
  const rewrite = 'When suggesting recipes, keep them gluten-free and lactose-free';
  const state = await mockCore(page, [{ ...guideline }, { ...proposal, id: 'p2', text: rewrite, amends: 'g1', created_at: 2 }]);
  await page.goto('/core/');
  const proposed = page.getByRole('listitem', { name: rewrite });
  await expect(proposed.getByRole('group', { name: 'Replaces' })).toContainText(guideline.text);
  // Approving adds only what the rewrite takes beyond the guideline it replaces.
  await expect(proposed).toContainText(`+${tokens(rewrite) - tokens(guideline.text)} tokens`);
  await proposed.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p2', state: 'active' }]);
  await expect(tab(page, 'Guidelines, 1')).toBeVisible();
  await expect(tab(page, 'Retired, 1')).toBeVisible();
  await page.keyboard.press('2');
  const active = page.getByRole('list', { name: 'Active guidelines' });
  await expect(active.getByRole('listitem', { name: rewrite })).toBeVisible();
  await expect(active.getByRole('listitem', { name: guideline.text, exact: true })).toHaveCount(0);
  await page.keyboard.press('5');
  await expect(page.getByRole('listitem', { name: guideline.text, exact: true })).toContainText('Guideline');
  expect(state.memories.find(m => m.id === 'g1')!.state).toBe('retired');

  await page.keyboard.press('z');
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'p2', state: 'proposed' });
  await expect.poll(() => state.memories.find(m => m.id === 'g1')!.state).toBe('active');
  await expect(tab(page, 'Retired, 0')).toBeVisible();
  await page.keyboard.press('3');
  await page.getByRole('listitem', { name: rewrite }).getByRole('button', { name: 'Reject' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'p2', state: 'retired' });
  await page.keyboard.press('2');
  await expect(page.getByRole('list', { name: 'Active guidelines' }).getByRole('listitem', { name: guideline.text, exact: true })).toBeVisible();
  expect(state.memories.find(m => m.id === 'g1')!.state).toBe('active');
});

test('a guideline is made from recorded core memories, one or several, and lists what it generalises', async ({ page }) => {
  const second = { ...record, id: 'r2', text: 'Asked for a gluten-free pancake recipe', kind: 'preference', updated_at: 4 };
  const state = await mockCore(page, [{ ...record }, second]);
  await page.goto('/core/#recorded');
  const rows = page.getByRole('list', { name: 'Recorded core memories' }).getByRole('listitem');
  await expect(rows).toHaveCount(2);
  await rows.first().getByRole('button', { name: 'Make a guideline' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Make a guideline' })).toBeVisible();
  await expect(dialog.getByRole('textbox', { name: 'New guideline' })).toHaveValue(record.text);
  await expect(dialog.getByRole('combobox', { name: 'Kind of the new guideline' })).toHaveValue('fact');
  await dialog.getByRole('textbox', { name: 'New guideline' }).fill('Has a baby; portion sizes can be approximate');
  await dialog.getByRole('button', { name: 'Add guideline' }).click();
  await expect(dialog).toHaveCount(0);
  await expect.poll(() => state.posts).toEqual([{ text: 'Has a baby; portion sizes can be approximate', kind: 'fact', scope: '*', covers: ['r1'] }]);
  // The page stays with the records; the one used is now in a guideline.
  await expect(tab(page, /Recorded/)).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('listitem', { name: record.text })).toContainText('In a guideline');
  await expect(page.getByRole('listitem', { name: second.text })).not.toContainText('In a guideline');

  // Several at once: the first one's text, covering all of them.
  await page.getByRole('checkbox', { name: `Select ${record.text}` }).click();
  await page.getByRole('checkbox', { name: `Select ${second.text}` }).click();
  await page.getByRole('toolbar', { name: 'Selected rows' }).getByRole('button', { name: 'Make a guideline from 2' }).click();
  await expect(dialog.getByRole('textbox', { name: 'New guideline' })).toHaveValue(record.text);
  await expect(dialog.getByRole('group', { name: 'Recorded core memories it generalises' })).toContainText(second.text);
  await dialog.getByRole('textbox', { name: 'New guideline' }).fill('Keep recipes gluten-free and baby-friendly');
  await dialog.getByRole('button', { name: 'Add guideline' }).click();
  await expect.poll(() => state.posts.at(-1)).toEqual({ text: 'Keep recipes gluten-free and baby-friendly', kind: 'fact', scope: '*', covers: ['r1', 'r2'] });
  await expect(page.getByRole('toolbar', { name: 'Selected rows' })).toHaveCount(0);

  // `g` makes one from the current row.
  await page.getByRole('listitem', { name: second.text }).focus();
  await page.keyboard.press('g');
  await expect(dialog.getByRole('textbox', { name: 'New guideline' })).toHaveValue(second.text);
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);

  await page.keyboard.press('2');
  const made = page.getByRole('listitem', { name: 'Keep recipes gluten-free and baby-friendly' });
  await made.getByRole('button', { name: 'Generalises 2 recorded' }).click();
  const covers = made.getByRole('group', { name: 'Recorded core memories it generalises' });
  await expect(covers).toContainText(record.text);
  await expect(covers).toContainText(second.text);
});

test('a guideline written by hand is active at once', async ({ page }) => {
  const state = await mockCore(page, [{ ...proposal }]);
  await page.goto('/core/');
  await page.getByRole('button', { name: 'Write a guideline' }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Write a guideline' })).toBeVisible();
  await dialog.getByRole('textbox', { name: 'New guideline' }).fill('Commit only when I ask');
  await dialog.getByRole('combobox', { name: 'Kind of the new guideline' }).selectOption('instruction');
  await dialog.getByRole('combobox', { name: 'Where the new guideline applies' }).selectOption('shop');
  await dialog.getByRole('button', { name: 'Add guideline' }).click();
  await expect(dialog).toHaveCount(0);
  await expect.poll(() => state.posts).toEqual([{ text: 'Commit only when I ask', kind: 'instruction', scope: 'shop' }]);
  await expect(tab(page, /Guidelines/)).toHaveAttribute('aria-selected', 'true');
  const added = page.getByRole('listitem', { name: 'Commit only when I ask' });
  await expect(added.getByRole('combobox', { name: /^Where/ })).toHaveValue('shop');
  await expect(added).toContainText('Written by you');
});

test('the budget meter counts every-project guidelines in tokens, never records', async ({ page }) => {
  // Three guidelines of 10 tokens for every project in a budget of 20: the
  // newest does not fit. A longer record, active and for every project, and a
  // guideline for one project do not count.
  const ten = (n: number) => `Guideline number ${n} for every session`.padEnd(37, '.');
  await mockCore(page, [
    { ...guideline, id: 'g1', text: ten(1), created_at: 1 },
    { ...guideline, id: 'g2', text: ten(2), created_at: 2 },
    { ...guideline, id: 'g3', text: ten(3), created_at: 3 },
    { ...guideline, id: 'g4', text: 'Uses pnpm in the shop project', scope: 'shop', created_at: 4 },
    { ...record, text: 'A recorded core memory that is long enough to take a lot of tokens if it ever counted', scope: '*' },
    { ...proposal, text: 'Prefers short answers' },
  ], 20);
  await page.goto('/core/');
  await expect(page.getByText('Each session receives up to 20 tokens of guidelines')).toBeVisible();
  const used = page.getByText(/used by the guidelines for every project/);
  await expect(used).toContainText('30 used by the guidelines for every project');
  await expect(used).toContainText("1 every-project guideline doesn't fit in a session");
  await expect(page.getByRole('progressbar', { name: 'Session budget used' })).toBeVisible();
  await expect(page.getByRole('listitem', { name: 'Prefers short answers' })).toContainText(`+${tokens('Prefers short answers')} tokens · over budget`);

  // Retiring the newest guideline brings the rest within the budget.
  await page.keyboard.press('2');
  await page.getByRole('listitem', { name: ten(3) }).getByRole('button', { name: 'Retire' }).click();
  await expect(used).toContainText('20 used by the guidelines for every project');
  await expect(used).not.toContainText("doesn't fit");
});

test('guidelines and records are retired and restored', async ({ page }) => {
  const state = await mockCore(page, [{ ...guideline }, { ...record }]);
  await page.goto('/core/');
  await page.getByRole('listitem', { name: guideline.text }).getByRole('button', { name: 'Retire' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'g1', state: 'retired' }]);
  await page.keyboard.press('4');
  await page.getByRole('listitem', { name: record.text }).getByRole('button', { name: 'Retire' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'r1', state: 'retired' });
  await expect(page.getByText('Nothing recorded yet.')).toBeVisible();
  await page.keyboard.press('5');
  const rows = page.getByRole('list', { name: 'Retired core memories' }).getByRole('listitem');
  await expect(rows).toHaveCount(2);
  await expect(page.getByRole('listitem', { name: guideline.text })).toContainText('Guideline');
  await expect(page.getByRole('listitem', { name: record.text })).toContainText('Recorded');
  // The layer tells them apart.
  await page.getByRole('combobox', { name: 'Layer' }).click();
  await page.getByRole('option', { name: 'Recorded', exact: true }).click();
  await expect(rows).toHaveCount(1);
  await page.getByRole('listitem', { name: record.text }).getByRole('button', { name: 'Restore' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'r1', state: 'active' });
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.getByRole('listitem', { name: guideline.text }).getByRole('button', { name: 'Restore' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'g1', state: 'active' });
  await expect(tab(page, 'Guidelines, 1')).toBeVisible();
  await expect(tab(page, 'Recorded, 1')).toBeVisible();
  await expect(page.getByText('Nothing retired.')).toBeVisible();
});

test('a proposal and a record can be edited', async ({ page }) => {
  const state = await mockCore(page, [{ ...proposal }, { ...record }]);
  await page.goto('/core/');
  await page.getByRole('listitem', { name: proposal.text }).getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('textbox', { name: 'Core memory text' }).fill('Lead with the result, then the reasoning');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', text: 'Lead with the result, then the reasoning' }]);
  await expect(page.getByRole('listitem', { name: 'Lead with the result, then the reasoning' })).toBeVisible();
  await page.keyboard.press('4');
  await page.getByRole('listitem', { name: record.text }).getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('textbox', { name: 'Core memory text' }).fill('Prefers approximate portion sizes for baby snacks');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'r1', text: 'Prefers approximate portion sizes for baby snacks' });
  await expect(page.getByRole('listitem', { name: 'Prefers approximate portion sizes for baby snacks' })).toBeVisible();
});

test('proposals are paged and filtered by kind', async ({ page }) => {
  const kinds = ['preference', 'instruction', 'fact'];
  await mockCore(page, Array.from({ length: 25 }, (_, i) => ({
    ...proposal, id: `p${i}`, text: `Proposal number ${i} about how to work`, kind: kinds[i % 3], created_at: 100 - i, updated_at: 100 - i,
  })));
  await page.goto('/core/');
  const rows = page.getByRole('list', { name: 'Proposed guidelines' }).getByRole('listitem');
  const pager = page.getByRole('navigation', { name: 'Pagination' });
  await expect(rows).toHaveCount(20);
  await expect(pager).toContainText('1–20 of 25');
  await pager.getByRole('button', { name: 'Next page' }).click();
  await expect(rows).toHaveCount(5);
  await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: /Instruction/ }).click();
  await expect(rows).toHaveCount(8);
  await expect(pager).toContainText('1–8 of 8');
  await expect(rows.first()).toHaveAttribute('aria-label', 'Proposal number 1 about how to work');
});

test('the keyboard approves and rejects', async ({ page }) => {
  const state = await mockCore(page, [
    { ...proposal, id: 'p1', created_at: 3 }, { ...proposal, id: 'p2', text: 'Uses pnpm, never npm', created_at: 2 },
    { ...proposal, id: 'p3', text: 'Writes British spelling', created_at: 1 },
  ]);
  await page.goto('/core/');
  const rows = page.getByRole('list', { name: 'Proposed guidelines' }).getByRole('listitem');
  await expect(rows).toHaveCount(3);
  await page.keyboard.press('j');
  await expect(rows.first()).toBeFocused();
  await page.keyboard.press('a');
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', state: 'active' }]);
  await expect(rows.first()).toHaveAttribute('aria-label', 'Uses pnpm, never npm');
  await expect(rows.first()).toBeFocused();
  await page.keyboard.press('r');
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'p2', state: 'retired' });
  await expect(rows).toHaveCount(1);
  await page.keyboard.press('e');
  await expect(page.getByRole('textbox', { name: 'Core memory text' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('textbox', { name: 'Core memory text' })).toHaveCount(0);
});
