import { test, expect } from '@playwright/test';

const sse = (events: object[]) => events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
const stream = (events: object[]) => ({ headers: { 'content-type': 'text/event-stream' }, body: sse(events) });
const stats = { memories: { inserted: 3, skipped: 0 }, facts: { inserted: 1, skipped: 0 } };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default', 'team'] } }));
  await page.route('**/api/desktop-learn/history', route => route.fulfill({ json: { seen: {}, count: 0 } }));
});

test('serve a chosen workspace send-only, with policy-aware approval and refusals', async ({ page }) => {
  const queries: URLSearchParams[] = [];
  await page.route('**/api/sync/serve?**', async route => {
    queries.push(new URL(route.request().url()).searchParams);
    await route.fulfill(stream(queries.length === 1 ? [
      { type: 'room', room_id: 'a1b2c3d4', target: 'wss://sync.example.com/a1b2c3d4', pin: 'Ab3xY9Kq', session_id: 's1', workspace: 'team', pull: true, push: false },
      { type: 'status', status: 'waiting' },
      { type: 'approval_required', hostname: 'colleague', os: 'linux', fingerprint: 'cafebabe', user: 'bob' },
    ] : [
      { type: 'room', room_id: 'b2c3d4e5', pin: 'Zz9yX8wV', session_id: 's2', workspace: 'team', pull: true, push: false },
      { type: 'auto_accepted', hostname: 'colleague', os: 'linux', fingerprint: 'cafebabe', user: 'bob' },
      { type: 'status', status: 'send_complete' },
      { type: 'refused', request: 'push', message: 'provider is serving send-only: pushes are refused' },
      { type: 'done' },
    ]));
  });
  await page.route('**/api/sync/cancel', route => route.fulfill({ json: { ok: true } }));
  await page.goto('/sync/');
  const panel = page.getByRole('tabpanel');
  await expect(panel.getByLabel('Workspace to serve')).toHaveValue('default');
  await panel.getByLabel('Workspace to serve').selectOption('team');
  await panel.getByRole('radio', { name: /^Send only/ }).check();
  await panel.getByLabel(/^Relay/).fill('sync.example.com');
  await panel.getByRole('button', { name: 'Start Serving' }).click();
  await expect(panel.getByText('Send only — peers can pull, pushes are refused')).toBeVisible();
  await expect(panel.getByText('Anything it pushes is refused.')).toContainText('read workspace “team”');
  expect(queries[0].get('workspace')).toBe('team');
  expect(queries[0].get('direction')).toBe('send');
  expect(queries[0].get('relay')).toBe('sync.example.com');

  // A second session: the peer is trusted and an old client pushes anyway.
  await page.reload();
  await panel.getByLabel('Workspace to serve').selectOption('team');
  await panel.getByRole('radio', { name: /^Send only/ }).check();
  await panel.getByRole('button', { name: 'Start Serving' }).click();
  await expect(panel.getByText('Sync Complete')).toBeVisible();
  await expect(panel.getByRole('status')).toContainText('Sent this workspace to the other device.');
  await expect(panel.getByRole('status')).toContainText('Refused the other device\'s push: this session is send-only');
  await expect(panel.getByText(/^Sent:/)).toHaveCount(0);
});

test('a custom relay is shown as the full address to share', async ({ page }) => {
  await page.route('**/api/sync/serve?**', route => route.fulfill(stream([
    { type: 'room', room_id: 'a1b2c3d4', target: 'wss://sync.example.com/a1b2c3d4', pin: 'Ab3xY9Kq', session_id: 's9', workspace: 'default', pull: true, push: true },
    { type: 'status', status: 'waiting' },
  ])));
  await page.route('**/api/sync/cancel', route => route.fulfill({ json: { ok: true } }));
  await page.goto('/sync/');
  const panel = page.getByRole('tabpanel');
  await panel.getByLabel(/^Relay/).fill('sync.example.com');
  await panel.getByRole('button', { name: 'Start Serving' }).click();
  await expect(panel.getByText('wss://sync.example.com/a1b2c3d4')).toBeVisible();
  await expect(panel.getByText('Ab3xY9Kq')).toBeVisible();
});

test('receive one way into a new workspace and show the negotiated plan', async ({ page }) => {
  let body: Record<string, unknown> = {};
  await page.route('**/api/sync/receive', async route => {
    body = route.request().postDataJSON();
    await route.fulfill(stream([
      { type: 'session', session_id: 's3', workspace: 'from-alice' },
      { type: 'status', status: 'handshake_ok' },
      { type: 'plan', pull: true, push: false, skipped: ['push skipped (this machine is receive-only)'] },
      { type: 'workspace_created', workspace: 'from-alice' },
      { type: 'pull_complete', stats },
      { type: 'done', pull_stats: stats, push_stats: null },
    ]));
  });
  await page.goto('/sync/');
  await page.getByRole('tab', { name: /Receive/ }).click();
  const panel = page.getByRole('tabpanel');
  await panel.getByLabel('Room', { exact: true }).fill('a1b2c3d4');
  const workspace = panel.getByLabel('Workspace on this device');
  await expect(workspace).toHaveValue('default');
  await workspace.fill('Bad_Name');
  await expect(panel.getByText(/Use lowercase letters/)).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Connect' })).toBeDisabled();
  await workspace.fill('from-alice');
  await expect(panel.getByText(/New workspace — created when/)).toBeVisible();

  // The direction group is a native radio group: arrow keys move the choice.
  await panel.getByRole('radio', { name: /^Two-way/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(panel.getByRole('radio', { name: /^Send only/ })).toBeChecked();
  await expect(panel.getByText('Send only needs an existing workspace to send from.')).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(panel.getByRole('radio', { name: /^Receive only/ })).toBeChecked();

  await panel.getByRole('button', { name: 'Connect' }).click();
  await expect(panel.getByText('Receive only', { exact: true })).toBeVisible();
  await panel.getByLabel('PIN', { exact: true }).fill('Ab3xY9Kq');
  await panel.getByRole('button', { name: 'Authenticate' }).click();
  await expect(panel.getByText('Sync Complete')).toBeVisible();
  await expect(panel.getByRole('status')).toContainText('Plan: pull only — push skipped (this machine is receive-only)');
  await expect(panel.getByRole('status')).toContainText('Created workspace “from-alice”.');
  await expect(panel.getByText(/^Pulled:/)).toBeVisible();
  expect(body).toMatchObject({ room_id: 'a1b2c3d4', pin: 'Ab3xY9Kq', workspace: 'from-alice', direction: 'receive' });
});

test('migrate dialog copies by default', async ({ page }) => {
  let body: Record<string, unknown> = {};
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [{ name: 'shared' }], total_memories: 0, total_facts: 0 } }));
  await page.route('**/api/commands/migrate', async route => {
    body = route.request().postDataJSON();
    await route.fulfill({ json: { job_id: 'j1', position: 0 } });
  });
  await page.route('**/api/jobs/j1/stream', route => route.fulfill(stream([
    { type: 'output', text: 'Done: 2 copied of 2 scanned (source unchanged)\n' }, { type: 'done' },
  ])));
  await page.goto('/settings/');
  await page.getByRole('button', { name: 'Copy / move…' }).click();
  await expect(page.getByRole('radio', { name: /^Copy/ })).toBeChecked();
  await page.getByRole('button', { name: 'shared' }).click();
  await page.getByRole('combobox', { name: /^To/ }).selectOption('team');
  await page.getByRole('button', { name: 'Copy', exact: true }).click();
  await expect(page.getByText('source unchanged')).toBeVisible();
  expect(body).toMatchObject({ from: 'default', to: 'team', project: 'shared', copy: true });
});
