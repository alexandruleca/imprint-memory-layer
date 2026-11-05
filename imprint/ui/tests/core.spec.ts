import { test, expect, type Page } from '@playwright/test';

type Core = { id: string; text: string; kind: string; scope: string; state: string; evidence: object[]; origin: string;
  created_at: number; updated_at: number; approved_at: number | null };

// Mirrors imprint/api.py's /api/core: the user adds active ones; PATCH edits,
// approves, retires, restores, and returns a proposal to proposed (an undo).
async function mockCore(page: Page, memories: Core[]) {
  const state = { memories, changes: [] as object[] };
  let clock = 100;
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 3, projects: [['shop', 2], ['engine', 1]], langs: [], domains: [], types: [], topics: [], layers: [] } }));
  await page.route('**/api/core', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      const added = { id: `n${state.memories.length}`, ...body, state: 'active', evidence: [], origin: 'user',
        created_at: ++clock, updated_at: clock, approved_at: clock };
      state.memories.push(added);
      return route.fulfill({ json: added });
    }
    const used = state.memories.filter(m => m.state === 'active' && m.scope === '*').reduce((n, m) => n + m.text.length + 3, 0);
    await route.fulfill({ json: { memories: state.memories, budget: 2000, used, max_text: 600 } });
  });
  await page.route('**/api/core/*', async route => {
    const id = route.request().url().split('/').pop()!;
    const change = route.request().postDataJSON();
    state.changes.push({ id, ...change });
    const memory = state.memories.find(m => m.id === id)!;
    Object.assign(memory, change, { updated_at: ++clock });
    await route.fulfill({ json: memory });
  });
  return state;
}

const proposal: Core = { id: 'p1', text: 'Prefers answers that lead with the result', kind: 'preference', scope: '*', state: 'proposed',
  evidence: [{ memory_id: 'm1', quote: 'just give me the answer first' }], origin: 'organize', created_at: 1, updated_at: 1, approved_at: null };

test('core memories are added, proposals approved with their evidence, and retired ones restored', async ({ page }) => {
  const state = await mockCore(page, [{ ...proposal }]);
  await page.goto('/core/');
  await expect(page.getByRole('heading', { name: /Core memories/ })).toBeVisible();
  // Proposed opens while anything waits.
  await expect(page.getByRole('tab', { name: 'Proposed, 1' })).toHaveAttribute('aria-selected', 'true');
  const proposed = page.getByRole('listitem', { name: proposal.text });
  await expect(proposed).toContainText('“just give me the answer first”');
  await expect(proposed).toContainText('From Organize');
  await expect(proposed).toContainText(`+${proposal.text.length + 3} chars`);
  await page.getByRole('tab', { name: /Active/ }).click();
  await expect(page.getByText('No active core memories')).toBeVisible();
  await page.getByRole('tab', { name: /Proposed/ }).click();
  await proposed.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', state: 'active' }]);
  await expect(page.getByText('No proposals.')).toBeVisible();
  await page.getByRole('tab', { name: /Active/ }).click();
  await expect(page.getByText('No active core memories')).toHaveCount(0);

  await page.getByRole('button', { name: 'Add core memory' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'New core memory' }).fill('Commit only when I ask');
  await dialog.getByRole('combobox', { name: 'Kind of the new core memory' }).selectOption('instruction');
  await dialog.getByRole('combobox', { name: 'Where the new core memory applies' }).selectOption('shop');
  await dialog.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const added = page.getByRole('listitem', { name: 'Commit only when I ask' });
  await expect(added.getByRole('combobox', { name: /^Where/ })).toHaveValue('shop');
  expect(state.memories.at(-1)).toMatchObject({ text: 'Commit only when I ask', kind: 'instruction', scope: 'shop', state: 'active' });

  await page.getByRole('listitem', { name: proposal.text }).getByRole('button', { name: 'Retire' }).click();
  await page.getByRole('tab', { name: /Retired/ }).click();
  await page.getByRole('listitem', { name: proposal.text }).getByRole('button', { name: 'Restore' }).click();
  await expect.poll(() => state.changes.at(-1)).toEqual({ id: 'p1', state: 'active' });
  await expect(page.getByText(/used by the ones for every project/)).toContainText(`${proposal.text.length + 3} used`);
});

test('a proposal can be edited before it is approved', async ({ page }) => {
  const state = await mockCore(page, [{ ...proposal }]);
  await page.goto('/core/');
  const proposed = page.getByRole('listitem', { name: proposal.text });
  await proposed.getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('textbox', { name: 'Core memory text' }).fill('Lead with the result, then the reasoning');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', text: 'Lead with the result, then the reasoning' }]);
  await expect(page.getByRole('listitem', { name: 'Lead with the result, then the reasoning' })).toBeVisible();
});

test('an approval is undone back to a proposal', async ({ page }) => {
  const state = await mockCore(page, [{ ...proposal }]);
  await page.goto('/core/');
  await page.getByRole('listitem', { name: proposal.text }).getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByRole('tab', { name: 'Active, 1' })).toBeVisible();
  // The tab stays where the decision was made.
  await expect(page.getByRole('tab', { name: 'Proposed, 0' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('No proposals.')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', state: 'active' }, { id: 'p1', state: 'proposed' }]);
  await expect(page.getByRole('listitem', { name: proposal.text })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Proposed, 1' })).toBeVisible();
});

test('proposals are paged and filtered by kind', async ({ page }) => {
  const kinds = ['preference', 'instruction', 'fact'];
  await mockCore(page, Array.from({ length: 25 }, (_, i) => ({
    ...proposal, id: `p${i}`, text: `Proposal number ${i} about how to work`, kind: kinds[i % 3], created_at: 100 - i, updated_at: 100 - i,
  })));
  await page.goto('/core/');
  const rows = page.getByRole('list', { name: 'Proposed core memories' }).getByRole('listitem');
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
  const rows = page.getByRole('list', { name: 'Proposed core memories' }).getByRole('listitem');
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
