import { test, expect, type Page } from '@playwright/test';

async function mockApi(page: Page, workspaces = { active: 'alpha', workspaces: ['default', 'alpha', 'beta'] }) {
  const state = { ...workspaces, workspaces: [...workspaces.workspaces] };
  const roots: Record<string, string[]> = { alpha: ['/home/me/notes'] };
  const posts: Record<string, unknown[]> = { switch: [], create: [], commands: [], roots: [] };
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 3, projects: [], langs: [], domains: [], types: [], topics: [] } }));
  await page.route('**/api/overview', route => route.fulfill({ json: { projects: [], total_memories: 3, total_facts: 0 } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: state }));
  await page.route('**/api/workspace/roots?**', route => {
    const workspace = new URL(route.request().url()).searchParams.get('workspace');
    route.fulfill({ json: { workspace, roots: roots[workspace ?? ''] ?? [], exclusions: [] } });
  });
  await page.route('**/api/workspace/roots', async route => {
    const body = route.request().postDataJSON();
    posts.roots.push(body);
    roots[body.workspace] = [...(roots[body.workspace] ?? []), body.path];
    await route.fulfill({ json: { workspace: body.workspace, roots: roots[body.workspace], exclusions: [] } });
  });
  await page.route('**/api/workspace/switch', async route => {
    const body = route.request().postDataJSON();
    posts.switch.push(body);
    state.active = body.workspace;
    if (!state.workspaces.includes(body.workspace)) state.workspaces.push(body.workspace);
    await route.fulfill({ json: { ok: true, active: state.active } });
  });
  await page.route('**/api/workspace/create', async route => {
    const body = route.request().postDataJSON();
    posts.create.push(body);
    if (!/^[a-z0-9][a-z0-9-]*$/.test(body.name)) {
      return route.fulfill({ status: 400, json: { error: 'must be lowercase alphanumeric + hyphens, start with letter/digit' } });
    }
    state.workspaces.push(body.name);
    await route.fulfill({ json: { ok: true, created: true, active: state.active, workspaces: state.workspaces } });
  });
  await page.route('**/api/commands/*', async route => {
    posts.commands.push({ command: route.request().url().split('/').pop(), body: route.request().postDataJSON() });
    await route.fulfill({ json: { job_id: 'j1', position: 0 } });
  });
  return { state, posts };
}

test('top bar switches the active workspace and reloads', async ({ page }) => {
  const { posts } = await mockApi(page);
  await page.goto('/');
  const trigger = page.getByRole('button', { name: 'Workspace: alpha' });
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(page.getByRole('button', { name: 'alpha', exact: true })).toHaveAttribute('aria-current', 'true');
  await page.getByRole('button', { name: 'beta', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Workspace: beta' })).toBeVisible();
  expect(posts.switch).toEqual([{ workspace: 'beta' }]);
});

test('top bar creates a workspace and switches to it', async ({ page }) => {
  const { posts } = await mockApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Workspace: alpha' }).click();
  await page.getByRole('button', { name: 'New workspace' }).click();
  const name = page.getByLabel('New workspace name');
  await name.fill('Bad Name');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.getByText('must be lowercase alphanumeric')).toBeVisible();
  expect(posts.switch).toEqual([]);
  await name.fill('research');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Workspace: research' })).toBeVisible();
  expect(posts.switch).toEqual([{ workspace: 'research' }]);
});

test('manage dialog edits roots and guards deletion', async ({ page }) => {
  const { posts } = await mockApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Workspace: alpha' }).click();
  await page.getByRole('button', { name: 'Manage workspaces…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Manage workspaces' });
  await expect(dialog.getByRole('listitem').filter({ hasText: 'alpha' })).toContainText('/home/me/notes');

  await expect(dialog.getByRole('button', { name: 'Delete alpha' })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: 'Delete default' })).toBeDisabled();

  await dialog.getByRole('button', { name: 'Edit beta' }).click();
  await dialog.getByLabel('Workspace root directory').fill('/data/beta');
  await dialog.getByRole('button', { name: 'Add root' }).click();
  await expect(dialog.getByRole('listitem').filter({ hasText: 'beta' }).first()).toContainText('/data/beta');
  expect(posts.roots).toEqual([{ workspace: 'beta', path: '/data/beta', action: 'add', allow_overlap: false }]);

  await dialog.getByRole('button', { name: 'Delete beta' }).click();
  const confirm = page.getByRole('dialog', { name: "Delete workspace 'beta'?" });
  await expect(confirm.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled();
  await confirm.getByLabel('Type beta to confirm').fill('beta');
  await expect(confirm.getByRole('button', { name: 'Delete', exact: true })).toBeEnabled();
});

test('ingest document queues a single-file ingest', async ({ page }) => {
  const { posts } = await mockApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: /Ingest Document/ }).click();
  await page.getByRole('button', { name: 'Run' }).click();
  await expect(page.getByText('Document is required')).toBeVisible();
  await page.getByLabel('Document').fill('/home/me/report.pdf');
  await page.getByRole('button', { name: 'Run' }).click();
  await expect(page.getByText('Started now.')).toBeVisible();
  expect(posts.commands).toEqual([{ command: 'ingest', body: { dir: '/home/me/report.pdf' } }]);
});

test('a Windows server offers its WSL distributions as picker roots', async ({ page }) => {
  const { posts } = await mockApi(page);
  const share = '\\\\wsl.localhost\\Ubuntu\\';
  const home = `${share}home`;
  await page.route('**/api/fs/roots', route => route.fulfill({ json: { platform: 'Windows', wsl: false, roots: [
    { label: 'Home', path: 'C:\\Users\\me' }, { label: 'C:', path: 'C:\\' }, { label: 'WSL Ubuntu', path: share },
  ] } }));
  const listed: string[] = [];
  await page.route('**/api/fs/list**', route => {
    const path = new URL(route.request().url()).searchParams.get('path') ?? '';
    listed.push(path);
    // Breadcrumbs come from the server, in its own path syntax.
    const listing = path === share
      ? { path: share, parent: null, entries: [{ name: 'home', is_dir: true, abs_path: home }],
          crumbs: [{ label: '\\\\wsl.localhost\\Ubuntu', path: share }] }
      : path === home
        ? { path: home, parent: share, entries: [],
            crumbs: [{ label: '\\\\wsl.localhost\\Ubuntu', path: share }, { label: 'home', path: home }] }
        : { path: 'C:\\Users\\me', parent: 'C:\\Users', entries: [],
            crumbs: [{ label: 'C:', path: 'C:\\' }, { label: 'Users', path: 'C:\\Users' }, { label: 'me', path: 'C:\\Users\\me' }] };
    route.fulfill({ json: { is_file: false, ...listing } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Workspace: alpha' }).click();
  await page.getByRole('button', { name: 'Manage workspaces…' }).click();
  const manager = page.getByRole('dialog', { name: 'Manage workspaces' });
  await manager.getByRole('button', { name: 'Edit beta' }).click();
  await manager.getByRole('button', { name: 'Pick folder' }).click();
  const picker = page.getByRole('dialog', { name: 'Pick a root folder' });
  await picker.getByRole('button', { name: 'WSL Ubuntu' }).click();
  await picker.getByRole('button', { name: 'home', exact: true }).click();
  await expect(picker.getByRole('button', { name: /^\\\\wsl\.localhost\\Ubuntu/ })).toBeVisible();
  await picker.getByRole('button', { name: 'Select this folder' }).click();
  await expect(manager.getByLabel('Workspace root directory')).toHaveValue(home);
  await manager.getByRole('button', { name: 'Add root' }).click();
  expect(posts.roots).toEqual([{ workspace: 'beta', path: home, action: 'add', allow_overlap: false }]);
  expect(listed).toContain(share);
});
