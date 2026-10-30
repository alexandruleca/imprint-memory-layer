import { test, expect } from '@playwright/test';

const sse = (events: object[]) => events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
const stream = (events: object[]) => ({ headers: { 'content-type': 'text/event-stream' }, body: sse(events) });
const stats = { memories: { inserted: 3, skipped: 0 }, facts: { inserted: 1, skipped: 0 } };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: [] } }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default', 'team'] } }));
  await page.route('**/api/desktop-learn/history', route => route.fulfill({ json: { seen: {}, count: 0 } }));
});

test('a paired-device list that cannot be read shows why, not an empty list', async ({ page }) => {
  await page.route('**/api/sync/peers', route => route.fulfill({
    status: 500, json: { error: 'Could not read paired devices: sync-pairs.sqlite3 is damaged' },
  }));
  await page.goto('/sync/');
  await expect(page.getByRole('alert').filter({ hasText: 'sync-pairs.sqlite3 is damaged' })).toBeVisible();
  await expect(page.getByText('No paired devices yet')).toHaveCount(0);
});

/** The emphasis on a menu item: its own class, not the focus style every destructive item has. */
const EMPHASIS = /(?:^|\s)bg-destructive\/10(?:\s|$)/;

test('paired-device actions pause and require confirmation to forget both devices', async ({ page }) => {
  let peer = {
    id: 'pair-1', peer: { hostname: 'workstation', fingerprint: 'bbbbbbbb', user: 'bob', os: 'linux' },
    workspace: 'team', remote_workspace: 'team-laptop', relay: 'wss://relay.example.com',
    direction: 'both', enabled: true, state: 'active', created_at: Date.now() / 1000, last_sync: null, last_seen: null, last_error: '',
  };
  const changes: Record<string, unknown>[] = [];
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: [peer] } }));
  await page.route('**/api/sync/peers/pair-1', async route => {
    const body = route.request().postDataJSON();
    changes.push(body);
    peer = { ...peer, ...(body.action === 'forget' ? { state: 'forget_pending', enabled: false } : { enabled: body.enabled }) };
    await route.fulfill({ json: { peers: [peer] } });
  });
  await page.goto('/sync/');
  const actions = page.getByRole('button', { name: 'Actions for workstation' });
  await actions.click();
  await page.getByRole('menuitem', { name: 'Pause automatic sync' }).click();
  await expect(page.getByText('Automatic sync paused on this device')).toBeVisible();
  expect(changes).toEqual([{ action: 'enabled', enabled: false }]);
  await actions.click();
  await page.getByRole('menuitem', { name: 'Resume automatic sync' }).click();
  await expect(page.getByText('Automatic sync enabled')).toBeVisible();
  await actions.click();
  await page.getByRole('menuitem', { name: 'Forget on both devices…' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Forget this device on both sides?' });
  await expect(dialog).toContainText('ALL paired workspaces');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(changes).toHaveLength(2);
  await actions.click();
  await page.getByRole('menuitem', { name: 'Forget on both devices…' }).click();
  await dialog.getByRole('button', { name: 'Forget device', exact: true }).click();
  await expect(page.getByText(/Remote removal pending/)).toBeVisible();
  expect(changes[2]).toEqual({ action: 'forget', confirm: true });
  // A pending forget leaves only removal here.
  await actions.click();
  await expect(page.getByRole('menuitem')).toHaveText(['Remove from this device…']);
});

test('a pairing can be removed from this device alone, from the keyboard, after its own confirmation', async ({ page }) => {
  let peers = [{
    id: 'pair-1', peer: { hostname: 'workstation', fingerprint: 'bbbbbbbb', user: 'bob', os: 'linux' },
    workspace: 'team', remote_workspace: 'team-laptop', relay: 'wss://relay.example.com', direction: 'both',
    enabled: true, state: 'active', created_at: Date.now() / 1000 - 30 * 86400, last_sync: null,
    last_seen: Date.now() / 1000 - 60, last_error: '',
  }];
  const changes: Record<string, unknown>[] = [];
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers } }));
  await page.route('**/api/sync/peers/pair-1', async route => {
    changes.push(route.request().postDataJSON());
    peers = [];
    await route.fulfill({ json: { peers } });
  });
  await page.goto('/sync/');
  const actions = page.getByRole('button', { name: 'Actions for workstation' });
  // Seen a minute ago: offered, not pushed.
  await expect(page.getByText(/was uninstalled or reset/)).toHaveCount(0);
  await actions.focus();
  await page.keyboard.press('Enter');
  const remove = page.getByRole('menuitem', { name: 'Remove from this device…' });
  await expect(remove).toBeVisible();
  await expect(remove).not.toHaveClass(EMPHASIS);
  await expect(page.getByRole('menuitem', { name: 'Forget on both devices…' })).toHaveAttribute('data-variant', 'destructive');
  await expect(remove).toHaveAttribute('data-variant', 'destructive');
  await page.keyboard.press('ArrowUp');  // wraps to the last item
  await expect(remove).toBeFocused();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('alertdialog', { name: 'Remove this pairing from this device only?' });
  await expect(dialog).toContainText('ALL paired workspaces');
  await expect(dialog).toContainText('workstation is not notified');
  await expect(dialog).toContainText('removed there the same way');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(changes).toEqual([]);
  await actions.click();
  await remove.click();
  await dialog.getByRole('button', { name: 'Remove from this device', exact: true }).click();
  await expect(page.getByText('Removed workstation from this device')).toBeVisible();
  await expect(page.getByText('No paired devices yet')).toBeVisible();
  expect(changes).toEqual([{ action: 'remove', confirm: true }]);
});

test('removing here is highlighted for a pending forget and a device not seen for a week', async ({ page }) => {
  const now = Date.now() / 1000;
  const peer = (id: string, hostname: string, extra: object) => ({
    id, peer: { hostname, fingerprint: id.padEnd(8, 'x'), user: 'bob', os: 'linux' },
    workspace: 'team', remote_workspace: 'team', relay: 'wss://relay.example.com', direction: 'both',
    enabled: true, state: 'active', created_at: now - 60 * 86400, last_sync: null, last_seen: now - 120,
    last_error: '', ...extra,
  });
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: [
    peer('pending', 'old-laptop', { state: 'forget_pending', enabled: false }),
    peer('stale', 'wiped-desktop', {
      last_seen: now - 10 * 86400 - 60,
      last_error: "The other device hasn't answered since 2026-10-16 09:00; it may be offline or have removed this pairing",
    }),
    peer('never', 'never-reconnected', { last_seen: null }),
    peer('fresh', 'workstation', {}),
  ] } }));
  await page.goto('/sync/');
  const row = (host: string) => page.getByRole('listitem').filter({ hasText: host });
  const removeIn = async (host: string) => {
    // A closed menu stays mounted while it animates out; open the next one after it is gone.
    await expect(page.getByRole('menu')).toHaveCount(0);
    await page.getByRole('button', { name: `Actions for ${host}` }).click();
    return page.getByRole('menu').getByRole('menuitem', { name: 'Remove from this device…' });
  };
  const hint = (host: string) => `If ${host} was uninstalled or reset, choose Remove from this device in its actions menu.`;

  await expect(row('old-laptop')).toContainText('Removal pending');
  await expect(row('old-laptop')).toContainText(hint('old-laptop'));
  await expect(await removeIn('old-laptop')).toHaveClass(EMPHASIS);
  await page.keyboard.press('Escape');

  await expect(row('wiped-desktop')).toContainText('Not seen for 10 days');
  await expect(row('wiped-desktop')).toContainText(hint('wiped-desktop'));
  await expect(row('wiped-desktop')).toContainText("Last connection: The other device hasn't answered since 2026-10-16 09:00");
  await expect(await removeIn('wiped-desktop')).toHaveClass(EMPHASIS);
  await expect(page.getByRole('menuitem', { name: 'Forget on both devices…' })).toBeVisible();
  await page.keyboard.press('Escape');

  // Never answered since pairing 60 days ago: counted from the pairing.
  await expect(row('never-reconnected')).toContainText('Not seen for 60 days');
  await expect(await removeIn('never-reconnected')).toHaveClass(EMPHASIS);
  await page.keyboard.press('Escape');

  await expect(row('workstation')).not.toContainText('Not seen');
  await expect(row('workstation')).not.toContainText('uninstalled or reset');
  await expect(await removeIn('workstation')).not.toHaveClass(EMPHASIS);
});

test('automatic sync is an explicit checkbox on both serve and receive', async ({ page }) => {
  let requested: Record<string, unknown> = {};
  await page.route('**/api/sync/serve', async route => {
    requested = route.request().postDataJSON();
    await route.fulfill(stream([{ type: 'paired', hostname: 'workstation' }, { type: 'done' }]));
  });
  await page.goto('/sync/#live');
  const checkbox = page.getByRole('checkbox', { name: /Enable automatic sync with this device/ });
  await expect(checkbox).not.toBeChecked();
  await checkbox.check();
  await page.getByRole('button', { name: 'Start Serving' }).click();
  expect(requested.remember).toBe(true);
  await expect(page.getByText(/Device paired for automatic sync/)).toBeVisible();
  await page.getByRole('tab', { name: /Receive/ }).click();
  await expect(page.getByRole('checkbox', { name: /Enable automatic sync with this device/ })).not.toBeChecked();
});

test('serve a chosen workspace send-only, with policy-aware approval and refusals', async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route('**/api/sync/serve', async route => {
    expect(route.request().method()).toBe('POST');
    bodies.push(route.request().postDataJSON());
    await route.fulfill(stream(bodies.length === 1 ? [
      { type: 'room', room_id: 'a1b2c3d4', pin: 'Ab3xY9Kq', code: 'a1b2c3d4-Ab3xY9Kq', target: 'wss://sync.example.com/a1b2c3d4-Ab3xY9Kq', session_id: 's1', workspace: 'team', pull: true, push: false },
      { type: 'status', status: 'waiting' },
      { type: 'approval_required', hostname: 'colleague', os: 'linux', fingerprint: 'cafebabe', user: 'bob' },
    ] : [
      { type: 'room', room_id: 'b2c3d4e5', pin: 'Zz9yX8wV', code: 'b2c3d4e5-Zz9yX8wV', target: 'b2c3d4e5-Zz9yX8wV', session_id: 's2', workspace: 'team', pull: true, push: false },
      { type: 'auto_accepted', hostname: 'colleague', os: 'linux', fingerprint: 'cafebabe', user: 'bob' },
      { type: 'status', status: 'send_complete' },
      { type: 'refused', request: 'push', message: 'provider is serving send-only: pushes are refused' },
      { type: 'done' },
    ]));
  });
  await page.route('**/api/sync/cancel', route => route.fulfill({ json: { ok: true } }));
  await page.goto('/sync/#live');
  const panel = page.getByRole('tabpanel', { name: /^(Serve|Receive)$/ });
  const served = panel.getByRole('combobox', { name: 'Workspace to serve' });
  await expect(served).toContainText('default (active)');
  await served.click();
  await page.getByRole('option', { name: 'team' }).click();
  await expect(served).toContainText('team');
  await panel.getByRole('radio', { name: /^Send only/ }).check();
  await panel.getByLabel(/^Relay/).fill('sync.example.com');
  await panel.getByRole('button', { name: 'Start Serving' }).click();
  await expect(panel.getByText('Send only — peers can pull, pushes are refused')).toBeVisible();
  await expect(panel.getByText('Anything it pushes is refused.')).toContainText('read workspace “team”');
  expect(bodies[0]).toEqual({ workspace: 'team', direction: 'send', relay: 'sync.example.com' });

  // A second session: the peer is trusted and an old client pushes anyway.
  await page.reload();
  await served.click();
  await page.getByRole('option', { name: 'team' }).click();
  await panel.getByRole('radio', { name: /^Send only/ }).check();
  await panel.getByRole('button', { name: 'Start Serving' }).click();
  await expect(panel.getByText('Sync Complete')).toBeVisible();
  await expect(panel.getByRole('status')).toContainText('Sent this workspace to the other device.');
  await expect(panel.getByRole('status')).toContainText('Refused the other device\'s push: this session is send-only');
  await expect(panel.getByText(/^Sent:/)).toHaveCount(0);
});

test('the share code is one string, with the relay address on a custom relay', async ({ page }) => {
  await page.route('**/api/sync/serve', route => route.fulfill(stream([
    { type: 'room', room_id: 'a1b2c3d4', pin: 'Ab3xY9Kq', code: 'a1b2c3d4-Ab3xY9Kq', target: 'wss://sync.example.com/a1b2c3d4-Ab3xY9Kq', session_id: 's9', workspace: 'default', pull: true, push: true },
    { type: 'status', status: 'waiting' },
  ])));
  await page.route('**/api/sync/cancel', route => route.fulfill({ json: { ok: true } }));
  await page.goto('/sync/#live');
  const panel = page.getByRole('tabpanel', { name: /^(Serve|Receive)$/ });
  await panel.getByLabel(/^Relay/).fill('sync.example.com');
  await panel.getByRole('button', { name: 'Start Serving' }).click();
  await expect(panel.getByText('wss://sync.example.com/a1b2c3d4-Ab3xY9Kq', { exact: true })).toBeVisible();
  await expect(panel.getByText('imprint sync wss://sync.example.com/a1b2c3d4-Ab3xY9Kq')).toBeVisible();
  await expect(panel.getByText('PIN', { exact: true })).toHaveCount(0);
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
  await page.goto('/sync/#live');
  await page.getByRole('tab', { name: /Receive/ }).click();
  const panel = page.getByRole('tabpanel', { name: /^(Serve|Receive)$/ });
  await expect(panel.getByRole('radio', { name: /^Receive only/ })).toBeChecked();
  await panel.getByLabel('Code', { exact: true }).fill('a1b2c3d4-Ab3xY9Kq');
  const workspace = panel.getByLabel('Workspace on this device');
  await expect(workspace).toHaveValue('default');
  await workspace.fill('Bad_Name');
  await expect(panel.getByText(/Use lowercase letters/)).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Connect' })).toBeDisabled();
  await workspace.fill('from-alice');
  await expect(panel.getByText(/New workspace — created when/)).toBeVisible();

  // The direction group is a radio group: arrow keys move the choice.
  await panel.getByRole('radio', { name: /^Two-way/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(panel.getByRole('radio', { name: /^Send only/ })).toBeChecked();
  await expect(panel.getByText('Send only needs an existing workspace to send from.')).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(panel.getByRole('radio', { name: /^Receive only/ })).toBeChecked();

  await panel.getByRole('button', { name: 'Connect' }).click();
  await expect(panel.getByText('Sync Complete')).toBeVisible();
  await expect(panel.getByRole('status')).toContainText('Plan: pull only — push skipped (this machine is receive-only)');
  await expect(panel.getByRole('status')).toContainText('Created workspace “from-alice”.');
  await expect(panel.getByText(/^Pulled:/)).toBeVisible();
  expect(body).toEqual({ code: 'a1b2c3d4-Ab3xY9Kq', workspace: 'from-alice', direction: 'receive' });
});

test('a two-way join names the receiving device and asks before sending', async ({ page }) => {
  let decision: Record<string, unknown> = {};
  await page.route('**/api/sync/receive', route => route.fulfill(stream([
    { type: 'session', session_id: 's4', workspace: 'default' },
    { type: 'status', status: 'handshake_ok' },
    { type: 'peer_connected', hostname: 'sam-workstation', user: 'sam', os: 'linux', fingerprint: '5e19b07c' },
    { type: 'confirm_push', workspace: 'default', hostname: 'sam-workstation', user: 'sam', os: 'linux', fingerprint: '5e19b07c' },
  ])));
  await page.route('**/api/sync/approve', async route => {
    decision = route.request().postDataJSON();
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto('/sync/#live');
  await page.getByRole('tab', { name: /Receive/ }).click();
  const panel = page.getByRole('tabpanel', { name: /^(Serve|Receive)$/ });
  await panel.getByRole('radio', { name: /^Two-way/ }).check();
  await panel.getByLabel('Code', { exact: true }).fill('a1b2c3d4-Ab3xY9Kq');
  await panel.getByRole('button', { name: 'Connect' }).click();
  await expect(panel.getByText('Send this workspace?')).toBeVisible();
  await expect(panel.getByText('sam-workstation', { exact: true })).toBeVisible();
  await expect(panel.getByText('fingerprint: 5e19b07c')).toBeVisible();
  await panel.getByRole('button', { name: 'Send', exact: true }).click();
  await expect.poll(() => decision).toEqual({ session_id: 's4', decision: 'accept' });
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
  await page.getByRole('button', { name: 'Copy / move memories…' }).click();
  await expect(page.getByRole('radio', { name: /^Copy/ })).toBeChecked();
  await page.getByRole('button', { name: 'shared' }).click();
  await page.getByRole('combobox', { name: 'To', exact: true }).click();
  await page.getByRole('option', { name: 'team' }).click();
  await page.getByRole('button', { name: 'Copy', exact: true }).click();
  await expect(page.getByText('source unchanged')).toBeVisible();
  expect(body).toMatchObject({ from: 'default', to: 'team', project: 'shared', copy: true });
});

test('the page opens on paired devices; each section has a tab the address keeps', async ({ page }) => {
  await page.goto('/sync/');
  await expect(page.getByRole('tab', { name: 'Paired devices' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: 'Pair a device' }).click();
  await expect(page).toHaveURL(/#live$/);
  await expect(page.getByRole('button', { name: 'Start Serving' })).toBeVisible();
  await page.getByRole('tab', { name: 'Snapshots' }).click();
  await expect(page.getByRole('heading', { name: 'Snapshots' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Snapshots' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: 'Start Serving' })).toHaveCount(0);
});

test('a running automatic sync shows its stage and counts, then what it moved', async ({ page }) => {
  const now = Date.now() / 1000;
  const base = {
    id: 'pair-1', peer: { hostname: 'workstation', fingerprint: 'bbbbbbbb', user: 'bob', os: 'darwin' },
    workspace: 'default', remote_workspace: 'default', relay: 'wss://sync.example.com', direction: 'both',
    enabled: true, state: 'active', created_at: now - 86400, last_seen: now, last_error: '',
  };
  let peer: Record<string, unknown> = {
    ...base, last_sync: null, last_checked: null, last_result: null,
    activity: { stage: 'receiving', since: now - 75, at: now, dataset: 'memories', done: 1200, total: 50214 },
  };
  await page.route('**/api/sync/peers', route => route.fulfill({ json: { peers: [peer] } }));
  await page.goto('/sync/');
  const progress = page.getByRole('status', { name: 'Sync with workstation' });
  await expect(progress).toContainText('Receiving from workstation');
  await expect(progress).toContainText('memories 1,200 of 50,214');
  await expect(progress.getByRole('progressbar')).toBeVisible();

  peer = { ...base, activity: null, last_sync: now - 180, last_checked: now - 60,
    last_result: { received: { memories: 50214, facts: 12 }, sent: { memories: 4 } } };
  await expect(progress).toHaveCount(0, { timeout: 5000 });  // refreshed every 2 s while syncing
  const row = page.getByRole('listitem').filter({ hasText: 'workstation' });
  await expect(row).toContainText('last sync 3 min ago: received 50,214 memories, 12 facts; sent 4 memories');
  await expect(row).toContainText('up to date, checked 1 min ago');
});
