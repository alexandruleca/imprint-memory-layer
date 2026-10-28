import { test, expect, type Page } from '@playwright/test';

const TOKEN = 'hf_storedtoken0000wxyz';
const DESC = 'Hugging Face access token for model downloads';

test('chat research token budget has a clear label and saves its numeric budget', async ({ page }) => {
  const writes: unknown[] = [];
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'chat.research_token_budget', value: 65536, default: 65536, type: 'int', source: 'default', env: 'IMPRINT_CHAT_RESEARCH_TOKENS',
      desc: 'Research token reservations including prompts and section workers', choices: [] },
    { key: 'chat.research_timeout_sec', value: 300, default: 300, type: 'int', source: 'default', env: 'IMPRINT_CHAT_RESEARCH_TIMEOUT',
      desc: 'Research time budget', choices: [] },
    { key: 'chat.research_no_progress_rounds', value: 4, default: 4, type: 'int', source: 'default', env: 'IMPRINT_CHAT_RESEARCH_PATIENCE',
      desc: 'Read rounds without new evidence', choices: [] },
  ] } }));
  await page.route('**/api/config/chat.research_token_budget', async route => {
    writes.push(route.request().postDataJSON()); await route.fulfill({ json: { ok: true } });
  });
  await page.goto('/settings/#settings-chat');
  const input = page.getByRole('textbox', { name: 'Research token budget', exact: true });
  await expect(page.getByRole('textbox', { name: 'Research time budget (seconds)', exact: true })).toHaveValue('300');
  await expect(page.getByRole('textbox', { name: 'Rounds without new evidence', exact: true })).toHaveValue('4');
  await expect(page.getByRole('textbox', { name: 'Tool research rounds', exact: true })).toHaveCount(0);
  await expect(input).toHaveValue('65536');
  await input.fill('131072');
  await input.press('Enter');
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({ value: '131072' });
});

test('audio ingestion offers model and language choices in Settings', async ({ page }) => {
  const state: Record<string, string> = { 'audio.model': 'large-v3-turbo', 'audio.language': 'auto' };
  const writes: unknown[] = [];
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'audio.model', value: state['audio.model'], default: 'large-v3-turbo', type: 'str', source: 'default', env: 'IMPRINT_AUDIO_MODEL',
      desc: 'Multilingual transcription model; both run locally on CPU', custom: false, choices: [
        { value: 'large-v3-turbo', label: 'Large v3 Turbo', detail: 'About 1.51 GiB', unavailable: '' },
        { value: 'small', label: 'Small', detail: 'About 464 MiB', unavailable: '' },
      ] },
    { key: 'audio.language', value: state['audio.language'], default: 'auto', type: 'str', source: 'default', env: 'IMPRINT_AUDIO_LANGUAGE',
      desc: 'Transcription language', custom: false, choices: [
        { value: 'auto', label: 'Auto', detail: '', unavailable: '' },
        { value: 'ro', label: 'Romanian', detail: '', unavailable: '' },
        { value: 'en', label: 'English', detail: '', unavailable: '' },
      ] },
  ] } }));
  await page.route('**/api/config/audio.*', async route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!;
    const body = route.request().postDataJSON(); writes.push({ key, ...body }); state[key] = body.value;
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto('/settings/#settings-audio');
  const language = page.getByRole('combobox', { name: 'Language', exact: true });
  await language.click();
  await page.getByRole('option', { name: 'Romanian', exact: true }).click();
  await expect(language).toContainText('Romanian');
  const model = page.getByRole('combobox', { name: 'Model', exact: true });
  await expect(model).toContainText('Large v3 Turbo');
  await model.click();
  await page.getByRole('option', { name: /^Small/ }).click();
  await expect(model).toContainText('Small');
  expect(writes).toEqual([{ key: 'audio.language', value: 'ro' }, { key: 'audio.model', value: 'small' }]);
});

test('chat section analysis defaults to two sequential workers and can be changed and reset', async ({ page }) => {
  const state = { value: 2, source: 'default', writes: [] as unknown[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [{
    key: 'chat.analysis_workers', value: state.value, default: 2, source: state.source, type: 'analysis_worker_count',
    env: 'IMPRINT_CHAT_ANALYSIS_WORKERS', desc: 'Sequential workers for oversized questions and evidence.', custom: false,
    choices: Array.from({ length: 9 }, (_, n) => ({ value: n, label: n ? String(n) : 'Off', detail: '', unavailable: '' })),
  }] } }));
  await page.route('**/api/config/chat.analysis_workers', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      state.writes.push(body);
      state.value = Number(body.value); state.source = 'config';
    } else {
      state.value = 2; state.source = 'default';
    }
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto('/settings/#settings-chat');
  const count = page.getByRole('combobox', { name: 'Sequential analysis workers' });
  await expect(count).toContainText('2');
  await count.click();
  await page.getByRole('option', { name: '3', exact: true }).click();
  await expect(count).toContainText('3');
  expect(state.writes).toEqual([{ value: '3' }]);
  await expect(page.getByText('Changed', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Reset', exact: true }).click();
  await expect(count).toContainText('2');
  await count.click();
  await page.getByRole('option', { name: 'Off', exact: true }).click();
  await expect(count).toContainText('Off');
});

// Mirrors imprint/api.py: secret values only ever leave the API masked.
async function mockApi(page: Page, initial: { value: string; source: string }) {
  const state = { ...initial, puts: [] as unknown[], deletes: 0 };
  const masked = () => (state.value ? '****' + state.value.slice(-4) : '');
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 0, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'huggingface.token', value: masked(), source: state.source, default: '', type: 'str', env: 'HF_TOKEN', desc: DESC, secret: true },
    { key: 'model.device', value: 'cpu', source: 'default', default: 'cpu', type: 'str', env: 'IMPRINT_DEVICE', desc: 'Compute device' },
  ] } }));
  await page.route('**/api/config/huggingface.token', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      state.puts.push(body);
      state.value = body.value;
      state.source = 'config';
      return route.fulfill({ json: { ok: true, value: masked() } });
    }
    state.deletes += 1;
    state.value = '';
    state.source = 'default';
    await route.fulfill({ json: { ok: true, removed: true } });
  });
  return state;
}

test('a Hugging Face token is entered hidden, shown masked and can be removed', async ({ page }) => {
  const state = await mockApi(page, { value: '', source: 'default' });
  await page.goto('/settings/');
  const card = page.locator('[data-slot="card"]', { hasText: 'huggingface.token' });
  await card.getByRole('button', { name: 'Not set' }).click();
  const input = card.getByLabel('huggingface.token');
  await expect(input).toHaveAttribute('type', 'password');
  await expect(input).toHaveValue('');
  await input.fill(TOKEN);
  await card.getByRole('button', { name: 'Save' }).click();
  await expect(card.getByRole('button', { name: '****wxyz' })).toBeVisible();
  expect(state.puts).toEqual([{ value: TOKEN }]);
  await expect(page.locator('body')).not.toContainText(TOKEN);

  await card.getByRole('button', { name: '****wxyz' }).click();
  await expect(card.getByLabel('huggingface.token')).toHaveValue('');
  await card.getByRole('button', { name: 'Cancel' }).click();

  await card.getByRole('button', { name: 'Remove' }).click();
  await expect(card.getByRole('button', { name: 'Not set' })).toBeVisible();
  expect(state.deletes).toBe(1);
});

test('an HF_TOKEN environment variable is reported as taking precedence', async ({ page }) => {
  await mockApi(page, { value: 'hf_environment000abcd', source: 'env' });
  await page.goto('/settings/');
  const card = page.locator('[data-slot="card"]', { hasText: 'huggingface.token' });
  await expect(card.getByRole('button', { name: '****abcd' })).toBeVisible();
  await expect(card).toContainText('HF_TOKEN is set in the environment and takes precedence.');
  await expect(card.getByRole('button', { name: 'Remove' })).toHaveCount(0);
});

const RTX = 'NVIDIA GeForce RTX 4080 Laptop GPU';
const NO_GPU = 'llama.cpp found no GPU it can use';

// Mirrors imprint/api.py for a setting whose choices are read from the device:
// the GPUs llama.cpp offloads to, in its order (ADR 0086).
async function mockGpuApi(page: Page) {
  const state = { value: '0', puts: [] as unknown[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'model.device', value: 'auto', source: 'default', default: 'auto', type: 'str', env: 'IMPRINT_DEVICE',
      desc: 'Compute device: auto / cpu / gpu', custom: false, choices: [
        { value: 'auto', label: 'Auto', detail: RTX, unavailable: '' },
        { value: 'cpu', label: 'CPU', detail: '', unavailable: '' },
        { value: 'gpu', label: 'GPU', detail: RTX, unavailable: '' },
      ] },
    { key: 'model.gpu_device', value: state.value, source: 'config', default: 'auto', type: 'device_index',
      env: 'IMPRINT_GPU_DEVICE', desc: 'GPU that runs local models', custom: true, choices: [
        { value: 'auto', label: 'Auto', detail: 'The discrete GPU with the most memory', unavailable: '' },
        { value: '0', label: '0: NVIDIA GeForce GTX 1060 6GB', detail: '6 GB', unavailable: '' },
        { value: '1', label: `1: ${RTX}`, detail: '12 GB', unavailable: '' },
      ] },
  ] } }));
  await page.route('**/api/config/model.gpu_device', async route => {
    const body = route.request().postDataJSON();
    state.puts.push(body);
    state.value = body.value;
    await route.fulfill({ json: { ok: true, value: body.value } });
  });
  return state;
}

test('GPUs are picked from the list llama.cpp offloads to, or entered by index', async ({ page }) => {
  const state = await mockGpuApi(page);
  await page.goto('/settings/');
  const trigger = page.getByRole('combobox', { name: 'GPU device' });
  await expect(trigger).toHaveText(/0: NVIDIA GeForce GTX 1060 6GB/);

  await trigger.click();
  await page.getByRole('option', { name: /1: NVIDIA GeForce RTX 4080/ }).click();
  await expect.poll(() => state.puts).toEqual([{ value: '1' }]);
  await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
  await expect(trigger).toHaveText(/1: NVIDIA GeForce RTX 4080/);

  await trigger.click();
  await page.getByRole('option', { name: 'Custom value…' }).click();
  const custom = page.getByLabel('Custom GPU device');
  await custom.fill('3');
  await page.getByRole('button', { name: 'Save model.gpu_device' }).click();
  await expect.poll(() => state.puts).toEqual([{ value: '1' }, { value: '3' }]);
  await expect(trigger).toHaveText('Custom: 3');
  await expect(custom).toHaveValue('3');
});

// Mirrors imprint/api.py: model.device's choices say what each value does on
// this device. Every value uses the same llama.cpp, so a change installs
// nothing (ADR 0086).
async function mockDeviceApi(page: Page, value: string, auto: string, gpu: { detail: string; unavailable: string }) {
  const state = { value, puts: [] as unknown[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/queue**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'model.device', value: state.value, source: state.value === 'auto' ? 'default' : 'config', default: 'auto', type: 'str',
      env: 'IMPRINT_DEVICE', desc: 'Compute device: auto / cpu / gpu', custom: false, choices: [
        { value: 'auto', label: 'Auto', detail: auto, unavailable: '' },
        { value: 'cpu', label: 'CPU', detail: '', unavailable: '' },
        { value: 'gpu', label: 'GPU', ...gpu },
      ] },
  ] } }));
  await page.route('**/api/config/model.device', async route => {
    const body = route.request().postDataJSON();
    state.puts.push(body);
    state.value = body.value;
    await route.fulfill({ json: { ok: true, value: body.value } });
  });
  return state;
}

test('choosing GPU embedding names the GPU and installs nothing', async ({ page }) => {
  const state = await mockDeviceApi(page, 'cpu', RTX, { detail: RTX, unavailable: '' });
  await page.goto('/settings/');
  const trigger = page.getByRole('combobox', { name: 'Device' });
  await trigger.click();
  const option = page.getByRole('option', { name: /^GPU/ });
  await expect(option).toContainText(RTX);
  await option.click();
  await expect.poll(() => state.puts).toEqual([{ value: 'gpu' }]);
  await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
  await expect(page.locator('[data-sonner-toast]')).toHaveCount(0);
});

test('a device where llama.cpp finds no GPU cannot choose GPU embedding', async ({ page }) => {
  await mockDeviceApi(page, 'cpu', `CPU here: ${NO_GPU}`, { detail: '', unavailable: NO_GPU });
  await page.goto('/settings/');
  await page.getByRole('combobox', { name: 'Device' }).click();
  const option = page.getByRole('option', { name: /^GPU/ });
  await expect(option).toContainText(NO_GPU);
  await expect(option).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByRole('option', { name: /^Auto/ })).toContainText(`CPU here: ${NO_GPU}`);
});

test('GPU embedding chosen where llama.cpp finds no GPU says why it runs on the CPU', async ({ page }) => {
  await mockDeviceApi(page, 'gpu', `CPU here: ${NO_GPU}`, { detail: '', unavailable: NO_GPU });
  await page.goto('/settings/');
  await expect(page.getByRole('alert').filter({ hasText: NO_GPU })).toBeVisible();
});

// Mirrors imprint/api.py for workspace.unassigned (ADR 0072): its choices are
// following the active workspace (the empty value) and this device's workspaces.
test('desktop app chats and unassigned folders get a workspace picked from this device\'s', async ({ page }) => {
  const state = { value: '', puts: [] as unknown[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default', 'personal'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'workspace.unassigned', value: state.value, source: state.value ? 'config' : 'default', default: '', type: 'str',
      env: 'IMPRINT_UNASSIGNED_WORKSPACE', custom: false,
      desc: 'Workspace for agent sessions outside an assigned project, such as desktop app chats (empty: the active workspace)',
      choices: [
        { value: '', label: 'Active workspace', detail: 'Follow the workspace the CLI and dashboard select', unavailable: '' },
        { value: 'default', label: 'default', detail: '', unavailable: '' },
        { value: 'personal', label: 'personal', detail: '', unavailable: '' },
      ] },
  ] } }));
  await page.route('**/api/config/workspace.unassigned', async route => {
    if (route.request().method() === 'DELETE') {
      state.puts.push('reset');
      state.value = '';
      await route.fulfill({ json: { ok: true } });
      return;
    }
    const body = route.request().postDataJSON();
    state.puts.push(body);
    state.value = body.value;
    await route.fulfill({ json: { ok: true, value: body.value } });
  });
  await page.goto('/settings/');
  const card = page.locator('#settings-workspace');
  await expect(card).toContainText('Workspace selection');
  const trigger = card.getByRole('combobox', { name: 'Unassigned' });
  await expect(trigger).toHaveText(/Active workspace/);

  await trigger.click();
  await page.getByRole('option', { name: 'personal' }).click();
  await expect.poll(() => state.puts).toEqual([{ value: 'personal' }]);
  await expect(trigger).toHaveText('personal');

  await trigger.click();
  await page.getByRole('option', { name: /Active workspace/ }).click();
  await expect.poll(() => state.puts).toEqual([{ value: 'personal' }, { value: '' }]);
  await expect(trigger).toHaveText(/Active workspace/);
});

test('one section shows at a time; its link, a deep link and the filter reach the others', async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'model.device', value: 'auto', source: 'default', default: 'auto', type: 'str', env: 'IMPRINT_DEVICE', desc: 'Compute device' },
    { key: 'chat.provider', value: 'local', source: 'config', default: 'local', type: 'str', env: 'IMPRINT_CHAT_PROVIDER', desc: 'Chat model provider' },
  ] } }));
  const sections = page.getByRole('navigation', { name: 'Settings sections' });
  await page.goto('/settings/');
  await expect(page.getByText('model.device')).toBeVisible();
  await expect(page.getByText('chat.provider')).toHaveCount(0);
  await expect(sections.getByRole('link', { name: /Embeddings/ })).toHaveAttribute('aria-current', 'page');

  await sections.getByRole('link', { name: /Chat/ }).click();
  await expect(page).toHaveURL(/#settings-chat$/);
  await expect(page.getByText('chat.provider')).toBeVisible();
  await expect(page.getByText('model.device')).toHaveCount(0);

  await page.getByLabel('Filter settings').fill('provider device');
  await expect(page.getByText('Nothing matches')).toBeVisible();
  await page.getByLabel('Filter settings').fill('model');
  await expect(page.getByText('model.device')).toBeVisible();
  await page.getByLabel('Filter settings').fill('');

  await page.goto('/settings/#workspaces');
  await expect(sections.getByRole('link', { name: 'Workspaces' })).toHaveAttribute('aria-current', 'page');
});
