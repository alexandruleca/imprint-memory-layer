import { test, expect, type Page } from '@playwright/test';

// The running job's progress record is shown above every page, and by the
// desktop app while the dashboard's server is down (ADR 0089).

const now = Date.now() / 1000;
const learn = {
  id: 'cli-4242', command: 'learn', status: 'running', body: {}, pid: 4242, pgid: null, exit_code: null, error: null,
  created_at: now - 60, started_at: now - 60, ended_at: null,
  phase: 'embedding 340/1200 chunks', processed: 3, total: 112, unit: 'conversations', percent: 2.7,
};

async function mockApi(page: Page, queue: Record<string, unknown>) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [], ...queue } }));
}

test('the running job and how far it is show above every page and open the queue', async ({ page }) => {
  await mockApi(page, { active: learn });
  await page.goto('/search/');
  const indicator = page.getByRole('link', { name: /^learn · embedding 340\/1200 chunks · 3\/112 conversations/ });
  await expect(indicator).toBeVisible();
  await indicator.click();
  await expect(page).toHaveURL(/\/queue\/$/);
});

test('background tagging shows while it runs, and nothing shows when the queue is idle', async ({ page }) => {
  await mockApi(page, { background: { enabled: true, pending: 9, state: 'running', resume_at: null,
    job: { ...learn, id: 'bg1', command: 'tag-backlog', phase: 'llm_tagging', processed: 4, total: 13, unit: 'memories' } } });
  await page.goto('/search/');
  await expect(page.getByRole('link', { name: /^tag-backlog · llm tagging · 4\/13 memories/ })).toBeVisible();

  await page.unroute('**/api/queue?**');
  await page.route('**/api/queue?**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await expect(page.getByRole('link', { name: /Open the queue/ })).toHaveCount(0);
});

test('while the service is down the desktop app says which job holds it', async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { electronAPI: object }).electronAPI = {
      isElectron: true, platform: 'win32',
      activity: async () => ({ command: 'setup', phase: 'installing llama.cpp', processed: 1, total: 3, unit: 'steps' }),
    };
  });
  await page.route('**/api/**', route => route.fulfill({ status: 503, json: {} }));
  await page.goto('/search/');
  await expect(page.getByRole('status').filter({ hasText: 'Imprint is busy: setup · installing llama.cpp · 1/3 steps.' })).toBeVisible();
  await expect(page.getByText('Local service is unavailable.')).toHaveCount(0);
});
