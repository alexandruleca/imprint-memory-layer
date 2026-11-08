import { test, expect, type Page } from '@playwright/test';

// The Queue page's history comes a page at a time (GET /api/queue/history),
// grouped by when each job ended with each group's count; a failed job offers
// Retry, which queues it again (POST /api/jobs/{id}/retry, ADR 0068), and a
// running job is cancelled only once confirmed.

const now = Date.now() / 1000;
const job = (over: Record<string, unknown>) => ({
  id: 'j1', command: 'refresh', label: 'Refresh files', params: [{ label: 'Directory', value: '/home/user/code/project' }],
  workspace: 'default', status: 'done', body: { dir: '/home/user/code/project' }, pid: 7, pgid: 7, exit_code: 0, error: null,
  created_at: now - 120, started_at: now - 100, ended_at: now - 60, twin: null, ...over,
});

async function mockApi(page: Page, queue: Record<string, unknown> = {}) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/jobs/*/stream', route => route.fulfill({
    headers: { 'content-type': 'text/event-stream' },
    body: `data: ${JSON.stringify({ type: 'done', exit_code: 0, status: 'done' })}\n\n`,
  }));
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [], ...queue } }));
}

const history = (page: Page) => page.getByRole('region', { name: 'History' });

test('history is grouped by when jobs ended, counted, and paged by cursor', async ({ page }) => {
  await mockApi(page);
  const requests: URLSearchParams[] = [];
  await page.route('**/api/queue/history?**', route => {
    const query = new URL(route.request().url()).searchParams;
    requests.push(query);
    const counts = [12, 0, 0, 0, 0, 0, 30];
    if (query.get('before') === 'cursor-2') {
      return route.fulfill({ json: { jobs: [job({ id: 'old', label: 'Retag memories', ended_at: now - 400 * 86400 })], next: null, total: 42, counts } });
    }
    return route.fulfill({ json: { jobs: [job({ id: 'a' }), job({ id: 'b', ended_at: now - 120 })], next: 'cursor-2', total: 42, counts } });
  });
  await page.goto('/queue/');
  await expect(history(page).getByRole('group', { name: 'Today' })).toContainText('12');
  await expect(history(page)).toContainText('1–2 of 42');
  // The period starts go with the request, newest first, in local time.
  const bounds = requests[0].get('bounds')!.split(',').map(Number);
  expect(bounds).toHaveLength(6);
  expect(bounds).toEqual([...bounds].sort((x, y) => y - x));
  expect(bounds[0]).toBe(new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000);

  await history(page).getByRole('button', { name: 'Older' }).click();
  await expect(history(page).getByRole('group', { name: 'Older' })).toContainText('Retag memories');
  await expect(history(page).getByRole('group', { name: 'Older' })).toContainText('30');
  await expect(history(page).getByRole('button', { name: 'Older' })).toBeDisabled();
  await history(page).getByRole('button', { name: 'Newer' }).click();
  await expect(history(page).getByRole('group', { name: 'Today' })).toBeVisible();
  expect(requests.some(q => q.get('before') === 'cursor-2')).toBe(true);
});

test.describe('on a fixed clock', () => {
  // Monday 2 November 2026, noon in Bucharest; en-GB weeks start on Monday, so
  // "This week" starts today and holds nothing that is not already Today. The
  // periods are the same as Agents' (lib/time-groups), with one Older group.
  test.use({ timezoneId: 'Europe/Bucharest', locale: 'en-GB' });
  const at = (iso: string) => new Date(iso).getTime() / 1000;

  test('period starts go with the request and label each group', async ({ page }) => {
    await page.clock.setFixedTime(at('2026-11-02T12:00:00+02:00') * 1000);
    await mockApi(page);
    let bounds: number[] = [];
    await page.route('**/api/queue/history?**', route => {
      bounds = new URL(route.request().url()).searchParams.get('bounds')!.split(',').map(Number);
      return route.fulfill({ json: { jobs: [
        job({ id: 'a', ended_at: at('2026-11-02T00:10:00+02:00') }),
        job({ id: 'b', ended_at: at('2026-11-01T22:00:00+02:00') }),
        job({ id: 'c', ended_at: at('2026-10-26T09:00:00+02:00') }),
        job({ id: 'd', ended_at: at('2026-10-05T09:00:00+03:00') }),
        job({ id: 'e', ended_at: at('2026-09-30T09:00:00+03:00') }),
      ], next: null, total: 5, counts: null } });
    });
    await page.goto('/queue/');
    const groups = history(page).locator('[role=group]:has(h3)');
    await expect(groups.first()).toBeVisible();
    expect(await groups.evaluateAll(els => els.map(el => `${el.getAttribute('aria-label')}:${el.querySelectorAll('li').length}`)))
      .toEqual(['Today:1', 'Yesterday:1', 'Last week:1', 'Last month:1', 'Older:1']);
    expect(bounds).toEqual([
      at('2026-11-02T00:00:00+02:00'), at('2026-11-01T00:00:00+02:00'),
      at('2026-11-01T00:00:00+02:00'), // This week (from today) is clamped to Yesterday's start.
      at('2026-10-26T00:00:00+02:00'), at('2026-10-26T00:00:00+02:00'), // This month (from 1 Nov) likewise.
      at('2026-10-01T00:00:00+03:00'),
    ]);
  });
});

test('the Failed filter asks the server for failed jobs only', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/queue/history?**', route => {
    const failed = new URL(route.request().url()).searchParams.get('status') === 'failed';
    return route.fulfill({ json: failed
      ? { jobs: [job({ id: 'f', status: 'failed', exit_code: 1, error: 'Traceback (most recent call last):\nConnectionError: qdrant is down' })], next: null, total: 1, counts: null }
      : { jobs: [job({})], next: null, total: 1, counts: null } });
  });
  await page.goto('/queue/');
  await history(page).getByRole('button', { name: 'Failed', exact: true }).click();
  // The row says why it failed.
  await expect(history(page)).toContainText('ConnectionError: qdrant is down');
  await expect(history(page)).not.toContainText('Traceback');
});

test('a failed job can be retried; one with an identical job waiting says so instead', async ({ page }) => {
  await mockApi(page);
  let retried = 0;
  await page.route('**/api/jobs/f/retry', route => {
    retried++;
    return route.fulfill({ json: { job_id: 'new', merged: false, position: 2, retry_of: 'f' } });
  });
  await page.route('**/api/queue/history?**', route => route.fulfill({ json: {
    jobs: [
      job({ id: 'f', status: 'failed', exit_code: 1, error: 'boom' }),
      job({ id: 'g', label: 'Learn conversations', status: 'failed', exit_code: 1, twin: { id: 'w', status: 'queued' } }),
    ],
    next: null, total: 2, counts: null,
  } }));
  await page.goto('/queue/');
  await history(page).getByRole('button', { name: 'Retry Refresh files' }).click();
  await expect.poll(() => retried).toBe(1);
  await expect(page.getByText('Refresh files queued again')).toBeVisible();
  await expect(page.getByText('It waits at position 2.')).toBeVisible();
  await expect(history(page).getByRole('button', { name: 'Retry Learn conversations' })).toHaveCount(0);
  await expect(history(page)).toContainText('Queued again');

  // The details panel offers it too, and follows the new job.
  await history(page).getByRole('button', { name: /^Refresh files, Failed/ }).click();
  const panel = page.getByRole('dialog');
  await panel.getByRole('button', { name: 'Retry Refresh files' }).click();
  await expect.poll(() => retried).toBe(2);
  await expect(panel.getByRole('button', { name: 'Remove from queue' })).toBeVisible();
});

test('cancelling a running job asks first', async ({ page }) => {
  await mockApi(page, { active: job({ id: 'r', status: 'running', ended_at: null, exit_code: null, pid: 9 }) });
  let cancelled = 0;
  await page.route('**/api/jobs/r/cancel', route => { cancelled++; return route.fulfill({ json: { ok: true } }); });
  await page.goto('/queue/');
  await page.getByRole('region', { name: 'Running: Refresh files' }).getByRole('button', { name: 'Cancel job' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Cancel Refresh files?');
  await dialog.getByRole('button', { name: 'Keep running' }).click();
  expect(cancelled).toBe(0);
  await page.getByRole('region', { name: 'Running: Refresh files' }).getByRole('button', { name: 'Cancel job' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel job' }).click();
  await expect.poll(() => cancelled).toBe(1);
});

test('a job that fails while the tab watches is announced with Retry', async ({ page }) => {
  const running = job({ id: 'r', status: 'running', ended_at: null, exit_code: null });
  let ended = false;
  await mockApi(page);
  await page.unroute('**/api/queue?**');
  await page.route('**/api/queue?**', route => route.fulfill({ json: ended
    ? { active: null, queued: [], recent: [{ ...running, status: 'failed', exit_code: 3, ended_at: now, error: 'Disk full' }] }
    : { active: running, queued: [], recent: [] } }));
  let retried = 0;
  await page.route('**/api/jobs/r/retry', route => { retried++; return route.fulfill({ json: { job_id: 'n', merged: true, position: 1, retry_of: 'r' } }); });
  await page.goto('/search/');
  await expect(page.getByRole('link', { name: /Open the queue/ })).toBeVisible();
  ended = true;
  const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Refresh files failed' });
  await expect(toast).toContainText('Disk full');
  await toast.getByRole('button', { name: 'Retry' }).click();
  await expect.poll(() => retried).toBe(1);
  await expect(page.getByText('Refresh files is already waiting')).toBeVisible();
});
