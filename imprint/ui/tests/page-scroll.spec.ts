import { test, expect, type Page } from '@playwright/test';

// The shell is a fixed frame and <main> is the one page scroller
// (dashboard-shell.tsx). The document itself never scrolls, in either axis,
// and the only other scrollers are deliberate fixed-size panels: a log, a
// capped side list. Each page gets enough data here to overflow.

const now = Date.now() / 1000;
const job = (i: number, status: string) => ({
  id: `job${i}`, command: ['ingest', 'refresh', 'retag'][i % 3], status,
  body: { dir: `/home/user/code/project-${i}/a/long/path/that/keeps/going`, llm: i % 2 === 0 },
  pid: 1000 + i, pgid: 1000 + i, exit_code: status === 'done' ? 0 : status === 'failed' ? 1 : null,
  error: status === 'failed' ? 'tagging failed' : null, created_at: now - 900, started_at: status === 'queued' ? null : now - 600,
  ended_at: ['done', 'failed'].includes(status) ? now - 60 : null,
  ...(status === 'running' ? { percent: 42, processed: 420, total: 1000 } : {}),
});
const memory = (i: number) => ({
  id: `m${i}`, label: `Memory ${i}`, project: 'imprint', type: 'decision', chunk_index: i,
  source: `imprint/src/a/long/directory/module_${i}/file_${i}.py`, content: `Decision ${i}: keep one page scroller.`,
  tags: { lang: 'python', layer: 'api', kind: 'source', domain: [], topics: ['layout'] }, similarity: 0.7, match_type: 'hybrid',
});

async function mockApi(page: Page) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
}

/** Every element that scrolls right now, the document included. */
function scrollers(page: Page) {
  return page.evaluate(() => {
    const found: string[] = [];
    const doc = document.scrollingElement!;
    if (doc.scrollHeight > doc.clientHeight + 1 || doc.scrollWidth > window.innerWidth) found.push('document');
    for (const el of document.body.querySelectorAll('*')) {
      const style = getComputedStyle(el);
      const y = /auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
      const x = /auto|scroll/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1;
      if (y || x) found.push(el.id === 'main-content' ? 'main' : el.getAttribute('data-slot') ?? el.tagName.toLowerCase());
    }
    return found;
  });
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 768, height: 1000 }, { width: 390, height: 844 }]) {
  test.describe(`at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test('the queue scrolls as one page around its log panel', async ({ page }) => {
      await mockApi(page);
      await page.route('**/api/queue**', route => route.fulfill({ json: {
        active: job(0, 'running'),
        queued: Array.from({ length: 10 }, (_, i) => job(i + 1, 'queued')),
        recent: Array.from({ length: 30 }, (_, i) => job(i + 20, ['done', 'failed', 'cancelled'][i % 3])),
      } }));
      const lines = Array.from({ length: 300 }, (_, i) => `[${i}] stored batch ${i} from /home/user/code/project/src/module_${i}/file.py\n`);
      await page.route('**/api/jobs/*/stream', route => route.fulfill({
        contentType: 'text/event-stream', body: lines.map(text => `data: ${JSON.stringify({ type: 'output', text })}\n\n`).join(''),
      }));
      await page.goto('/queue/');
      await page.getByRole('button', { name: 'Show log' }).first().click();
      await expect(page.getByText('[299] stored batch 299')).toBeAttached();
      await expect.poll(() => scrollers(page)).toEqual(['main', 'scroll-area-viewport']);
    });

    test('topics scroll as one page beside a capped topic list', async ({ page }) => {
      await mockApi(page);
      await page.route('**/api/topics', route => route.fulfill({ json: {
        topics: Array.from({ length: 60 }, (_, i) => ({ id: `t${i}`, name: `topic-${i}`, count: 300 - i, topProjects: [], topLangs: [], color: '#000' })),
        total: 60, facets: {},
      } }));
      await page.route('**/api/topic/*', route => route.fulfill({ json: {
        topic: 'topic-1', total: 40, nodes: Array.from({ length: 40 }, (_, i) => memory(i)), projects: [['imprint', 40]],
      } }));
      await page.goto('/topics/');
      await page.getByRole('button', { name: /^topic-1\b/ }).click();
      await expect(page.getByText('topic-1 (40 memories)')).toBeVisible();
      await expect.poll(() => scrollers(page)).toEqual(['main', 'scroll-area-viewport']);
    });

    test('search results scroll with the page', async ({ page }) => {
      await mockApi(page);
      await page.route('**/api/search?**', route => route.fulfill({ json: { nodes: Array.from({ length: 30 }, (_, i) => memory(i)) } }));
      await page.goto('/search/');
      await page.getByLabel('Search query', { exact: true }).fill('layout');
      await page.getByRole('button', { name: 'Search', exact: true }).click();
      await page.getByRole('button', { name: /^1\. Memory 0/ }).click();
      await expect(page.getByRole('button', { name: 'Open full memory' })).toBeVisible();
      await expect.poll(() => scrollers(page)).toEqual(['main']);
    });

    test('settings scroll as one page', async ({ page }) => {
      await mockApi(page);
      await page.route('**/api/config', route => route.fulfill({ json: { settings: Array.from({ length: 30 }, (_, i) => ({
        key: `section${i % 5}.setting_${i}`, value: `value-${i}`, source: 'default', default: `value-${i}`, type: 'str',
        env: `IMPRINT_SETTING_${i}`, desc: `What setting ${i} changes, in a sentence of prose.`,
      })) } }));
      await page.goto('/settings/');
      await expect(page.getByText('section0.setting_0')).toBeVisible();
      await expect.poll(() => scrollers(page)).toEqual(['main']);
    });
  });
}
