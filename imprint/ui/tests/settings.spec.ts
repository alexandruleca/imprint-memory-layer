import { test, expect, type Page } from '@playwright/test';

const TOKEN = 'hf_storedtoken0000wxyz';
const DESC = 'Hugging Face access token for model downloads';

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

const UNSUPPORTED = 'CUDA 13 needs compute capability 7.5+, this GPU has 6.1';

// Mirrors imprint/api.py for a setting whose choices are read from the device.
async function mockGpuApi(page: Page) {
  const state = { value: '1', puts: [] as unknown[] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'model.device', value: 'gpu', source: 'config', default: 'cpu', type: 'str', env: 'IMPRINT_DEVICE',
      desc: 'Compute device: auto / cpu / gpu', custom: false, choices: [
        { value: 'auto', label: 'Auto', detail: '', unavailable: '' },
        { value: 'cpu', label: 'CPU', detail: '', unavailable: '' },
        { value: 'gpu', label: 'GPU', detail: '', unavailable: '' },
      ] },
    { key: 'model.gpu_device', value: state.value, source: 'config', default: 'auto', type: 'device_index',
      env: 'IMPRINT_GPU_DEVICE', desc: 'CUDA device index', custom: true, choices: [
        { value: 'auto', label: 'Auto', detail: 'First GPU the installed CUDA runtime supports', unavailable: '' },
        { value: '0', label: '0: NVIDIA GeForce RTX 4080 Laptop GPU', detail: '12 GB, compute 8.9', unavailable: '' },
        { value: '1', label: '1: NVIDIA GeForce GTX 1060 6GB', detail: '6 GB, compute 6.1', unavailable: UNSUPPORTED },
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

test('GPUs are picked from a list that explains which ones cannot run, or entered by index', async ({ page }) => {
  const state = await mockGpuApi(page);
  await page.goto('/settings/');
  const trigger = page.getByRole('combobox', { name: 'GPU device' });
  await expect(trigger).toHaveText(/1: NVIDIA GeForce GTX 1060 6GB/);
  await expect(page.getByRole('alert').filter({ hasText: UNSUPPORTED })).toBeVisible();

  await trigger.click();
  await page.getByRole('option', { name: /0: NVIDIA GeForce RTX 4080/ }).click();
  await expect.poll(() => state.puts).toEqual([{ value: '0' }]);
  await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
  await expect(trigger).toHaveText(/0: NVIDIA GeForce RTX 4080/);
  await expect(page.getByRole('alert').filter({ hasText: UNSUPPORTED })).toHaveCount(0);

  await trigger.click();
  await page.getByRole('option', { name: 'Custom value…' }).click();
  const custom = page.getByLabel('Custom GPU device');
  await custom.fill('3');
  await page.getByRole('button', { name: 'Save model.gpu_device' }).click();
  await expect.poll(() => state.puts).toEqual([{ value: '0' }, { value: '3' }]);
  await expect(trigger).toHaveText('Custom: 3');
  await expect(custom).toHaveValue('3');
});

const RTX = 'NVIDIA GeForce RTX 4080 Laptop GPU, CUDA 13';

// Mirrors imprint/api.py: model.device's choices say what each value does on
// this device, a change that needs another ONNX Runtime build starts the
// `imprint bootstrap` that installs it (ADR 0062, 0084), and the runtime it
// still needs is reported with the last bootstrap's log.
async function mockDeviceApi(page: Page, auto: string, gpu: { detail: string; unavailable: string }) {
  const state = {
    value: 'cpu', puts: [] as unknown[], provisions: 0,
    runtime: { pending: '', provisioning: false, log: [] as string[] },
  };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/queue**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/runtime/provision', async route => {
    state.provisions++;
    state.runtime = { ...state.runtime, provisioning: true };
    await route.fulfill({ json: state.runtime });
  });
  await page.route('**/api/config', route => route.fulfill({ json: { runtime: state.runtime, settings: [
    { key: 'model.device', value: state.value, source: state.value === 'cpu' ? 'default' : 'config', default: 'cpu', type: 'str',
      env: 'IMPRINT_DEVICE', desc: 'Compute device: auto / cpu / gpu (NVIDIA GPUs, through CUDA)', custom: false, choices: [
        { value: 'auto', label: 'Auto', detail: auto, unavailable: '' },
        { value: 'cpu', label: 'CPU', detail: 'The default model runs faster on the CPU', unavailable: '' },
        { value: 'gpu', label: 'GPU', ...gpu },
      ] },
  ] } }));
  await page.route('**/api/config/model.device', async route => {
    const body = route.request().postDataJSON();
    state.puts.push(body);
    state.value = body.value;
    await route.fulfill({ json: { ok: true, value: body.value, provisioning: body.value !== 'cpu' } });
  });
  return state;
}

test('choosing GPU embedding says what it installs and starts the installation', async ({ page }) => {
  const installs = `${RTX}; installs the CUDA runtime, more than 1 GB`;
  const state = await mockDeviceApi(page, installs, { detail: installs, unavailable: '' });
  await page.goto('/settings/');
  const trigger = page.getByRole('combobox', { name: 'Device' });
  await trigger.click();
  const option = page.getByRole('option', { name: /^GPU/ });
  await expect(option).toContainText('installs the CUDA runtime, more than 1 GB');
  await option.click();
  await expect.poll(() => state.puts).toEqual([{ value: 'gpu' }]);
  const toast = page.locator('[data-sonner-toast]', { hasText: 'Installing the embedding runtime' });
  await expect(toast).toContainText('The dashboard restarts while it replaces the runtime');
});

test('a runtime setup did not install shows its last log and installs again', async ({ page }) => {
  const installs = `${RTX}; installs the CUDA runtime, more than 1 GB`;
  const state = await mockDeviceApi(page, installs, { detail: installs, unavailable: '' });
  state.value = 'gpu';
  state.runtime = {
    pending: `the CUDA runtime (more than 1 GB) for ${RTX}`, provisioning: false,
    log: ['[x] Installing dependencies for the gpu runtime failed: Access is denied. (os error 5)'],
  };
  await page.goto('/settings/');
  const alert = page.getByRole('alert').filter({ hasText: 'The embedding runtime does not match this device' });
  await expect(alert).toContainText(`Setup still has to install the CUDA runtime (more than 1 GB) for ${RTX}`);
  await expect(alert).toContainText('Access is denied');
  await alert.getByRole('button', { name: 'Install' }).click();
  await expect.poll(() => state.provisions).toBe(1);
  await expect(page.locator('[data-sonner-toast]', { hasText: 'Installing the embedding runtime' })).toBeVisible();
  await expect(alert).toBeHidden();
});

test('a device without a usable NVIDIA GPU cannot choose GPU embedding', async ({ page }) => {
  await mockDeviceApi(page, 'CPU here: no NVIDIA GPU found', { detail: '', unavailable: 'No NVIDIA GPU found' });
  await page.goto('/settings/');
  await page.getByRole('combobox', { name: 'Device' }).click();
  const option = page.getByRole('option', { name: /^GPU/ });
  await expect(option).toContainText('No NVIDIA GPU found');
  await expect(option).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByRole('option', { name: /^Auto/ })).toContainText('CPU here: no NVIDIA GPU found');
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
