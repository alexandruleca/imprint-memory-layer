import { test, expect, type Locator, type Page } from '@playwright/test';
import { mockCatalog } from './command-catalog';

// Log panels follow their newest output by scrolling only themselves
// (useFollowOutput); the page around them stays where the reader left it. The
// job stream here is a ReadableStream the test feeds, so output arrives in
// steps the way a running job's does.

const now = Date.now() / 1000;
const job = (i: number, status: string) => ({
  id: `job${i}`, command: 'ingest', status, body: { dir: `/home/user/code/project-${i}` },
  pid: 1000 + i, pgid: 1000 + i, exit_code: status === 'done' ? 0 : null, error: null,
  created_at: now - 900, started_at: status === 'queued' ? null : now - 600, ended_at: status === 'done' ? now - 60 : null,
});

async function mockApi(page: Page) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.addInitScript(() => {
    let push: (chunk: string) => void = () => {};
    const body = new ReadableStream<Uint8Array>({ start(controller) { push = chunk => controller.enqueue(new TextEncoder().encode(chunk)); } });
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => /\/api\/jobs\/[^/]+\/stream$/.test(String(input))
      ? Promise.resolve(new Response(body, { headers: { 'content-type': 'text/event-stream' } }))
      : fetch(input, init);
    Object.assign(window, { emitOutput: (from: number, count: number) => {
      for (let i = from; i < from + count; i++) push(`data: ${JSON.stringify({ type: 'output', text: `line ${i} of the job\n` })}\n\n`);
    } });
  });
}

const emit = (page: Page, from: number, count: number) =>
  page.evaluate(([f, c]) => (Reflect.get(window, 'emitOutput') as (f: number, c: number) => void)(f, c), [from, count]);
const mainTop = (page: Page) => page.evaluate(() => document.getElementById('main-content')!.scrollTop);
const atBottom = (log: Locator) => log.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 2);
// Scroll events arrive with the next frame; the panel learns the reader's position then.
const nextFrame = (page: Page) => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

test('a job log follows its output without moving the page and pauses while scrolled up', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/queue**', route => route.fulfill({ json: {
    active: null, queued: [], recent: Array.from({ length: 30 }, (_, i) => job(i, 'done')),
  } }));
  await page.goto('/queue/');
  // The reader has scrolled down the page to a job and opens its output.
  const show = page.getByRole('button', { name: /Show details$/ }).nth(15);
  await show.evaluate(el => el.scrollIntoView({ block: 'start' }));
  await show.click();
  const log = page.getByRole('dialog').locator('[data-slot="scroll-area-viewport"]');
  await expect(log).toContainText('No output.');
  const top = await mainTop(page);
  expect(top).toBeGreaterThan(0);

  await emit(page, 0, 100);
  await expect(log).toContainText('line 99 of the job');
  await expect.poll(() => atBottom(log)).toBe(true);
  expect(await mainTop(page)).toBe(top);

  // Scrolled up, the log keeps the reader's place while output continues.
  await log.evaluate(el => { el.scrollTop = 0; });
  await nextFrame(page);
  await emit(page, 100, 50);
  await expect(log).toContainText('line 149 of the job');
  expect(await log.evaluate(el => el.scrollTop)).toBe(0);

  // Back at the bottom, it follows again.
  await log.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await nextFrame(page);
  await emit(page, 150, 50);
  await expect(log).toContainText('line 199 of the job');
  await expect.poll(() => atBottom(log)).toBe(true);
  expect(await mainTop(page)).toBe(top);
});

test('command output follows without moving the page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await mockCatalog(page);
  await page.route('**/api/commands/learn', route => route.fulfill({ json: { job_id: 'j1', position: 0 } }));
  await page.goto('/commands/');
  const run = page.getByRole('button', { name: 'Run', exact: true });
  await expect(run).toBeVisible();
  // Opening the page does not jump to the output panel further down.
  expect(await mainTop(page)).toBe(0);
  await run.click();
  const log = page.locator('[data-slot="scroll-area-viewport"]', { has: page.locator('pre[aria-live]') });
  await expect(log).toContainText('Waiting for output…');
  const top = await mainTop(page);
  expect(await page.evaluate(() => { const main = document.getElementById('main-content')!; return main.scrollHeight - main.clientHeight; }))
    .toBeGreaterThan(top);

  await emit(page, 0, 100);
  await expect(log).toContainText('line 99 of the job');
  await expect.poll(() => atBottom(log)).toBe(true);
  expect(await mainTop(page)).toBe(top);
});
