import { test, expect, type Page } from '@playwright/test';

// Shapes follow build_overview / build_project_detail (imprint/cli_viz.py) and
// projects.move (imprint/projects.py).
const project = (name: string, count: number) => ({ id: `proj_${name}`, name, count, types: {}, color: '#4ecdc4', topDomains: [], topLangs: [] });
const overview = (...projects: ReturnType<typeof project>[]) => ({ projects, total: projects.reduce((n, p) => n + p.count, 0), version: 1, facets: {} });
const detail = (name: string, count: number) => ({ project: name, count, color: '#4ecdc4', types: [], domains: [], langs: [], topics: [] });
const sources: Record<string, { source: string; chunks: number }[]> = {
  Desktop: [{ source: 'Desktop/Naming Questionnaire.pdf', chunks: 2 }, { source: 'Desktop/editor.pdf', chunks: 5 }],
  Documents: [{ source: 'Documents/handbook.md', chunks: 3 }],
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'alpha', workspaces: ['alpha'] } }));
  await page.route('**/api/queue**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/desktop-learn/history', route => route.fulfill({ json: { seen: {}, count: 0 } }));
  await page.route('**/api/project/*', route => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
    return route.fulfill({ json: detail(name, 7) });
  });
  await page.route('**/api/sources?**', route => {
    const name = new URL(route.request().url()).searchParams.get('project') ?? '';
    const list = sources[name] ?? [];
    return route.fulfill({ json: { sources: list, total: list.length } });
  });
});

async function quietStream(page: Page) {
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
}

test('a project created by a finished job appears without a reload', async ({ page }) => {
  let changed = false;
  let announce!: () => void;
  const ready = new Promise<void>(resolve => { announce = resolve; });
  await page.route('**/api/overview', route => route.fulfill({
    json: changed ? overview(project('Desktop', 7), project('fresh', 3)) : overview(project('Desktop', 7)),
  }));
  await page.route('**/api/stream', async route => {
    await ready;
    await route.fulfill({ contentType: 'text/event-stream', body: 'event: update\ndata: {"version":1}\n\n' });
  });
  await page.goto('/projects/');
  await expect(page.getByRole('heading', { name: 'Projects 1', exact: true })).toBeVisible();
  changed = true;
  announce();
  await expect(page.getByRole('heading', { name: 'Projects 2', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Projects' }).getByRole('button', { name: /fresh/ })).toBeVisible();
});

test('selected sources move to a new project after review and confirmation', async ({ page }) => {
  await quietStream(page);
  await page.route('**/api/overview', route => route.fulfill({ json: overview(project('Desktop', 7), project('Documents', 3)) }));
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/projects/move', async route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    await route.fulfill({ json: { workspace: 'alpha', project: body.project, target: body.target, whole: !body.sources,
      chunks: 2, sources: 1, target_exists: false, sample: body.sources ?? [], applied: !!body.apply } });
  });
  await page.goto('/projects/');
  await page.getByRole('navigation', { name: 'Projects' }).getByRole('button', { name: /Desktop/ }).click();
  const move = page.getByRole('button', { name: 'Move selected…' });
  await expect(move).toBeDisabled();
  await page.getByRole('checkbox', { name: 'Select Desktop/Naming Questionnaire.pdf' }).click();
  await expect(page.getByText('1 selected')).toBeVisible();
  await move.click();

  const dialog = page.getByRole('dialog', { name: 'Move sources to another project' });
  await dialog.getByRole('radio', { name: 'New project' }).click();
  await dialog.getByLabel('New project name').fill('branding');
  await dialog.getByRole('button', { name: 'Review move' }).click();
  await expect(dialog.getByRole('status')).toContainText('2 chunks from 1 source move from Desktop to branding, a new project.');
  expect(requests).toEqual([{ project: 'Desktop', target: 'branding', sources: ['Desktop/Naming Questionnaire.pdf'] }]);

  await dialog.getByRole('button', { name: 'Move 2 chunks' }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[1]).toEqual({ project: 'Desktop', target: 'branding', sources: ['Desktop/Naming Questionnaire.pdf'],
    workspace: 'alpha', apply: true });
  await expect(page.getByText('Moved 2 chunks to branding')).toBeVisible();
});

test('a whole project merges into an existing one and the page follows it', async ({ page }) => {
  await quietStream(page);
  let merged = false;
  await page.route('**/api/overview', route => route.fulfill({
    json: merged ? overview(project('Documents', 10)) : overview(project('Desktop', 7), project('Documents', 3)),
  }));
  await page.route('**/api/projects/move', async route => {
    const body = route.request().postDataJSON();
    merged = !!body.apply;
    await route.fulfill({ json: { workspace: 'alpha', project: 'Desktop', target: body.target, whole: true,
      chunks: 7, sources: 2, target_exists: true, sample: [], applied: !!body.apply } });
  });
  await page.goto('/projects/');
  await page.getByRole('navigation', { name: 'Projects' }).getByRole('button', { name: /Desktop/ }).click();
  await page.getByRole('button', { name: 'Move or merge…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Move or merge Desktop' });
  await dialog.getByRole('combobox', { name: 'Existing project' }).click();
  await page.getByRole('option', { name: 'Documents' }).click();
  await dialog.getByRole('button', { name: 'Review move' }).click();
  await expect(dialog.getByRole('status')).toContainText('to Documents, an existing project.');
  await expect(dialog.getByRole('status')).toContainText('Memories saved as Desktop later');
  await dialog.getByRole('button', { name: 'Move 7 chunks' }).click();
  await expect(page.getByRole('heading', { name: 'Projects 1', exact: true })).toBeVisible();
  await expect(page.getByText('Documents/handbook.md')).toBeVisible();
});
