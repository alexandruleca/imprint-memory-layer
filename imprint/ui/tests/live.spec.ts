import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// Live (ADR 0233): who is working on what on this device, from the API's
// /api/agents/live, which reads local records and transcripts without a model.

const now = Date.now() / 1000;

function agent(over: Record<string, unknown>) {
  return {
    id: 's1', board_session: 'conversation:c1', name: 'grimforge', client: 'claude-code', client_version: '2.1', model: 'claude-opus-5-5',
    directory: '/home/alex/code/shop', project: 'shop', workspace: 'default', pid: 100, started_at: now - 3600, last_seen: now,
    state: 'working', activity: { kind: 'testing', label: 'Testing', detail: 'pytest -q', since: now - 40, long: false },
    task: null, dispatched: null, actions: [], transcript: true, last_call_at: null,
    tokens: { context: 484_000, window: null, new_input: 1_200_000, cached_input: 48_300_000, output: 202_000, thinking: 50_000,
      cache_share: 0.976, partial: false, rate_limit: null },
    ...over,
  };
}

const report = (agents: { state: unknown }[], others: unknown[] = []) => ({
  now, device: 'device-a', agents, others,
  counts: { working: agents.filter(a => a.state === 'working').length, waiting: agents.filter(a => a.state === 'waiting').length,
    idle: agents.filter(a => a.state === 'idle').length, others: others.length },
  today: [{ client: 'claude-code', new_input: 1_000_000, cached_input: 19_000_000, output: 100_000 }],
});

const agents = () => [
  agent({ id: 'w1', name: 'grimforge', task: { id: 'T-aaaa1', title: 'Fix the tax rounding', status: 'in_progress', role: 'working',
    stalling: false, next: 'Run the tests', checkpoint_at: now - 600 },
    actions: [{ kind: 'testing', label: 'Testing', detail: 'pytest -q', at: now - 40, running: true },
      { kind: 'editing', label: 'Editing cart.py', detail: 'shop/cart.py', at: now - 90, running: false }] }),
  agent({ id: 'q1', name: 'quill', client: 'codex', model: 'gpt-6.2-sol', state: 'waiting',
    activity: { kind: 'waiting', label: 'Waiting for you', detail: '', since: now - 120, long: false },
    task: { id: 'T-bbbb2', title: 'Review the coupon field', status: 'review', role: 'reviewing', stalling: false, next: null, checkpoint_at: null },
    dispatched: { run: 'run9', role: 'review' },
    tokens: { context: 210_000, window: 475_000, new_input: 22_000, cached_input: 142_000, output: 441, thinking: 57, cache_share: 0.866,
      partial: false, rate_limit: { used_percent: 12, window_minutes: 10080, resets_at: now + 86400 } } }),
  agent({ id: 'i1', name: null, project: 'site', state: 'idle', directory: '/home/alex/code/site',
    activity: { kind: 'idle', label: 'Idle', detail: '', since: now - 45 * 60, long: false }, tokens: null, transcript: false }),
];

async function mockLive(page: Page, data = report(agents(), [{ name: 'anvil', client: 'codex', device: 'bedrock', task: 'T-cccc3',
  title: 'Upload retries', project: 'shop', status: 'in_progress', next: 'Retry on 503', progress_at: now - 300, stalling: false }])) {
  const asked: { method: string; path: string; body: unknown }[] = [];
  await page.route('**/api/agents/live', route => route.fulfill({ json: data }));
  const session = { id: 'w1', pid: 100, client: 'claude-code', client_version: '2.1', directory: '/home/alex/code/shop', workspace: 'default',
    started_at: now - 3600, last_seen: now, ended_at: null, connected: true, calls: 3, errors: 0, last_tool: 'search', agent_name: 'grimforge' };
  await page.route(/\/api\/agents\?/, route => route.fulfill({ json: { running: [session], sessions: [], next: null, total: 0,
    clients: [{ client: 'claude-code', sessions: 1, connected: 1, calls: 3, errors: 0, last_seen: now }] } }));
  await page.route('**/api/agents/w1/calls**', route => route.fulfill({ json: { session, calls: [] } }));
  await page.route('**/api/tasks/**', route => {
    const request = route.request();
    const url = new URL(request.url());
    asked.push({ method: request.method(), path: url.pathname, body: request.postData() ? JSON.parse(request.postData()!) : null });
    if (url.pathname.endsWith('/stop')) return route.fulfill({ json: { run: { id: 'run9' } } });
    if (url.pathname.endsWith('/comment')) return route.fulfill({ json: { task: {} } });
    if (url.pathname.endsWith('/board')) return route.fulfill({ json: { workspace: 'default', total: 0, autonomy: 'ready',
      columns: { backlog: [], ready: [], in_progress: [], review: [], done: [], cancelled: [] },
      options: { projects: [], tags: [], assignees: [], holders: [], features: [] } } });
    if (url.pathname.endsWith('/inbox')) return route.fulfill({ json: { comments: [], review: [] } });
    return route.fulfill({ status: 404, json: { error: `No task ${url.pathname.split('/').pop()}` } });
  });
  return asked;
}

test('Live groups agents by project, waiting first, with what each is doing', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  await mockLive(page);
  await page.goto('/agents/');  // Live is the page's first tab
  const strip = page.getByRole('group', { name: 'Agents right now' });
  await expect(strip).toContainText('1 waiting for you');
  await expect(strip).toContainText('1 working');
  await expect(strip).toContainText('1 on other devices');
  await expect(strip).toContainText('Claude Code 20.1M');
  const shop = page.getByRole('region', { name: 'shop, 2 agents' });
  await expect(shop.locator('li[data-agent]')).toHaveCount(2);
  await expect(shop.locator('li[data-agent]').first()).toHaveAttribute('data-agent', 'q1');
  const working = page.locator('li[data-agent="w1"]');
  await expect(working).toContainText('Testing');
  await expect(working).toContainText('pytest -q');
  await expect(working).toContainText('Working on');
  await expect(working).toContainText('Next: Run the tests');
  await expect(working).toContainText('Context484k');
  await expect(working.getByLabel('Last actions')).toContainText('Editing cart.py');
  const reviewing = page.locator('li[data-agent="q1"]');
  await expect(reviewing).toContainText('Reviewing');
  await expect(reviewing.getByRole('meter', { name: 'Context in use' })).toBeVisible();
  await expect(reviewing).toContainText('12% of the weekly window used');
  const idle = page.locator('li[data-agent="i1"]');
  await expect(idle).toHaveCount(0);  // folded away: idle for over 30 minutes
  await page.getByRole('region', { name: 'site, 1 agent' }).getByRole('button', { name: '1 idle for over 30 minutes' }).click();
  await expect(idle).toHaveClass(/opacity-60/);
  await expect(idle).toContainText('only its Imprint calls show');
  await expect(page.getByRole('region', { name: 'On other devices' })).toContainText('Upload retries');
});

test('Live opens a Task, a History entry, messages an agent and stops only a Dispatched one', async ({ page }) => {
  await base(page, { mode: 'advanced', tutorials: false });
  const asked = await mockLive(page);
  await page.goto('/agents/#live');
  const working = page.locator('li[data-agent="w1"]');
  await expect(working.getByRole('button', { name: 'Stop' })).toHaveCount(0);
  await page.getByRole('button', { name: '1 idle for over 30 minutes' }).click();
  await expect(page.locator('li[data-agent="i1"]').getByRole('button', { name: 'Message' })).toBeDisabled();
  await working.getByRole('button', { name: 'Message' }).click();
  await working.getByLabel('Message to grimforge').fill('Use the new tax table');
  await working.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText('Sent to grimforge on T-aaaa1')).toBeVisible();
  expect(asked.find(a => a.path === '/api/tasks/T-aaaa1/comment')?.body).toEqual({ text: 'Use the new tax table', to: ['grimforge'] });
  const reviewing = page.locator('li[data-agent="q1"]');
  await reviewing.getByRole('button', { name: 'Stop' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Stop' }).click();
  await expect.poll(() => asked.some(a => a.path === '/api/tasks/dispatch/run9/stop' && a.method === 'POST')).toBe(true);
  await working.getByRole('button', { name: 'Open Task' }).click();
  await expect(page).toHaveURL(/\?task=T-aaaa1#board$/);
  // The Board's drawer is modal: the tabs behind it are hidden from the accessibility tree.
  await expect(page.getByRole('tab', { name: 'Board', includeHidden: true })).toHaveAttribute('aria-selected', 'true');
  await page.goto('/agents/#live');
  await page.locator('li[data-agent="w1"]').getByRole('button', { name: 'History' }).click();
  await expect(page.getByRole('tab', { name: 'History', includeHidden: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page).toHaveURL(/\?session=w1#history$/);
});

test('simple mode shows Live with token figures behind Details', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await mockLive(page);
  await page.goto('/agents/#live');
  await expect(page.getByRole('tab', { name: 'History' })).toHaveCount(0);
  const working = page.locator('li[data-agent="w1"]');
  await expect(working).toContainText('Testing');
  await expect(working).not.toContainText('Context');
  await expect(page.getByLabel('Tokens today on this device')).toHaveCount(0);
  await working.getByRole('button', { name: 'Details' }).click();
  await expect(working).toContainText('Context');
});

test('Live says when nothing runs and fits a phone in the dark', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await base(page, { mode: 'advanced', tutorials: false });
  await mockLive(page);
  await page.goto('/agents/#live');
  await expect(page.locator('li[data-agent="w1"]')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.unroute('**/api/agents/live');
  await page.route('**/api/agents/live', route => route.fulfill({ json: report([]) }));
  await page.reload();
  await expect(page.getByText('No agents are running on this device.')).toBeVisible();
});
