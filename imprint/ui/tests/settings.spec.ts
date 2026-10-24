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
