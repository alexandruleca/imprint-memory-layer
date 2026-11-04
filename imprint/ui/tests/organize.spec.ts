import { test, expect, type Page } from '@playwright/test';

const proposal = (source: string, target: string, origin = 'Desktop', method = 'name', quote = `mentions ${target}`) => ({
  workspace: 'default', origin, source, target, method, evidence: [{ quote }], preview: '', chunks: 2,
  status: 'pending', created_at: 1, decided_at: null,
});

// Mirrors imprint/api.py's /api/organize and /api/organize/decide.
async function mockOrganize(page: Page) {
  const state = {
    proposals: [proposal('Desktop/call.txt', 'checkout-service'), proposal('note/retries', 'checkout-service', ''),
                proposal('Desktop/avatar.md', 'echo-ui', 'Desktop', 'local-model', 'Render the avatar with WebGL')],
    decisions: [] as object[], queued: [] as object[],
  };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/organize', route => route.fulfill({ json: {
    workspace: 'default', proposals: state.proposals,
    profiles: [{ project: 'echo-ui', text: 'Project profile: echo-ui\n\nThe voice companion\'s 3D head.', updated: 1 }],
    last_run: { finished_at: Date.now() / 1000 - 120, summary: JSON.stringify({ profiles: 1, pending: 3, core: 2, model: true }) },
  } }));
  await page.route('**/api/organize/decide', async route => {
    const body = route.request().postDataJSON();
    state.decisions.push(body);
    for (const item of body.items) {
      const held = state.proposals.find(p => p.origin === item.origin && p.source === item.source)!;
      held.status = body.accept ? 'accepted' : 'rejected';
    }
    await route.fulfill({ json: { decided: body.items.length, failed: [] } });
  });
  await page.route('**/api/commands/organize', async route => {
    state.queued.push(route.request().postDataJSON());
    await route.fulfill({ json: { job_id: 'j1', position: 0 } });
  });
  return state;
}

test('proposals are reviewed by target, one at a time or all at once', async ({ page }) => {
  const state = await mockOrganize(page);
  await page.goto('/organize/');
  await expect(page.getByRole('status')).toContainText('1 profiles, 3 moves to review, 2 core memory proposals');
  await expect(page.getByRole('link', { name: 'Review core memories' })).toHaveAttribute('href', '/core/');
  const checkout = page.locator('[aria-label="Moves to checkout-service"]');
  const fromNoProject = checkout.getByRole('listitem', { name: 'note/retries' });
  await expect(fromNoProject).toContainText('No project');
  await expect(fromNoProject).toContainText('Names the project');
  await expect(page.getByRole('listitem', { name: 'Desktop/avatar.md' })).toContainText('“Render the avatar with WebGL”');
  await expect(page.getByText("The voice companion's 3D head.")).toBeVisible();

  await fromNoProject.getByRole('button', { name: 'Move' }).click();
  await expect.poll(() => state.decisions).toEqual([{ workspace: 'default', items: [{ origin: '', source: 'note/retries' }], accept: true }]);
  await page.locator('[aria-label="Moves to echo-ui"]').getByRole('button', { name: 'Keep all' }).click();
  await expect.poll(() => state.decisions.at(-1)).toEqual({ workspace: 'default', items: [{ origin: 'Desktop', source: 'Desktop/avatar.md' }], accept: false });
  await page.getByRole('button', { name: 'Decided (2)' }).click();
  await expect(page.getByRole('listitem', { name: 'note/retries' })).toContainText('Moved');
  await expect(page.getByRole('listitem', { name: 'Desktop/avatar.md' })).toContainText('Kept');
});

test('Organize is queued, without the local helper when asked', async ({ page }) => {
  const state = await mockOrganize(page);
  await page.goto('/organize/');
  await page.getByRole('switch', { name: 'Without the local helper' }).click();
  await page.getByRole('button', { name: 'Run Organize' }).click();
  await expect.poll(() => state.queued).toEqual([{ no_model: true }]);
  await expect(page.getByText('Organize queued')).toBeVisible();
});
