import { test, expect, type Page } from '@playwright/test';

// A topic's memories are grouped by the conversation or file they came from,
// and each opens whole in the reader with the topic's passages marked.
// Shapes follow build_topic_detail and build_source_read (imprint/cli_viz.py).

const chunk = (i: number, content: string) => ({ id: `m${i}`, label: content, type: 'finding', chunk_index: i, tags: { topics: ['layout'] }, content });
const detail = {
  topic: 'layout', project: null, total: 3, projects: [['imprint', 2], ['site', 1]],
  sources: [
    { source: 'conversation/a1', project: 'imprint', source_type: 'conversation', title: 'Why do the panes scroll apart?',
      chunks: [chunk(1, 'Two scrollers fight over the page'), chunk(3, 'One frame, two panes')] },
    { source: 'site/layout.md', project: 'site', source_type: 'file', title: '', chunks: [chunk(0, 'Layout notes')] },
  ],
};
const at = (event: number, over: Record<string, unknown>) => ({
  event, start: event * 100, end: event * 100 + 50, chunks: [event], match: false, turn: 1, timestamp: '2026-10-26T15:44:26Z', ...over,
});
const conversation = {
  source: 'conversation/a1', project: 'imprint', kind: 'conversation', available: true, title: 'Why do the panes scroll apart?',
  topic: 'layout', total_chars: 600, chunk_count: 5, updated_at: 1793119423, details: { event_count: 6, omissions: { opaque_reasoning: 2 } },
  blocks: [
    at(0, { role: 'user', kind: 'message', text: 'Why do the panes scroll apart?' }),
    at(1, { role: 'assistant', kind: 'message', text: 'Let me look at the **shell**.' }),
    at(2, { role: 'assistant', kind: 'tool_call', tool: 'Bash', call_id: 'c1', text: 'Bash\n{"command": "grep -n overflow shell.tsx", "description": "Find scrollers"}' }),
    at(3, { role: 'tool', kind: 'tool_output', call_id: 'c1', text: '12: overflow-y-auto', match: true }),
    at(4, { role: 'assistant', kind: 'analysis', text: 'Both panes scroll with the page.' }),
    at(5, { role: 'assistant', kind: 'message', text: 'The panes share one scroller.' }),
    at(6, { role: 'assistant', kind: 'message', text: 'One frame with two panes fixes it.', match: true }),
  ],
};

async function mockApi(page: Page) {
  const reads: URLSearchParams[] = [];
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/topics', route => route.fulfill({ json: {
    topics: [{ id: 't1', name: 'layout', count: 3, topProjects: [], topLangs: [], color: '#000' }], total: 1, facets: {},
  } }));
  await page.route('**/api/topic/*', route => route.fulfill({ json: detail }));
  await page.route('**/api/sources/read?**', route => {
    const params = new URL(route.request().url()).searchParams;
    reads.push(params);
    return params.get('source') === 'conversation/a1'
      ? route.fulfill({ json: conversation })
      : route.fulfill({ status: 404, json: { available: false, error: 'This source has no full snapshot. Learn the conversation again or refresh the file with --force to keep one.' } });
  });
  return reads;
}

test('a topic lists its conversations by their question and reads one whole, stepping through its passages', async ({ page }) => {
  const reads = await mockApi(page);
  await page.goto('/topics/?topic=layout');
  await expect(page.getByText('3 memories from 2 sources')).toBeVisible();
  const sources = page.getByRole('list', { name: 'Sources' });
  await expect(sources.getByText('Why do the panes scroll apart?')).toBeVisible();
  await expect(sources.getByText('site/layout.md')).toBeVisible();

  await page.getByRole('group', { name: 'Show sources from' }).getByRole('button', { name: /site/ }).click();
  await expect(sources.getByText('Why do the panes scroll apart?')).toHaveCount(0);
  await page.getByRole('group', { name: 'Show sources from' }).getByRole('button', { name: 'All projects' }).click();

  await sources.getByRole('button', { name: 'Read conversation' }).click();
  const reader = page.getByRole('dialog', { name: 'Why do the panes scroll apart?' });
  expect(Object.fromEntries(reads[0])).toEqual({ source: 'conversation/a1', project: 'imprint', topic: 'layout' });
  await expect(reader.getByText('Let me look at the')).toBeVisible();
  await expect(reader.getByText('2 encrypted reasoning blocks or attachments were not stored.')).toBeVisible();
  // Tool calls and thinking fold between the messages; the first marked passage opens.
  const status = reader.getByRole('status');
  await expect(status).toContainText('1 of 2 passages');
  await expect(reader.getByRole('button', { name: /1 tool call, 1 thought/ })).toHaveAttribute('aria-expanded', 'true');
  await reader.getByRole('button', { name: /^Bash/ }).click();
  await expect(reader.getByText('12: overflow-y-auto')).toBeVisible();
  await reader.getByRole('button', { name: 'Next passage' }).click();
  await expect(status).toContainText('2 of 2 passages');
  await expect(reader.locator('[data-match]')).toHaveCount(2);
});

test('a file without a snapshot says how to keep one', async ({ page }) => {
  await mockApi(page);
  await page.goto('/topics/?topic=layout');
  await page.getByRole('list', { name: 'Sources' }).getByRole('button', { name: 'Read source' }).click();
  await expect(page.getByRole('dialog')).toContainText('Learn the conversation again or refresh the file with --force');
});

test('audio conversations show timestamped unidentified speech without chat roles', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/sources/read?**', route => route.fulfill({ json: {
    ...conversation, title: 'meeting.m4a', details: { event_count: 2, media: 'audio' },
    blocks: [
      at(0, { role: 'speaker', kind: 'message', text: 'Bună ziua, discutăm despre proiect.', audio_start: 1.2, audio_end: 8.5, language: 'ro' }),
      at(1, { role: 'speaker', kind: 'message', text: 'Let us review the design.', audio_start: 30.2, audio_end: 35.5, language: 'en' }),
    ],
  } }));
  await page.goto('/topics/?topic=layout');
  await page.getByRole('list', { name: 'Sources' }).getByRole('button', { name: 'Read conversation' }).click();
  const reader = page.getByRole('dialog', { name: 'meeting.m4a' });
  await expect(reader.getByText('Machine transcript · speakers unidentified')).toBeVisible();
  await expect(reader.getByText('Bună ziua, discutăm despre proiect.')).toBeVisible();
  await expect(reader.getByText('Let us review the design.')).toBeVisible();
  await expect(reader.getByText('Unidentified speaker · 00:00:01–00:00:08 · ro')).toBeVisible();
  await expect(reader.getByText('Unidentified speaker · 00:00:30–00:00:35 · en')).toBeVisible();
  await expect(reader.getByText(/context note/)).toHaveCount(0);
});

test('the graph opens a topic\'s conversations in the reader', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/graph?**', route => route.fulfill({ json: {
    scope: 'root', center: '',
    nodes: [{ id: 'proj:imprint', kind: 'project', label: 'imprint', count: 2, color: '#4ecdc4' },
            { id: 'topic:layout', kind: 'topic', label: 'layout', count: 3, color: '#a78bfa' }],
    edges: [{ id: 'relates:proj:imprint->topic:layout', source: 'proj:imprint', target: 'topic:layout', kind: 'relates', weight: 2 }],
  } }));
  await page.goto('/graph/');
  await page.getByLabel('Find in graph').fill('layout');
  await page.getByLabel('Find in graph').press('Enter');
  const details = page.getByRole('complementary', { name: 'Topic details' });
  await details.getByRole('button', { name: /Why do the panes scroll apart\?/ }).click();
  await expect(page.getByRole('dialog', { name: 'Why do the panes scroll apart?' }).getByRole('status')).toContainText('1 of 2 passages');
});
