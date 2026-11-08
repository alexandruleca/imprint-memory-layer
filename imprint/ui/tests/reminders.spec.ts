import { test, expect, type Page } from '@playwright/test';

// Reminders, timers and scheduled jobs (ADR 0158). Mirrors imprint/schedule_api.py:
// /api/schedule lists upcoming and past entries a page at a time, /alerts
// holds what rang, and done, snooze and cancel act on an entry.

const now = Date.now() / 1000;
const ID = (c: string) => c.repeat(32);
const entry = (over: Record<string, unknown>) => ({
  id: ID('a'), kind: 'reminder', title: 'call Dan', message: 'call Dan', due_at: now + 3600, timezone: 'Europe/Bucharest',
  recurrence: null, repeat: '', when: 'today 15:00', workspace: 'default', payload: {}, status: 'scheduled', created_by: 'chat',
  created_at: now - 60, updated_at: now - 60, last_fired_at: null, snoozed_until: null, ended_at: null, alert: null, ...over,
});
const alert = (over: Record<string, unknown>) => ({
  id: ID('f'), entry_id: ID('b'), kind: 'reminder', title: 'stand up', message: 'stand up', occurrence_at: now - 5, fired_at: now - 5,
  alerted_at: now - 5, late: false, missed: false, ring: 1, repeats: false, when: 'today 10:00', outcome: {}, ...over,
});

async function mockSchedule(page: Page, { upcoming = [] as object[], past = [] as object[], alerts = [] as object[] } = {}) {
  const state = { created: [] as Record<string, unknown>[], snoozed: [] as unknown[], done: [] as string[], cancelled: [] as string[], upcoming, past, alerts };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/schedule?**', route => {
    const query = new URL(route.request().url()).searchParams;
    const list = query.get('view') === 'past' ? state.past : state.upcoming;
    if (query.get('before') === 'more') return route.fulfill({ json: { entries: list.slice(1), next: null, total: list.length } });
    const paged = query.get('view') === 'upcoming' && list.length > 2;
    return route.fulfill({ json: { entries: paged ? list.slice(0, 1) : list, next: paged ? 'more' : null, total: list.length } });
  });
  await page.route('**/api/schedule/alerts', route => route.fulfill({ json: { alerts: state.alerts, snooze_minutes: [5, 10, 30] } }));
  await page.route('**/api/schedule', async route => {
    const body = route.request().postDataJSON();
    state.created.push(body);
    if (body.kind === 'job' && body.payload?.command === 'wipe') return route.fulfill({ status: 400, json: { error: 'Only these jobs can be scheduled' } });
    const made = entry({ id: ID('c'), kind: body.kind, title: body.title || body.message || 'Timer 10m', message: body.message ?? '', created_by: 'dashboard',
      due_at: now + 600, payload: body.kind === 'timer' ? { seconds: 600 } : {} });
    state.upcoming = [...state.upcoming, made];
    await route.fulfill({ json: { ...made, note: '' } });
  });
  await page.route(/\/api\/schedule\/[a-f0-9]{32}\/(snooze|done|cancel)$/, async route => {
    const [id, action] = route.request().url().split('/').slice(-2);
    if (action === 'snooze') state.snoozed.push({ id, ...route.request().postDataJSON() });
    if (action === 'done') state.done.push(id);
    if (action === 'cancel') { state.cancelled.push(id); state.upcoming = state.upcoming.filter((e) => (e as { id: string }).id !== id); }
    if (action !== 'cancel') state.alerts = state.alerts.filter((a) => (a as { entry_id: string }).entry_id !== id);
    await route.fulfill({ json: entry({ id }) });
  });
  return state;
}

test('upcoming entries are grouped by day, a timer counts down, and more load by cursor', async ({ page }) => {
  await mockSchedule(page, { upcoming: [
    entry({ id: ID('1'), kind: 'timer', title: 'tea', message: '', due_at: now + 125, payload: { seconds: 300 } }),
    entry({ id: ID('2'), title: 'standup', repeat: 'Every weekday at 09:00', due_at: now + 8 * 86400, recurrence: { every: 'weekdays', time: '09:00' } }),
    entry({ id: ID('3'), kind: 'job', title: 'Re-index /code', payload: { command: 'refresh', dir: '/code' }, due_at: now + 9 * 86400 }),
  ] });
  await page.goto('/reminders/');
  await expect(page.getByRole('link', { name: 'Reminders' })).toHaveAttribute('aria-current', 'page');
  const timer = page.getByRole('timer', { name: 'tea remaining' });
  await expect(timer).toHaveText(/^[12]:\d\d$/);
  const first = await timer.textContent();
  await expect(timer).not.toHaveText(first!);
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect(page.getByRole('listitem', { name: 'standup' })).toContainText('Every weekday at 09:00');
  await expect(page.getByRole('heading', { name: 'Later' })).toBeVisible();
});

test('a reminder is created on the page with the viewer’s time zone, and cancelled', async ({ page }) => {
  const state = await mockSchedule(page);
  await page.goto('/reminders/');
  await expect(page.getByText('Nothing scheduled')).toBeVisible();
  await page.getByRole('button', { name: 'New' }).click();
  await page.getByRole('textbox', { name: 'Message' }).fill('water plants');
  await page.getByRole('combobox', { name: 'When' }).selectOption('at');
  await page.getByLabel('Date and time').fill('2030-01-02T10:00');
  await page.getByRole('combobox', { name: 'Repeat' }).selectOption('weekly');
  await page.getByRole('button', { name: 'Mon' }).click();
  await page.getByRole('button', { name: 'Thu' }).click();
  await page.getByRole('button', { name: 'Set', exact: true }).click();
  await expect.poll(() => state.created).toEqual([{
    kind: 'reminder', title: '', message: 'water plants', at: '2030-01-02T10:00', recurrence: 'weekly:mon,thu',
    timezone: await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone),
  }]);
  const row = page.getByRole('listitem', { name: 'water plants' });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Cancel water plants' }).click();
  await expect.poll(() => state.cancelled).toEqual([ID('c')]);
  await expect(row).toHaveCount(0);
});

test('a refused job shows why and keeps the form open', async ({ page }) => {
  await mockSchedule(page);
  await page.route('**/api/schedule', route => route.fulfill({ status: 400, json: { error: '/elsewhere is not an approved folder of workspace \'default\'' } }));
  await page.goto('/reminders/');
  await page.getByRole('button', { name: 'New' }).click();
  await page.getByRole('combobox', { name: 'Kind' }).selectOption('job');
  await page.getByRole('combobox', { name: 'Job' }).selectOption('ingest');
  await page.getByRole('textbox', { name: 'Folder' }).fill('/elsewhere');
  await page.getByRole('button', { name: 'Set', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('not an approved folder');
  await expect(page.getByRole('dialog')).toBeVisible();
});

test('a reminder that rings shows a toast on any page with snooze choices and Done', async ({ page }) => {
  const state = await mockSchedule(page, { alerts: [alert({})] });
  await page.goto('/search/');
  const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Reminder: stand up' });
  await expect(toast).toBeVisible();
  await toast.getByRole('button', { name: 'Snooze 10 minutes' }).click();
  await expect.poll(() => state.snoozed).toEqual([{ id: ID('b'), minutes: 10 }]);
  await expect(toast).toHaveCount(0);
  // Snoozed, it rings again: a new toast, which Done puts away.
  state.alerts = [alert({ ring: 2 })];
  await expect(toast).toBeVisible({ timeout: 10_000 });
  await toast.getByRole('button', { name: 'Done' }).click();
  await expect.poll(() => state.done).toEqual([ID('b')]);
});

test('the Now section offers what each alert can do; a question opens its chat', async ({ page }) => {
  const state = await mockSchedule(page, { alerts: [
    alert({ id: ID('d'), entry_id: ID('e'), kind: 'prompt', title: 'Summarize yesterday', outcome: { chat: 'chat-1' } }),
  ] });
  await page.goto('/reminders/');
  const row = page.getByRole('region', { name: 'Now' }).getByRole('listitem', { name: 'Summarize yesterday' });
  await expect(row).toContainText('The answer is in a new chat.');
  await expect(row.getByRole('button', { name: /Snooze/ })).toHaveCount(0);
  await row.getByRole('button', { name: 'Open chat' }).click();
  await expect(page).toHaveURL(/\/chat\/$/);
  expect(await page.evaluate(() => localStorage.getItem('imprint.chat.last'))).toBe('chat-1');
  await expect.poll(() => state.done).toContain(ID('e'));
});
