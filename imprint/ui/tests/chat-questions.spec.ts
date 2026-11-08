import { test, expect, type Page } from '@playwright/test';

// ADR 0159: a chat reply asks a clarifying question and waits; any window can
// answer it through the API, and the stored exchange shows the answer.

const now = Date.now() / 1000;
const sse = (...events: object[]) => events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('');
const QUESTION = { type: 'question', id: 'q1', question: 'Which project?', options: ['Imprint', 'Billing'], allow_other: true };

async function mockChat(page: Page, options: { messages?: object[]; replying?: boolean } = {}) {
  const answers: object[] = [];
  const session = { id: 'chat1', title: 'Ports', created_at: now, updated_at: now, replying: options.replying ?? false };
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/chat/status', route => route.fulfill({ json: {
    provider: 'local', model_path: '', model_file: 'model.gguf', error: null, installed: true, model_ready: true } }));
  await page.route('**/api/chat/sessions', route => route.fulfill({ json: { sessions: [session] } }));
  // An earlier exchange, so the page has opened the chat before anything is typed.
  const earlier = [{ role: 'user', content: 'Hello' }, { role: 'assistant', content: 'Hi there.' }];
  await page.route('**/api/chat/sessions/*', route => route.fulfill({ json: { ...session, messages: options.messages ?? earlier, summary: null } }));
  await page.route('**/api/chat/sessions/*/answer', route => {
    answers.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true } });
  });
  // The reply streams up to its question, then waits for the test to finish it.
  await page.addInitScript(([head]) => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = String(input);
      if (!url.endsWith('/api/chat') && !url.endsWith('/reply')) return original(input, init);
      const encoder = new TextEncoder();
      const body = new ReadableStream({ start(controller) {
        controller.enqueue(encoder.encode(head));
        (window as unknown as { finishReply: (rest: string) => void }).finishReply = rest => {
          controller.enqueue(encoder.encode(rest));
          controller.close();
        };
      } });
      return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    };
  }, [sse(
    { type: 'token', text: 'Let me check. ' },
    { type: 'tool_call', name: 'ask_user', args: { question: 'Which project?', options: ['Imprint', 'Billing'] } },
    QUESTION,
  )]);
  return { answers, opened: () => expect(page.getByText('Hi there.', { exact: true })).toBeVisible(), finish: (...events: object[]) => page.evaluate(rest =>
    (window as unknown as { finishReply: (rest: string) => void }).finishReply(rest), sse(...events)) };
}

test('a question in the reply takes the focus, and a chosen option answers it', async ({ page }) => {
  const chat = await mockChat(page);
  await page.goto('/chat/');
  await chat.opened();
  await page.getByRole('textbox', { name: 'Message' }).fill('Which port?');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  const card = page.getByRole('group', { name: 'Which project?' });
  await expect(card).toContainText('Imprint waits for your answer');
  await expect(card.getByRole('button', { name: 'Imprint' })).toBeFocused();
  // The call shows as the question, not as a tool row.
  await expect(page.getByText(/Ran ask_user|Running ask_user/)).toHaveCount(0);
  await card.getByRole('button', { name: 'Billing' }).click();
  await expect.poll(() => chat.answers).toEqual([{ id: 'q1', answer: 'Billing' }]);
  await expect(card).toContainText('You answered: Billing');
  await expect(card.getByRole('button', { name: 'Imprint' })).toBeDisabled();
  await chat.finish({ type: 'answer', id: 'q1', text: 'Billing' },
    { type: 'tool_result', name: 'ask_user', result: 'The user answered: Billing' },
    { type: 'token', text: 'Billing is on 8101.' }, { type: 'done' });
  await expect(page.getByText('Billing is on 8101.')).toBeVisible();
});

test('an answer in the user\'s own words is sent with Enter, from the card or the message box', async ({ page }) => {
  const chat = await mockChat(page);
  await page.goto('/chat/');
  await chat.opened();
  await page.getByRole('textbox', { name: 'Message' }).fill('Which port?');
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  const card = page.getByRole('group', { name: 'Which project?' });
  await card.getByRole('textbox', { name: 'Your answer' }).fill('The payments service');
  await card.getByRole('textbox', { name: 'Your answer' }).press('Enter');
  await expect.poll(() => chat.answers).toEqual([{ id: 'q1', answer: 'The payments service' }]);
  await expect(card).toContainText('You answered: The payments service');
});

test('the message box answers a waiting question', async ({ page }) => {
  const chat = await mockChat(page);
  await page.goto('/chat/');
  await chat.opened();
  const box = page.getByRole('textbox', { name: 'Message' });
  await box.fill('Which port?');
  await box.press('Enter');
  await expect(page.getByRole('group', { name: 'Which project?' })).toBeVisible();
  await expect(box).toHaveAttribute('placeholder', "Answer Imprint's question…");
  await box.fill('Imprint');
  await box.press('Enter');
  await expect.poll(() => chat.answers).toEqual([{ id: 'q1', answer: 'Imprint' }]);
  await expect(page.getByRole('button', { name: 'Stop reply' })).toBeVisible();
});

test('a chat opened again while its reply waits shows the question, ready to answer', async ({ page }) => {
  const chat = await mockChat(page, { replying: true, messages: [{ role: 'user', content: 'Which port?' }] });
  await page.goto('/chat/');
  const card = page.getByRole('group', { name: 'Which project?' });
  await card.getByRole('button', { name: 'Imprint' }).click();
  await expect.poll(() => chat.answers).toEqual([{ id: 'q1', answer: 'Imprint' }]);
});

test('Chat history shows a stored question with its answer', async ({ page }) => {
  await mockChat(page, { messages: [
    { role: 'user', content: 'Which port?' },
    { role: 'tool', tool_name: 'ask_user', tool_args: { question: 'Which project?', options: ['Imprint', 'Billing'] },
      content: 'The user answered: Billing' },
    { role: 'assistant', content: 'Billing is on 8101.' },
  ] });
  await page.goto('/chat/');
  const card = page.getByRole('group', { name: 'Which project?' });
  await expect(card).toContainText('You answered: Billing');
  await expect(card.getByRole('button', { name: 'Billing' })).toHaveAttribute('aria-pressed', 'true');
  await expect(card.getByRole('button', { name: 'Imprint' })).toBeDisabled();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`the question card is legible in the ${scheme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    const chat = await mockChat(page);
    await page.goto('/chat/');
    await chat.opened();
    await page.getByRole('textbox', { name: 'Message' }).fill('Which port?');
    await page.getByRole('textbox', { name: 'Message' }).press('Enter');
    const card = page.getByRole('group', { name: 'Which project?' });
    const colors = await card.evaluate(element => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, color: style.color };
    });
    expect(colors.background).not.toBe(colors.color);
    expect(colors.background).not.toBe('rgba(0, 0, 0, 0)');
  });
}
