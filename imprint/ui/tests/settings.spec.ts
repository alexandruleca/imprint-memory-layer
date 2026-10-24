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
