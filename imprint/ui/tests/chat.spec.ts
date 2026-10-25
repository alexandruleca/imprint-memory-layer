import { test, expect, type Page } from '@playwright/test';

type Session = { id: string; title: string; created_at: number; updated_at: number };

const now = Date.now() / 1000;
const LOCAL = { enabled: true, provider: 'local', model_path: '', model_file: 'Qwen3.5-4B-Q4_K_M.gguf', error: null };

const sse = (...events: object[]) => events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('');

async function mockChat(page: Page, options: {
  sessions?: Session[];
  messages?: Record<string, object[]>;
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
    await route.fulfill({ json: { ...session, messages: options.messages?.[id] ?? [] } });
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
    status: { ...LOCAL, installed: false, model_ready: true },
    replies: [sse(
      { type: 'install_start' },
      { type: 'install_progress', line: '[*] Installing llama-cpp-python (local tagger + chat)...' },
      { type: 'install_complete' },
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
  await expect(page.getByText('Your first message sets up local chat: Imprint installs the llama.cpp runtime on this device, once.')).toBeVisible();
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

test('shows install output and download progress while setup runs', async ({ page }) => {
  await mockChat(page, {
    status: { ...LOCAL, installed: false, model_ready: false },
    replies: [sse(
      { type: 'install_start' },
      { type: 'install_complete' },
      { type: 'download_start', file: 'Qwen3.5-4B-Q4_K_M.gguf', repo: 'unsloth/Qwen3.5-4B-GGUF', path: '/models/q.gguf' },
      { type: 'download_progress', downloaded: 1_370_000_000, total: 2_740_000_000 },
    )],
  });
  await page.goto('/chat/');
  await expect(page.getByText('installs the llama.cpp runtime and downloads the model (a few GB)')).toBeVisible();
  await page.getByRole('button', { name: 'Which projects are indexed?' }).click();
  const setup = page.getByRole('region', { name: 'Setting up local chat' });
  await expect(setup).toContainText('Install the llama.cpp runtime');
  await expect(setup).toContainText('Download Qwen3.5-4B-Q4_K_M.gguf');
  await expect(setup).toContainText('1.4 GB of 2.7 GB · 50%');
  await expect(setup.getByRole('progressbar', { name: 'Model download' })).toBeVisible();
});

test('a failed reply offers retry, which asks again', async ({ page }) => {
  const state = await mockChat(page, {
    sessions: [{ id: 'chat1', title: 'Setup', created_at: now, updated_at: now }],
    replies: [
      sse({ type: 'error', error: 'installing llama-cpp-python failed:\nno C compiler' }, { type: 'done' }),
      sse({ type: 'token', text: 'Working now.' }, { type: 'assistant_message', text: 'Working now.' }, { type: 'done' }),
    ],
  });
  await page.goto('/chat/');
  await expect(page.getByRole('heading', { name: 'Setup' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message' }).fill('hello');
  await page.getByRole('button', { name: 'Send message' }).click();
  const failure = page.getByRole('alert').filter({ hasText: 'The reply failed' });
  await expect(failure).toContainText('no C compiler');
  await failure.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByText('Working now.')).toBeVisible();
  await expect(page.getByText('The reply failed')).toHaveCount(0);
  expect(state.chatBodies.map(body => body.message)).toEqual(['hello', 'hello']);
});

test('stop ends a reply that is still running', async ({ page }) => {
  await mockChat(page, { sessions: [{ id: 'chat1', title: 'Slow', created_at: now, updated_at: now }] });
  await page.route('**/api/chat', async route => {
    await new Promise(resolve => setTimeout(resolve, 10_000));
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
