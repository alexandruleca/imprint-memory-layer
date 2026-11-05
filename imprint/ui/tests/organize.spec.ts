import { test, expect, type Page } from '@playwright/test';

const proposal = (source: string, target: string, origin = 'Desktop', method = 'name', quote = `mentions ${target}`,
                  over: Record<string, unknown> = {}) => ({
  workspace: 'default', origin, source, target, method, evidence: [{ quote }], preview: '', chunks: 2,
  status: 'pending', strength: 'weak', created_at: 1, decided_at: null as number | null, title: '', ...over,
});
type Proposal = ReturnType<typeof proposal>;
const item = (p: { origin: string; source: string }) => ({ origin: p.origin, source: p.source });

// Mirrors imprint/api.py's /api/organize, /api/organize/decide, /undo and
// /reopen, and the core memory count /api/core gives the status strip.
async function mockOrganize(page: Page, extra: Proposal[] = [], background = { enabled: false, auto_move: true, last_pass: null as number | null }) {
  const state = {
    proposals: [proposal('Desktop/call.txt', 'checkout-service'), proposal('note/retries', 'checkout-service', ''),
                proposal('Desktop/avatar.md', 'echo-ui', 'Desktop', 'local-model', 'Render the avatar with WebGL'), ...extra],
    decisions: [] as { items: object[]; accept: boolean }[], queued: [] as object[], undone: [] as object[], reopened: [] as object[],
    loads: 0, fail: null as string | null,
  };
  const find = (i: { origin: string; source: string }) => state.proposals.find(p => p.origin === i.origin && p.source === i.source)!;
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/core', route => route.fulfill({ json: {
    memories: [{ state: 'proposed' }, { state: 'proposed' }, { state: 'active' }], budget: 2000, used: 0, max_text: 600,
  } }));
  await page.route('**/api/organize', route => {
    state.loads++;
    return route.fulfill({ json: {
      workspace: 'default', proposals: state.proposals,
      profiles: [{ project: 'echo-ui', text: 'Project profile: echo-ui\n\nThe voice companion\'s 3D head.', updated: 1 }],
      last_run: { finished_at: Date.now() / 1000 - 120, summary: JSON.stringify({ profiles: 1, pending: 3, moved: 1, core: 2, model: true }) },
      background,
    } });
  });
  const failed = (items: { origin: string; source: string }[]) => (state.fail ? items.map(i => ({ ...i, error: state.fail })) : []);
  await page.route('**/api/organize/undo', async route => {
    const body = route.request().postDataJSON();
    state.undone.push(body);
    if (!state.fail) for (const i of body.items) find(i).status = 'rejected';
    await route.fulfill({ json: { undone: state.fail ? 0 : body.items.length, failed: failed(body.items) } });
  });
  await page.route('**/api/organize/reopen', async route => {
    const body = route.request().postDataJSON();
    state.reopened.push(body);
    for (const i of body.items) Object.assign(find(i), { status: 'pending', decided_at: null });
    await route.fulfill({ json: { reopened: body.items.length, failed: [] } });
  });
  await page.route('**/api/organize/decide', async route => {
    const body = route.request().postDataJSON();
    state.decisions.push(body);
    if (!state.fail) for (const i of body.items) Object.assign(find(i), { status: body.accept ? 'accepted' : 'rejected', decided_at: Date.now() / 1000 });
    await route.fulfill({ json: { decided: state.fail ? 0 : body.items.length, failed: failed(body.items) } });
  });
  await page.route('**/api/commands/organize', async route => {
    state.queued.push(route.request().postDataJSON());
    await route.fulfill({ json: { job_id: 'j1', position: 0 } });
  });
  return state;
}

const rail = (page: Page) => page.getByRole('navigation', { name: 'Projects with moves to review' });
const reviewList = (page: Page) => page.getByRole('list', { name: 'Moves to review' });

test('proposals are reviewed by target, one at a time or all at once', async ({ page }) => {
  const state = await mockOrganize(page);
  await page.goto('/organize/');
  await expect(page.getByLabel('Last run')).toContainText('1 profiles · 1 moved · 3 to review · 2 core memory proposals');
  await expect(page.getByLabel('How Organize runs')).toContainText('Runs when you start it.');
  await expect(page.getByRole('link', { name: /2 core memory proposals/ })).toHaveAttribute('href', '/core/#proposed');
  await expect(page.getByRole('tab', { name: 'To review, 3' })).toHaveAttribute('aria-selected', 'true');
  // The largest group opens first.
  const checkout = page.locator('[aria-label="Moves to checkout-service"]');
  const fromNoProject = checkout.getByRole('listitem', { name: 'note/retries' });
  await expect(fromNoProject).toContainText('No project');
  await expect(fromNoProject).toContainText('Names the project');

  await fromNoProject.getByRole('button', { name: 'Move', exact: true }).click();
  await expect.poll(() => state.decisions).toEqual([{ workspace: 'default', items: [{ origin: '', source: 'note/retries' }], accept: true }]);
  await expect(page.getByText('Moved note/retries to checkout-service')).toBeVisible();
  await rail(page).getByRole('button', { name: /echo-ui/ }).click();
  const echo = page.locator('[aria-label="Moves to echo-ui"]');
  await expect(echo.getByRole('listitem', { name: 'Desktop/avatar.md' })).toContainText('“Render the avatar with WebGL”');
  await echo.getByRole('button', { name: 'Keep all' }).click();
  await expect.poll(() => state.decisions.at(-1)).toEqual({ workspace: 'default', items: [{ origin: 'Desktop', source: 'Desktop/avatar.md' }], accept: false });

  await page.getByRole('tab', { name: /Profiles/ }).click();
  await expect(page.getByText("The voice companion's 3D head.")).toBeVisible();
  await page.getByRole('tab', { name: /Decided/ }).click();
  await expect(page).toHaveURL(/#decided$/);
  await expect(page.getByRole('listitem', { name: 'note/retries' })).toContainText('Moved by you');
  await expect(page.getByRole('listitem', { name: 'Desktop/avatar.md' })).toContainText('Kept here');
});

test('Organize is queued, without the local helper when asked', async ({ page }) => {
  const state = await mockOrganize(page);
  await page.goto('/organize/');
  await page.getByRole('button', { name: 'More ways to run' }).click();
  await page.getByRole('menuitem', { name: 'Run without the local helper' }).click();
  await expect.poll(() => state.queued).toEqual([{ no_model: true }]);
  await expect(page.getByText('Organize queued')).toBeVisible();
  await page.getByRole('button', { name: 'Run Organize' }).click();
  await expect.poll(() => state.queued).toEqual([{ no_model: true }, {}]);
});

test('moves Organize made by itself are listed and can be moved back', async ({ page }) => {
  const state = await mockOrganize(page, [
    proposal('Desktop/retry.txt', 'checkout-service', 'Desktop', 'name', 'checkout-service retries', { status: 'auto', strength: 'strong', decided_at: 5 }),
  ], { enabled: true, auto_move: true, last_pass: Date.now() / 1000 - 60 });
  await page.goto('/organize/');
  await expect(page.getByLabel('How Organize runs')).toContainText('Runs by itself when the queue is idle');
  await expect(page.getByRole('link', { name: 'Settings ↗' })).toHaveAttribute('href', '/settings/#settings-organize');
  await expect(page.locator('[aria-label="Moves to checkout-service"]').getByRole('listitem', { name: 'Desktop/retry.txt' })).toHaveCount(0);
  await page.getByRole('tab', { name: /Moved automatically/ }).click();
  const row = page.getByRole('listitem', { name: 'Desktop/retry.txt' });
  await expect(row).toContainText('→ checkout-service');
  await expect(row).toContainText('Only this project, 2+ times');
  await row.getByRole('button', { name: 'Move back' }).click();
  await expect.poll(() => state.undone).toEqual([{ workspace: 'default', items: [{ origin: 'Desktop', source: 'Desktop/retry.txt' }] }]);
  await expect(page.getByText('Moved Desktop/retry.txt back to Desktop')).toBeVisible();
  await expect(page.getByText('Nothing moved by itself yet.')).toBeVisible();
  await page.getByRole('tab', { name: /Decided/ }).click();
  await expect(page.getByRole('listitem', { name: 'Desktop/retry.txt' })).toContainText('Moved back');
});

test('with automatic moves off, strong proposals wait and say so', async ({ page }) => {
  await mockOrganize(page, [proposal('Desktop/retry.txt', 'checkout-service', 'Desktop', 'path', '/code/checkout', { strength: 'strong' })],
                     { enabled: false, auto_move: false, last_pass: null });
  await page.goto('/organize/');
  await expect(page.getByLabel('How Organize runs')).toContainText('Every move waits for your review.');
  await expect(page.getByRole('listitem', { name: 'Desktop/retry.txt' })).toContainText('Strong evidence');
  await page.getByRole('tab', { name: /Moved automatically/ }).click();
  await expect(page.getByText('Automatic moves are off.')).toBeVisible();
});

test('the keyboard moves, keeps and undoes, and leaves typing alone', async ({ page }) => {
  const state = await mockOrganize(page, [proposal('Desktop/plan.txt', 'checkout-service'), proposal('Desktop/todo.txt', 'checkout-service')]);
  await page.goto('/organize/');
  const rows = reviewList(page).getByRole('listitem');
  await expect(rows).toHaveCount(4);
  const names = await rows.evaluateAll(items => items.map(li => li.getAttribute('aria-label')));
  await page.keyboard.press('j');
  await expect(rows.nth(0)).toBeFocused();
  await page.keyboard.press('j');
  await expect(rows.nth(1)).toBeFocused();
  await page.keyboard.press('a');
  const second = state.proposals.find(p => p.source === names[1])!;
  await expect.poll(() => state.decisions).toEqual([{ workspace: 'default', items: [item(second)], accept: true }]);
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(1)).toHaveAttribute('aria-label', names[2]!);
  await expect(rows.nth(1)).toBeFocused();

  await page.keyboard.press('z');
  await expect.poll(() => state.reopened).toEqual([{ workspace: 'default', items: [item(second)] }]);
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(1)).toHaveAttribute('aria-label', names[1]!);
  await expect(rows.nth(1)).toBeFocused();

  await page.keyboard.press('r');
  await expect.poll(() => state.decisions.at(-1)).toEqual({ workspace: 'default', items: [item(second)], accept: false });
  await expect(rows).toHaveCount(3);

  const search = page.getByRole('searchbox', { name: 'Search' });
  await search.focus();
  await page.keyboard.press('q');
  await page.keyboard.press('a');
  await expect(search).toHaveValue('qa');
  await expect(rows).toHaveCount(0);
  await expect(page.getByText('No moves match these filters.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(search).toHaveValue('');
  await expect(rows).toHaveCount(3);
  expect(state.decisions).toHaveLength(2);
});

test('a page refills from the next one and clamps to the last', async ({ page }) => {
  const many = Array.from({ length: 45 }, (_, i) => proposal(`Desktop/n${String(i).padStart(2, '0')}.txt`, 'shop'));
  const state = await mockOrganize(page, many);
  await page.goto('/organize/');
  const rows = reviewList(page).getByRole('listitem');
  const pager = page.getByRole('navigation', { name: 'Pagination' });
  await expect(rows).toHaveCount(20);
  await expect(pager).toContainText('1–20 of 45');
  await expect(pager.getByRole('button', { name: /^Page \d$/ })).toHaveCount(3);

  await rows.first().getByRole('button', { name: 'Keep here' }).click();
  await expect(rows).toHaveCount(20);
  await expect(pager).toContainText('1–20 of 44');
  await expect(rows.first()).toHaveAttribute('aria-label', 'Desktop/n01.txt');

  await pager.getByRole('button', { name: 'Page 3' }).click();
  await expect(pager).toContainText('41–44 of 44');
  await page.getByRole('checkbox', { name: 'Select all on this page' }).click();
  await page.getByRole('button', { name: 'Keep 4 here' }).click();
  await expect(pager).toContainText('21–40 of 40');
  await expect(pager.getByRole('button', { name: 'Page 2' })).toHaveAttribute('aria-current', 'page');
  await expect.poll(() => state.decisions.at(-1)?.items.length).toBe(4);

  await page.getByRole('searchbox', { name: 'Search' }).fill('Desktop/n');
  await expect(pager).toContainText('1–20 of 40');
  await expect(pager.getByRole('button', { name: 'Page 1' })).toHaveAttribute('aria-current', 'page');
});

test('a whole page moves at once, in requests of ten, and refills', async ({ page }) => {
  const many = Array.from({ length: 25 }, (_, i) => proposal(`Desktop/m${String(i).padStart(2, '0')}.txt`, 'shop'));
  const state = await mockOrganize(page, many);
  await page.goto('/organize/');
  const rows = reviewList(page).getByRole('listitem');
  await expect(rows).toHaveCount(20);
  await page.getByRole('checkbox', { name: 'Select all on this page' }).click();
  await expect(page.getByText('20 on this page selected')).toBeVisible();
  await page.getByRole('button', { name: 'Move 20' }).click();
  await expect.poll(() => state.decisions.map(d => d.items.length)).toEqual([10, 10]);
  expect(state.decisions.every(d => d.accept)).toBe(true);
  await expect(rows).toHaveCount(5);
  await expect(page.getByRole('navigation', { name: 'Pagination' })).toContainText('1–5 of 5');
});

test('a finished group offers the next one', async ({ page }) => {
  await mockOrganize(page);
  await page.goto('/organize/');
  await page.locator('[aria-label="Moves to checkout-service"]').getByRole('button', { name: 'Move all 2' }).click();
  await expect(page.getByRole('heading', { name: 'checkout-service is done.' })).toBeVisible();
  const next = page.getByRole('button', { name: /Next: echo-ui · 1/ });
  await expect(next).toBeFocused();
  await page.keyboard.press('Enter');
  const echo = page.locator('[aria-label="Moves to echo-ui"]');
  await expect(echo.getByRole('listitem', { name: 'Desktop/avatar.md' })).toBeFocused();
});

test('at 800 by 600 the rail is a select and nothing scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 600 });
  await mockOrganize(page, Array.from({ length: 30 }, (_, i) => proposal(`conversation/${i}abcdef`, 'transaction-gateway', 'unassigned', 'name',
    'a long quote that mentions transaction-gateway and keeps going well past the width of the row', { title: `Why does the gateway time out after deploy number ${i}?` })));
  await page.goto('/organize/');
  await expect(page.getByRole('combobox', { name: 'Project' })).toBeVisible();
  await expect(rail(page)).toBeHidden();
  await expect(page.getByRole('tab', { name: 'To review, 33' })).toContainText('Review');
  const rows = reviewList(page).getByRole('listitem');
  await expect(rows.first()).toBeInViewport();
  await expect(rows.first()).toContainText('Why does the gateway time out after deploy number 0?');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await page.locator('#main-content').evaluate(main => main.scrollWidth <= main.clientWidth)).toBe(true);
  await page.getByRole('combobox', { name: 'Project' }).click();
  await page.getByRole('option', { name: 'checkout-service (2)' }).click();
  await expect(page.locator('[aria-label="Moves to checkout-service"]').getByRole('listitem')).toHaveCount(2);
});

test('a move that fails comes back with its error and a retry', async ({ page }) => {
  const state = await mockOrganize(page);
  state.fail = 'An ingestion or maintenance job is running; retry after it finishes';
  await page.goto('/organize/');
  const row = page.getByRole('listitem', { name: 'note/retries' });
  await row.getByRole('button', { name: 'Move', exact: true }).click();
  await expect(row.getByRole('alert')).toContainText('An ingestion or maintenance job is running');
  await expect(page.getByText('1 could not move')).toBeVisible();
  state.fail = null;
  await row.getByRole('button', { name: 'Retry' }).click();
  await expect.poll(() => state.decisions.length).toBe(2);
  await expect(row).toHaveCount(0);
});

test('a running Organize shows its progress, holds moves and brings its results in', async ({ page }) => {
  const state = await mockOrganize(page);
  let running = true;
  await page.route('**/api/queue**', route => route.fulfill({ json: {
    active: running ? { id: 'j1', command: 'organize', label: 'Organize', status: 'running', body: {}, pid: 1, pgid: 1, exit_code: null,
                        error: null, created_at: 1, started_at: 1, ended_at: null, phase: 'organize', processed: 2, total: 5, unit: 'steps' } : null,
    queued: [], recent: [],
  } }));
  await page.goto('/organize/');
  const strip = page.getByLabel('How Organize runs');
  await expect(strip).toContainText('Organizing');
  await expect(strip).toContainText('2/5 steps');
  await expect(page.getByRole('progressbar', { name: 'Organize progress' })).toBeVisible();
  await expect(strip).toContainText('Moves wait while Organize runs');
  await expect(page.getByRole('listitem', { name: 'note/retries' }).getByRole('button', { name: 'Move', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Run Organize' })).toBeDisabled();

  const loads = state.loads;
  state.proposals.push(proposal('Desktop/new.txt', 'checkout-service'));
  running = false;
  await expect(page.getByText('Organize finished: 1 new to review')).toBeVisible();
  expect(state.loads).toBeGreaterThan(loads);
  await expect(page.getByRole('listitem', { name: 'Desktop/new.txt' })).toBeVisible();
  await expect(strip).toContainText('Runs when you start it.');
});

test('a decision can be reopened from Decided', async ({ page }) => {
  const state = await mockOrganize(page, [proposal('Desktop/old.txt', 'echo-ui', 'Desktop', 'name', 'echo-ui', { status: 'accepted', decided_at: 10 })]);
  await page.goto('/organize/#decided');
  const row = page.getByRole('listitem', { name: 'Desktop/old.txt' });
  await expect(row).toContainText('Moved by you');
  await row.getByRole('button', { name: 'Reopen' }).click();
  await expect.poll(() => state.reopened).toEqual([{ workspace: 'default', items: [{ origin: 'Desktop', source: 'Desktop/old.txt' }] }]);
  await expect(page.getByText('Nothing decided yet.')).toBeVisible();
  await page.getByRole('tab', { name: /To review/ }).click();
  await rail(page).getByRole('button', { name: /echo-ui/ }).click();
  await expect(page.getByRole('listitem', { name: 'Desktop/old.txt' })).toBeVisible();
});
