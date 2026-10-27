import { test, expect } from '@playwright/test';

const stats = { total: 0, projects: [], langs: [], domains: [], types: [], topics: [] };
const overview = { projects: [], total: 0 };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/queue', route => route.fulfill({ json: { active: null, queued: [], history: [] } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
});

test('an open dashboard recovers after its API becomes reachable', async ({ page }) => {
  let reachable = false;
  await page.route('**/api/stats', route => reachable ? route.fulfill({ json: stats }) : route.abort('connectionrefused'));
  await page.route('**/api/overview', route => reachable ? route.fulfill({ json: overview }) : route.abort('connectionrefused'));
  await page.goto('/');
  const error = page.getByRole('alert').filter({ hasText: 'Failed to connect to API' });
  await expect(error).toBeVisible();
  await expect(page.getByText('Reconnecting automatically.', { exact: false })).toBeVisible();
  reachable = true;
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible({ timeout: 10000 });
  await expect(error).toHaveCount(0);
});

for (const failing of ['stats', 'overview']) {
  test(`an HTTP 500 from ${failing} explains the server error and can be retried`, async ({ page }) => {
    let fail = true;
    for (const [endpoint, json] of Object.entries({ stats, overview })) {
      await page.route(`**/api/${endpoint}`, route => fail && endpoint === failing
        ? route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' })
        : route.fulfill({ json }));
    }
    await page.goto('/');
    const error = page.getByRole('alert').filter({ hasText: 'Could not load dashboard' });
    await expect(error).toHaveText('Could not load dashboard: Request failed (500)');
    await expect(page.getByText('The API server responded with an error.', { exact: false })).toBeVisible();
    await expect(page.getByText('imprint ui start', { exact: true })).toHaveCount(0);
    fail = false;
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await expect(error).toHaveCount(0);
  });
}

test('an incompatible embedding index explains rebuilding and leaves workspace recovery available', async ({ page }) => {
  let active = 'default';
  const actions: string[] = [];
  await page.route('**/api/workspaces', route => route.fulfill({ json: {
    active, workspaces: active === 'default' ? ['default'] : ['default', active],
  } }));
  for (const [endpoint, json] of Object.entries({ stats, overview })) {
    await page.route(`**/api/${endpoint}`, route => active === 'default'
      ? route.fulfill({ status: 409, json: {
        error: 'embedding_profile_conflict', collection: 'memories',
        detail: "Embedding profile mismatch for collection 'memories'. Create a new workspace and reindex your sources.",
      } })
      : route.fulfill({ json }));
  }
  await page.route('**/api/workspace/create', async route => {
    actions.push(`create:${route.request().postDataJSON().name}`);
    await route.fulfill({ json: { ok: true, created: true, active, workspaces: ['default', 'rebuilt'] } });
  });
  await page.route('**/api/workspace/switch', async route => {
    active = route.request().postDataJSON().workspace;
    actions.push(`switch:${active}`);
    await route.fulfill({ json: { ok: true, active } });
  });
  await page.goto('/');
  const error = page.getByRole('alert').filter({ hasText: 'Memory index needs rebuilding' });
  await expect(error).toContainText("Embedding profile mismatch for collection 'memories'");
  await expect(page.getByText('Your old workspace is preserved;', { exact: false })).toBeVisible();
  await expect(page.getByText('imprint ui start', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Workspace: default' }).click();
  await page.getByRole('menuitem', { name: 'New workspace…' }).click();
  await page.getByLabel('New workspace name').fill('rebuilt');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Workspace: rebuilt' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await expect(error).toHaveCount(0);
  expect(actions).toEqual(['create:rebuilt', 'switch:rebuilt']);
});
