import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// History, the session ledger (ADR 0233): each finished session's agent,
// model, Tasks, tokens and outcome, filtered by project and agent, with links
// to its Task and its learned conversation.

const now = Date.now() / 1000;

function session(over: Record<string, unknown>) {
  return {
    id: 's1', pid: 1, client: 'claude-code', client_version: '2.1', directory: '/home/alex/code/shop', workspace: 'default',
    started_at: now - 7200, last_seen: now - 3600, ended_at: now - 3600, connected: false, calls: 4, errors: 0, last_tool: 'search',
    board_session: 'conversation:abc', agent_name: null, project: 'shop', tasks: [], outcome: null, dispatched_role: null, model: null,
    tokens: null, conversation: null, ...over,
  };
}

async function mockHistory(page: Page) {
  const asked: URLSearchParams[] = [];
  const sessions = [
    session({ id: 'q1', agent_name: 'quill', model: 'claude-opus-5-5', tasks: ['T-aaaa1'], outcome: 'review', dispatched_role: 'work',
      tokens: { new_input: 120_000, cached_input: 4_800_000, output: 80_000 }, conversation: { source: 'conversation/abc12345', project: 'shop' } }),
    session({ id: 'n1', directory: '/home/alex/code/site', project: 'site' }),
  ];
  await page.route(/\/api\/agents\?/, route => {
    const params = new URL(route.request().url()).searchParams;
    asked.push(params);
    const shown = sessions.filter(s => (!params.get('project') || s.project === params.get('project'))
      && (!params.get('agent') || s.agent_name === params.get('agent')));
    return route.fulfill({ json: { running: [], sessions: shown, next: null, total: shown.length,
      clients: [{ client: 'claude-code', sessions: 2, connected: 0, calls: 8, errors: 0, last_seen: now - 3600 }],
      options: { projects: ['shop', 'site'], agents: ['anvil', 'quill'] } } });
  });
  await page.route('**/api/sources/read?**', route => route.fulfill({ json: {
    available: true, source: 'conversation/abc12345', project: 'shop', title: 'Fix the tax rounding', kind: 'conversation',
    details: { event_count: 1 }, blocks: [{ index: 0, role: 'user', kind: 'message', text: 'Fix the tax rounding please.', chunk: null }],
    topic: '', chunks: [], version: 'v1', integrity: null } }));
  return asked;
}

test('a History row shows its agent, model, tokens, Tasks and outcome, and links them', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockHistory(page);
  await page.goto('/agents/#history');
  const row = page.getByRole('listitem').filter({ hasText: 'quill' });
  await expect(row).toContainText('claude-opus-5-5');
  await expect(row).toContainText('5.00M tokens');
  await expect(row).toContainText('work · review');
  const plain = page.getByRole('listitem').filter({ hasText: 'site' }).filter({ hasNotText: 'quill' });
  await expect(plain).not.toContainText('tokens');
  await row.getByRole('button', { name: 'Read its conversation' }).click();
  await expect(page.getByRole('dialog').getByText('Fix the tax rounding please.')).toBeVisible();
  await page.keyboard.press('Escape');
  await row.getByRole('button', { name: 'T-aaaa1', exact: true }).click();
  await expect(page).toHaveURL(/\?task=T-aaaa1#board$/);
});

test('History filters by project and agent alongside the text filter', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockHistory(page);
  await page.goto('/agents/#history');
  await page.getByRole('combobox', { name: 'Project' }).click();
  await page.getByRole('option', { name: 'shop' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: 'quill' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Agent' }).click();
  await page.getByRole('option', { name: 'quill' }).click();
  await expect.poll(() => asked.at(-1)?.get('agent')).toBe('quill');
  expect(asked.at(-1)?.get('project')).toBe('shop');
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect.poll(() => asked.at(-1)?.get('project')).toBeNull();
});
