import { test, expect, type Page } from '@playwright/test';

type Core = { id: string; text: string; kind: string; scope: string; state: string; evidence: object[]; origin: string;
  created_at: number; updated_at: number; approved_at: number | null };

// Mirrors imprint/api.py's /api/core: the user adds active ones; PATCH edits,
// approves, retires and restores.
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
  await expect(page.getByText('No active core memories')).toBeVisible();
  const proposed = page.getByRole('listitem', { name: proposal.text });
  await expect(proposed).toContainText('“just give me the answer first”');
  await expect(proposed).toContainText('From Organize');
  await proposed.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => state.changes).toEqual([{ id: 'p1', state: 'active' }]);
  await expect(page.getByText('No active core memories')).toHaveCount(0);

  await page.getByRole('textbox', { name: 'New core memory' }).fill('Commit only when I ask');
  await page.getByRole('combobox', { name: 'Kind of the new core memory' }).selectOption('instruction');
  await page.getByRole('combobox', { name: 'Where the new core memory applies' }).selectOption('shop');
  await page.getByRole('button', { name: 'Add' }).click();
  const added = page.getByRole('listitem', { name: 'Commit only when I ask' });
  await expect(added.getByRole('combobox', { name: /^Where/ })).toHaveValue('shop');
  await expect(page.getByRole('textbox', { name: 'New core memory' })).toHaveValue('');
  expect(state.memories.at(-1)).toMatchObject({ text: 'Commit only when I ask', kind: 'instruction', scope: 'shop', state: 'active' });

  await page.getByRole('listitem', { name: proposal.text }).getByRole('button', { name: 'Retire' }).click();
  await page.getByRole('button', { name: /Retired and rejected \(1\)/ }).click();
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
