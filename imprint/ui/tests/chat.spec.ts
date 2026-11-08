import { test, expect, type Page } from '@playwright/test';
import { slack } from './slack';

type Session = { id: string; title: string; created_at: number; updated_at: number; kind?: 'chat' | 'voice' };

const now = Date.now() / 1000;
const LOCAL = { provider: 'local', model_path: '', model_file: 'Qwen3.5-4B-Q4_K_M.gguf', error: null };

const sse = (...events: object[]) => events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('');

test('a research limit finishes with an answer and a status notice instead of a failed reply', async ({ page }) => {
  await mockChat(page, {
    sessions: [{ id: 'chat1', title: 'Project research', created_at: now, updated_at: now }],
    replies: [sse(
      { type: 'tool_call', name: 'search', args: { query: 'project history' } },
      { type: 'tool_result', name: 'search', result: 'Source meeting.txt: the project tracks inventory.' },
      { type: 'notice', title: 'Finishing the answer', code: 'research_complete', text: 'The research token budget is complete. Answering with the information gathered so far.' },
      { type: 'token', text: 'The project tracks inventory, according to meeting.txt.' },
      { type: 'assistant_message', text: 'The project tracks inventory, according to meeting.txt.' },
      { type: 'done' },
    )],
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Project research', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message' }).fill('Tell me about the project');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  await expect(page.getByRole('status').filter({ hasText: 'Finishing the answer' })).toContainText('The research token budget is complete');
  await expect(page.getByText('The project tracks inventory, according to meeting.txt.', { exact: true })).toBeVisible();
  await expect(page.getByText('The reply failed')).toHaveCount(0);
  await expect(page.getByText('Chatting without tools')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Copy reply' })).toBeVisible();
});

test('section analysis shows progress while worker findings stay out of the answer', async ({ page }) => {
  await mockChat(page, {});
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (!String(input).endsWith('/api/chat')) return original(input, init);
      const encoder = new TextEncoder();
      const body = new ReadableStream({ start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"analysis","text":"Reading section 3 · worker 1 of 2"}\n\n'));
        (window as unknown as { finishSectionReply: () => void }).finishSectionReply = () => {
          controller.enqueue(encoder.encode('data: {"type":"token","text":"The combined answer."}\n\ndata: {"type":"done"}\n\n'));
          controller.close();
        };
      } });
      return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    };
  });
  await page.goto('/chat/');
  await page.getByRole('textbox', { name: 'Message' }).fill('Analyze these sections');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Reading section 3 · worker 1 of 2' })).toBeVisible();
  await page.evaluate(() => (window as unknown as { finishSectionReply: () => void }).finishSectionReply());
  await expect(page.getByText('The combined answer.', { exact: true })).toBeVisible();
  await expect(page.getByText('Reading section 3 · worker 1 of 2')).toHaveCount(0);
});

async function mockChat(page: Page, options: {
  sessions?: Session[];
  messages?: Record<string, object[]>;
  summaries?: Record<string, object>;
  status?: object;
  replies?: string[];
}) {
  const state = {
    sessions: [...(options.sessions ?? [])],
    chatBodies: [] as Record<string, string>[],
    renamed: [] as string[],
    deleted: [] as string[],
  };
  const replies = [...(options.replies ?? [])];
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  // Chats are the active workspace's: the list waits until the page knows which one.
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/chat/status', route =>
    route.fulfill({ json: options.status ?? { ...LOCAL, installed: true, model_ready: true } }));
  await page.route('**/api/chat/sessions', async route => {
    if (route.request().method() === 'POST') {
      const session = { id: 'new1', title: 'New chat', created_at: now, updated_at: now };
      state.sessions.unshift(session);
      return route.fulfill({ json: session });
    }
    await route.fulfill({ json: { sessions: state.sessions } });
  });
  await page.route('**/api/chat/sessions/*', async route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!);
    if (route.request().method() === 'DELETE') {
      state.deleted.push(id);
      state.sessions = state.sessions.filter(session => session.id !== id);
      return route.fulfill({ json: { ok: true } });
    }
    const session = state.sessions.find(item => item.id === id);
    await route.fulfill({ json: { ...session, messages: options.messages?.[id] ?? [], summary: options.summaries?.[id] ?? null } });
  });
  await page.route('**/api/chat/sessions/*/rename', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2);
    const { title } = route.request().postDataJSON();
    state.renamed.push(title);
    state.sessions = state.sessions.map(session => (session.id === id ? { ...session, title } : session));
    await route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/chat', async route => {
    state.chatBodies.push(route.request().postDataJSON());
    // The first message of a new chat is its title, as on the server.
    const body = route.request().postDataJSON();
    state.sessions = state.sessions.map(session =>
      session.id === body.session_id && session.title === 'New chat' ? { ...session, title: body.message } : session);
    await route.fulfill({ contentType: 'text/event-stream', body: replies.shift() ?? sse({ type: 'done' }) });
  });
  return state;
}

test('reopens the chat this browser was last in', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('imprint.chat.last', 'older'));
  await mockChat(page, {
    sessions: [
      { id: 'latest', title: 'Latest chat', created_at: now, updated_at: now },
      { id: 'older', title: 'Naming questionnaire', created_at: now - 86_400 * 3, updated_at: now - 86_400 * 3 },
    ],
    messages: {
      older: [
        { role: 'user', content: 'What do you know about the naming questionnaire?' },
        { role: 'tool', content: 'Naming Questionnaire General UPDATE.pdf …', tool_name: 'search', tool_args: { query: 'naming questionnaire' } },
        { role: 'assistant', content: 'It is a **branding** questionnaire.' },
      ],
    },
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Naming questionnaire' })).toBeVisible();
  await expect(page.getByText('It is a branding questionnaire.')).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Chats' }).getByRole('region', { name: 'Previous 7 days' })).toContainText('Naming questionnaire');
  await expect(page.getByRole('button', { name: 'Naming questionnaire', exact: true })).toHaveAttribute('aria-current', 'page');

  const tool = page.getByRole('button', { name: /Searched memory for “naming questionnaire”/ });
  await tool.click();
  await expect(page.getByText('Naming Questionnaire General UPDATE.pdf …')).toBeVisible();
});

test('without chats the page is a new chat whose first message sets up local chat', async ({ page }) => {
  const state = await mockChat(page, {
    status: { ...LOCAL, installed: true, model_ready: false },
    replies: [sse(
      { type: 'download_start', file: 'Qwen3.5-4B-Q4_K_M.gguf', repo: 'unsloth/Qwen3.5-4B-GGUF', path: '/models/q.gguf' },
      { type: 'download_progress', downloaded: 2_740_000_000, total: 2_740_000_000 },
      { type: 'download_complete', path: '/models/q.gguf' },
      { type: 'load_start' },
      { type: 'tool_call', name: 'search', args: { query: 'decisions' } },
      { type: 'tool_result', name: 'search', result: 'ADR 0035' },
      { type: 'token', text: 'We decided to ship ' },
      { type: 'token', text: 'prebuilt wheels.' },
      { type: 'assistant_message', text: 'We decided to ship prebuilt wheels.' },
      { type: 'done' },
    )],
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'What do you want to recall?' })).toBeVisible();
  await expect(page.getByText('Your first message sets up local chat: Imprint downloads the model (a few GB) on this device, once.')).toBeVisible();
  await expect(page.getByText('Local · Qwen3.5-4B-Q4_K_M')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled();

  await page.getByRole('textbox', { name: 'Message' }).fill('What did we decide?');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  await expect(page.getByText('We decided to ship prebuilt wheels.')).toBeVisible();
  await expect(page.getByText('Local chat is set up')).toBeVisible();
  await expect(page.getByRole('button', { name: /Searched memory for “decisions”/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What did we decide?' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'What did we decide?', exact: true })).toHaveAttribute('aria-current', 'page');
  expect(state.chatBodies).toEqual([{ session_id: 'new1', message: 'What did we decide?' }]);
  await expect(page.getByRole('button', { name: 'Copy reply' })).toBeVisible();
});

test('a local model without a tool-call format says the chat runs without tools', async ({ page }) => {
  const notice = "This model's chat template writes tool calls in no format Imprint recognizes, so this chat runs "
    + 'without memory tools. If the model can call tools, choose its format in Settings, Chat, Tool-call format (chat.tool_format).';
  await mockChat(page, {
    sessions: [{ id: 'chat1', title: 'Plain model', created_at: now, updated_at: now }],
    replies: [sse(
      { type: 'load_start' },
      { type: 'notice', text: notice },
      { type: 'token', text: 'Hello.' },
      { type: 'assistant_message', text: 'Hello.' },
      { type: 'done' },
    )],
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Plain model' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message' }).fill('hi');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  const status = page.getByRole('status').filter({ hasText: 'Chatting without tools' });
  await expect(status).toContainText('chat.tool_format');
  await expect(status.getByRole('link', { name: 'Open chat settings' })).toHaveAttribute('href', '/settings/#settings-chat');
  await expect(page.getByText('Hello.')).toBeVisible();
  await expect(page.getByText('Loading the local model…')).toHaveCount(0);
});

test('shows download progress while setup runs', async ({ page }) => {
  await mockChat(page, {
    status: { ...LOCAL, installed: true, model_ready: false },
    replies: [sse(
      { type: 'download_start', file: 'Qwen3.5-4B-Q4_K_M.gguf', repo: 'unsloth/Qwen3.5-4B-GGUF', path: '/models/q.gguf' },
      { type: 'download_progress', downloaded: 1_370_000_000, total: 2_740_000_000 },
    )],
  });
  await page.goto('/chat/');
  await expect(page.getByText('downloads the model (a few GB)')).toBeVisible();
  await page.getByRole('button', { name: 'Which projects are indexed?' }).click();
  const setup = page.getByRole('region', { name: 'Setting up local chat' });
  await expect(setup).toContainText('Download Qwen3.5-4B-Q4_K_M.gguf');
  await expect(setup).toContainText('1.4 GB of 2.7 GB · 50%');
  await expect(setup.getByRole('progressbar', { name: 'Model download' })).toBeVisible();
});

test('a failed reply offers retry, which asks again', async ({ page }) => {
  const state = await mockChat(page, {
    sessions: [{ id: 'chat1', title: 'Setup', created_at: now, updated_at: now }],
    replies: [
      sse({ type: 'error', error: 'download failed: connection reset by peer' }, { type: 'done' }),
      sse({ type: 'token', text: 'Working now.' }, { type: 'assistant_message', text: 'Working now.' }, { type: 'done' }),
    ],
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Setup' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message' }).fill('hello');
  await page.getByRole('button', { name: 'Send message' }).click();
  const failure = page.getByRole('alert').filter({ hasText: 'The reply failed' });
  await expect(failure).toContainText('connection reset by peer');
  await failure.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByText('Working now.')).toBeVisible();
  await expect(page.getByText('The reply failed')).toHaveCount(0);
  expect(state.chatBodies.map(body => body.message)).toEqual(['hello', 'hello']);
});

test('stop ends a reply that is still running, and asks the API to stop it', async ({ page }) => {
  await mockChat(page, { sessions: [{ id: 'chat1', title: 'Slow', created_at: now, updated_at: now }] });
  const stops: string[] = [];
  await page.route('**/api/chat/sessions/*/stop', route => { stops.push(new URL(route.request().url()).pathname); return route.fulfill({ json: { ok: true } }); });
  await page.route('**/api/chat', async route => {
    await new Promise(resolve => setTimeout(resolve, slack(10_000)));  // longer than Stop takes
    await route.fulfill({ contentType: 'text/event-stream', body: sse({ type: 'done' }) }).catch(() => {});
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Slow' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message' }).fill('take your time');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  await expect(page.getByRole('status', { name: 'Thinking' })).toBeVisible();
  await page.getByRole('button', { name: 'Stop reply' }).click();
  await expect(page.getByText('Stopped')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible();
  // Closing the stream alone would only stop watching; the reply is stopped in the API (ADR 0145).
  expect(stops).toEqual(['/api/chat/sessions/chat1/stop']);
});

test('a reply goes on while another chat is open, and its chat shows it whole when opened again', async ({ page }) => {
  const messages: Record<string, object[]> = { other: [{ role: 'user', content: 'Hello' }, { role: 'assistant', content: 'Hi.' }] };
  const state = await mockChat(page, {
    sessions: [
      { id: 'billing', title: 'Billing', created_at: now, updated_at: now },
      { id: 'other', title: 'Other chat', created_at: now - 60, updated_at: now - 60 },
    ],
    messages,
  });
  const stops: string[] = [];
  await page.route('**/api/chat/sessions/*/stop', route => { stops.push(route.request().url()); return route.fulfill({ json: { ok: true } }); });
  await page.route('**/api/chat/sessions/*/reply', route => route.fulfill({ contentType: 'text/event-stream', body: sse(
    { type: 'tool_call', name: 'search', args: { query: 'billing port' } },
    { type: 'tool_result', name: 'search', result: 'billing: 8101' },
    { type: 'token', text: 'Billing is on ' },
    { type: 'token', text: 'port 8101.' },
    { type: 'assistant_message', text: 'Billing is on port 8101.' },
    { type: 'done' },
  ) }));
  // The first reply streams one token, then waits; leaving aborts only the watching.
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (!String(input).endsWith('/api/chat')) return original(input, init);
      const encoder = new TextEncoder();
      const body = new ReadableStream({ start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"token","text":"Billing is on "}\n\n'));
        init?.signal?.addEventListener('abort', () => controller.error(new DOMException('The watching stopped', 'AbortError')));
      } });
      return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    };
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message' }).fill('Which port is billing on?');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  await expect(page.getByText('Billing is on')).toBeVisible();
  // The API now has the question and a reply still being written.
  messages.billing = [{ role: 'user', content: 'Which port is billing on?' },
    { role: 'tool', content: 'billing: 8101', tool_name: 'search', tool_args: { query: 'billing port' } }];
  state.sessions = state.sessions.map(session => session.id === 'billing' ? { ...session, replying: true } : session);
  await page.getByRole('button', { name: 'Other chat', exact: true }).click();
  await expect(page.getByText('Hi.', { exact: true })).toBeVisible();
  await expect(page.getByTitle('Still answering')).toBeVisible();
  await expect(page.getByText('Stopped')).toHaveCount(0);
  await page.getByRole('button', { name: 'Billing', exact: true }).click();
  await expect(page.getByText('Billing is on port 8101.', { exact: true })).toBeVisible();
  // Replayed from its start once: one search row, not the stored one again.
  await expect(page.getByRole('button', { name: /Searched memory for “billing port”/ })).toHaveCount(1);
  await expect(page.getByText('Stopped')).toHaveCount(0);
  expect(stops).toEqual([]);
});

test('chats can be renamed and deleted from their menu', async ({ page }) => {
  const state = await mockChat(page, {
    sessions: [
      { id: 'a', title: 'First', created_at: now, updated_at: now },
      { id: 'b', title: 'Second', created_at: now - 10, updated_at: now - 10 },
    ],
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'First' })).toBeVisible();

  await page.getByRole('button', { name: 'Actions for First' }).click();
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  await page.getByLabel('Chat title').fill('Discarded');
  await page.getByLabel('Chat title').press('Escape');
  await expect(page.getByRole('heading', { name: 'First' })).toBeVisible();
  expect(state.renamed).toEqual([]);

  await page.getByRole('button', { name: 'Actions for First' }).click();
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  await page.getByLabel('Chat title').fill('Renamed');
  await page.getByLabel('Chat title').press('Enter');
  await expect(page.getByRole('heading', { name: 'Renamed' })).toBeVisible();
  expect(state.renamed).toEqual(['Renamed']);

  await page.getByRole('button', { name: 'Actions for Renamed' }).click();
  await page.getByRole('menuitem', { name: 'Delete' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete chat?' });
  await expect(confirm).toContainText('“Renamed” and its messages will be deleted.');
  await confirm.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('heading', { name: 'What do you want to recall?' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Renamed', exact: true })).toHaveCount(0);
  expect(state.deleted).toEqual(['a']);
});

test('a summarized chat marks where its summary ends, and the summary opens to be read', async ({ page }) => {
  const exchange = (n: number) => [
    { seq: n * 2 - 1, role: 'user', content: `Question ${n}` },
    { seq: n * 2, role: 'assistant', content: `Answer ${n}.` },
  ];
  await mockChat(page, {
    sessions: [{ id: 'long', title: 'Long chat', created_at: now, updated_at: now }],
    messages: { long: [1, 2, 3].flatMap(exchange) },
    summaries: { long: { text: '- Asked question 1 (#1–#2).\n- Asked question 2 (#3–#4).', covers: 4, exchanges: 2 } },
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Long chat' })).toBeVisible();
  const toggle = page.getByRole('button', { name: 'Summary of earlier conversation' });
  await expect(toggle).toBeVisible();
  // It sits after the second exchange, before the third.
  const order = await page.evaluate(() => {
    const at = (text: string) => [...document.querySelectorAll('section[aria-label="Chat"] *')]
      .findIndex(element => element.textContent?.trim() === text);
    return [at('Answer 2.'), at('Summary of earlier conversation'), at('Question 3')];
  });
  expect(order[0]).toBeLessThan(order[1]);
  expect(order[1]).toBeLessThan(order[2]);
  const summary = page.getByRole('region', { name: 'Summary of earlier conversation' });
  await expect(summary).toBeHidden();
  await toggle.click();
  await expect(summary).toContainText("Imprint reads this summary in place of the 2 exchanges above, which no longer fit the model's context.");
  await expect(summary.getByText('Asked question 2 (#3–#4).')).toBeVisible();
  await toggle.click();
  await expect(summary).toBeHidden();
});

test('a reply that summarizes shows its progress, then the summary and the lookup it made', async ({ page }) => {
  await mockChat(page, {
    sessions: [{ id: 'chat1', title: 'Compose file', created_at: now, updated_at: now }],
    messages: { chat1: [
      { seq: 1, role: 'user', content: 'Here is our compose file: billing listens on 8101' },
      { seq: 2, role: 'assistant', content: 'Got it.' },
    ] },
  });
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (!String(input).endsWith('/api/chat')) return original(input, init);
      const encoder = new TextEncoder();
      const send = (controller: ReadableStreamDefaultController, event: object) =>
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      const body = new ReadableStream({ start(controller) {
        send(controller, { type: 'analysis', text: 'Summarizing the earlier conversation…' });
        (window as unknown as { finishSummary: () => void }).finishSummary = () => {
          send(controller, { type: 'summary', text: '- Pasted the compose file (#1–#2).', covers: 2, exchanges: 1, retry_after: 0 });
          send(controller, { type: 'tool_call', name: 'conversation_history', args: { query: 'billing port' } });
          send(controller, { type: 'tool_result', name: 'conversation_history', result: '#1 user · 49 characters\nHere is our compose file: billing listens on 8101' });
          send(controller, { type: 'token', text: 'Billing listens on 8101, as your compose file says.' });
          send(controller, { type: 'done' });
          controller.close();
        };
      } });
      return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    };
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Compose file' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Summary of earlier conversation' })).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Message' }).fill('Which port does billing use?');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  await expect(page.getByRole('status').filter({ hasText: 'Summarizing the earlier conversation…' })).toBeVisible();
  await page.evaluate(() => (window as unknown as { finishSummary: () => void }).finishSummary());
  await expect(page.getByText('Billing listens on 8101, as your compose file says.')).toBeVisible();
  await expect(page.getByText('Summarizing the earlier conversation…')).toHaveCount(0);
  const lookup = page.getByRole('button', { name: /Searched earlier in this conversation for “billing port”/ });
  await expect(lookup).toBeVisible();
  await lookup.click();
  await expect(page.getByText('#1 user · 49 characters')).toBeVisible();
  await page.getByRole('button', { name: 'Summary of earlier conversation' }).click();
  await expect(page.getByRole('region', { name: 'Summary of earlier conversation' })).toContainText('Pasted the compose file (#1–#2).');
});

test('the chat list moves into a sheet on narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockChat(page, { sessions: [{ id: 'a', title: 'Phone chat', created_at: now, updated_at: now }] });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Phone chat' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Chats' })).toBeHidden();
  await page.getByRole('button', { name: 'Show chats' }).click();
  await expect(page.getByRole('dialog').getByRole('navigation', { name: 'Chats' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('a conversation with Echo is marked, and the open chat can be carried on with Echo (ADR 0146)', async ({ page }) => {
  await mockChat(page, {
    sessions: [
      { id: 'spoken', title: 'Where is billing?', created_at: now, updated_at: now, kind: 'voice' },
      { id: 'typed', title: 'Typed chat', created_at: now - 60, updated_at: now - 60, kind: 'chat' },
    ],
    messages: {
      spoken: [{ role: 'user', content: 'Where is billing?' }, { role: 'assistant', content: 'Port 8101.' }],
      typed: [{ role: 'user', content: 'Typed chat' }, { role: 'assistant', content: 'A typed answer.' }],
    },
  });
  await page.goto('/chat/');
  const list = page.getByRole('navigation', { name: 'Chats' });
  await expect(list.getByRole('img', { name: 'Echo conversation' })).toHaveCount(1);
  await expect(list.getByRole('button', { name: 'Echo conversation Where is billing?' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('link', { name: 'Continue with Echo' })).toHaveAttribute('href', '/voice/?chat=spoken');
  await list.getByRole('button', { name: 'Typed chat', exact: true }).click();
  await expect(page.getByText('A typed answer.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Continue with Echo' })).toHaveAttribute('href', '/voice/?chat=typed');
});
