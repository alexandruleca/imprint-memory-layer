import { test, expect, type Page } from '@playwright/test';
import { mockCatalog } from './command-catalog';

// A submission identical to a waiting job joins it (ADR 0068): the Queue page
// shows how many requests the job answers, and the pages that queue a job say
// when it joined one.

const now = Date.now() / 1000;
const job = (over: Record<string, unknown>) => ({
  id: 'j1', command: 'refresh', status: 'queued', body: { dir: '/home/user/code/project' },
  pid: null, pgid: null, exit_code: null, error: null, created_at: now - 60, started_at: null, ended_at: null, ...over,
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

test('a waiting job shows how many identical requests it answers', async ({ page }) => {
  await mockApi(page);
  await mockQueue(page, { queued: [job({ body: { dir: '/home/user/code/project', _merged: 2 } })] });
  await page.goto('/queue/');
  await expect(page.getByText('3 requests')).toBeVisible();
  await expect(page.getByText('_merged')).toHaveCount(0);
});

test('running a command that joins an identical waiting job says so', async ({ page }) => {
  await mockApi(page);
  await mockCatalog(page);
  await page.route('**/api/commands/learn', route => route.fulfill({ json: { job_id: 'j1', position: 1, merged: true } }));
  await page.goto('/commands/');
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByText('An identical job was already waiting; following it instead of queueing another.')).toBeVisible();
});

test('quick actions say when a job joined an identical waiting one', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 1, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total_memories: 1, total_facts: 0 } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [] } }));
  await mockQueue(page, {});
  await page.route('**/api/commands/learn', route => route.fulfill({ json: { job_id: 'j1', position: 2, merged: true } }));
  await page.goto('/');
  await page.getByRole('button', { name: /^Conversations/ }).click();
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Already queued at position 2');
  await expect(page.getByRole('status')).toContainText('runs once');
});
