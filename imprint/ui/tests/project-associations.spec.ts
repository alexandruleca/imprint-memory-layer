import { test, expect } from '@playwright/test';

test('project suggestions show evidence, persist decisions and queue optional local analysis', async ({ page }) => {
  await page.route('**/api/ping', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', r => r.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', r => r.fulfill({ json: { active: 'alpha', workspaces: ['alpha'] } }));
  await page.route('**/api/queue**', r => r.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/stream', r => r.fulfill({ status: 204 }));
  await page.route('**/api/overview', r => r.fulfill({ json: { projects: [], total: 0, facets: {} } }));
  const decisions: Record<string, unknown>[] = [];
  const scans: Record<string, unknown>[] = [];
  let status = 'suggested';
  await page.route('**/api/projects/associations**', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON(); decisions.push(body); status = body.status;
    }
    await route.fulfill({ json: { workspace: 'alpha', projects: ['repository-a', 'repository-b'],
      sources: [{ source: 'conversation/shared', project: 'unassigned', title: 'A cross-project integration' }],
      associations: [{ source: 'conversation/shared', origin: 'unassigned', project: 'unassigned', target: 'repository-a',
        status, method: 'name', manual: status !== 'suggested', stale: false,
        evidence: [{ start: 10, end: 80, method: 'name', text: 'We need repository-a to share its API with repository-b.' }] }] } });
  });
  await page.route('**/api/commands/associate-projects', async route => {
    scans.push(route.request().postDataJSON()); await route.fulfill({ json: { job_id: 'job1', position: 1 } });
  });
  await page.goto('/projects/');
  await page.getByRole('button', { name: 'Review project associations' }).click();
  const dialog = page.getByRole('dialog', { name: 'Project associations', exact: true });
  await expect(dialog.getByText('We need repository-a to share its API with repository-b.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Accept', exact: true }).click();
  await expect(dialog.getByText('repository-a · accepted', { exact: true })).toBeVisible();
  expect(decisions[0]).toMatchObject({ workspace: 'alpha', project: 'unassigned', source: 'conversation/shared', target: 'repository-a', status: 'accepted' });
  await dialog.getByRole('button', { name: 'Remove link' }).click();
  await expect(dialog.getByText('repository-a · rejected', { exact: true })).toBeVisible();
  await dialog.getByLabel('Source', { exact: true }).selectOption(JSON.stringify(['unassigned', 'conversation/shared']));
  await dialog.getByLabel('Link to project', { exact: true }).fill('repository-b');
  await dialog.getByRole('button', { name: 'Add link', exact: true }).click();
  await expect.poll(() => decisions.length).toBe(3);
  expect(decisions[2]).toMatchObject({ workspace: 'alpha', target: 'repository-b', status: 'accepted' });
  await dialog.getByRole('button', { name: 'Analyze existing sources' }).click();
  await expect(dialog.getByRole('status')).toContainText('Project analysis queued');
  expect(scans[0]).toEqual({ workspace: 'alpha', model: false });
  await dialog.getByLabel('Use installed local helper').check();
  await dialog.getByRole('button', { name: 'Analyze existing sources' }).click();
  await expect.poll(() => scans.length).toBe(2);
  expect(scans[1]).toEqual({ workspace: 'alpha', model: true });
});

test('linked sources identify their canonical project and cannot be moved from a related one', async ({ page }) => {
  await page.route('**/api/ping', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', r => r.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/workspaces', r => r.fulfill({ json: { active: 'alpha', workspaces: ['alpha'] } }));
  await page.route('**/api/queue**', r => r.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/stream', r => r.fulfill({ status: 204 }));
  await page.route('**/api/overview', r => r.fulfill({ json: { projects: [{ name: 'repository-a', id: 'p1', count: 2, color: '#abcdef', types: {}, topDomains: [], topLangs: [] }], total: 2, facets: {} } }));
  await page.route('**/api/project/repository-a', r => r.fulfill({ json: { count: 2, types: [], domains: [], langs: [], topics: [] } }));
  await page.route('**/api/sources?**', r => r.fulfill({ json: { sources: [{ source: 'conversation/shared', project: 'unassigned', linked: true, chunks: 2 }] } }));
  await page.goto('/projects/');
  await page.getByRole('navigation', { name: 'Projects', exact: true }).getByRole('button', { name: /repository-a/ }).click();
  await expect(page.getByText('Linked from unassigned')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Select conversation/shared', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Move selected…', exact: true })).toBeDisabled();
});
