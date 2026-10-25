import { test, expect, type Page } from '@playwright/test';

// The Queue page shows background tagging apart from the jobs (ADR 0067).

const now = Date.now() / 1000;
const job = (over: Record<string, unknown>) => ({
  id: 'j1', command: 'refresh', status: 'queued', body: { dir: '/home/user/code/project' },
  pid: null, pgid: null, exit_code: null, error: null, created_at: now - 60, started_at: null, ended_at: null, ...over,
});
const tagging = (over: Record<string, unknown>) => ({
  id: 'bg1', command: 'tag-backlog', status: 'running', body: {}, pid: 4242, pgid: 4242, exit_code: null,
  error: null, created_at: now - 120, started_at: now - 120, ended_at: null, ...over,
});

async function mockApi(page: Page) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/jobs/*/stream', route => route.fulfill({
    headers: { 'content-type': 'text/event-stream' },
    body: `data: ${JSON.stringify({ type: 'done', exit_code: 0, status: 'done' })}\n\n`,
  }));
}

async function mockQueue(page: Page, data: Record<string, unknown>) {
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [], ...data } }));
}

const card = (page: Page) => page.getByRole('region', { name: 'Background tagging' });

test('shows background tagging running apart from the jobs, with its progress and cancel', async ({ page }) => {
  await mockApi(page);
  await mockQueue(page, {
    background: {
      enabled: true, pending: 1234, state: 'running', resume_at: null,
      job: tagging({ phase: 'llm_tagging', processed: 40, total: 1274, percent: 3.1 }),
    },
  });
  await page.goto('/queue/');
  await expect(card(page)).toContainText('Running');
  await expect(card(page)).toContainText('1,234 memories waiting');
  await expect(card(page)).toContainText('steps aside between memories');
  await expect(card(page).getByRole('progressbar', { name: 'Background tagging progress' })).toBeVisible();
  await expect(card(page).getByRole('button', { name: 'Cancel job' })).toBeVisible();
  await expect(page.getByText('Nothing running.')).toBeVisible();
});

test('a yielded run says it resumes when the queue is idle', async ({ page }) => {
  await mockApi(page);
  await mockQueue(page, {
    active: job({ status: 'running', started_at: now - 5 }),
    background: { enabled: true, pending: 9, state: 'yielded', resume_at: null,
      job: tagging({ status: 'yielded', exit_code: 75, ended_at: now - 6 }) },
  });
  await page.goto('/queue/');
  await expect(card(page)).toContainText('Yielded');
  await expect(card(page)).toContainText('resumes when the queue is idle');
  await expect(card(page).getByText('exit 75')).not.toHaveClass(/text-red-600/);
  await expect(card(page).getByRole('button', { name: 'Cancel job' })).toHaveCount(0);
});

test('a paused backlog can be resumed now', async ({ page }) => {
  await mockApi(page);
  let resumed = 0;
  await page.route('**/api/queue/background/resume', route => {
    resumed++;
    return route.fulfill({ json: { enabled: true, pending: 9, state: 'waiting', resume_at: null, job: null } });
  });
  await mockQueue(page, {
    background: { enabled: true, pending: 9, state: 'paused', resume_at: null,
      job: tagging({ status: 'cancelled', exit_code: -15, ended_at: now - 30, error: 'cancelled by user; ' }) },
  });
  await page.goto('/queue/');
  await expect(card(page)).toContainText('Cancelled with 9 memories waiting');
  await card(page).getByRole('button', { name: 'Resume now' }).click();
  await expect.poll(() => resumed).toBe(1);
});

test('tagging off names the setting that runs it', async ({ page }) => {
  await mockApi(page);
  await mockQueue(page, { background: { enabled: false, pending: 3, state: 'off', resume_at: null, job: null } });
  await page.goto('/queue/');
  await expect(card(page)).toContainText('Off');
  await expect(card(page)).toContainText('Turn on LLM tagging (tagger.llm)');
});
