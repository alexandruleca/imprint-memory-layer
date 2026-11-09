import { test, expect, type Page } from '@playwright/test';

type Item = { id: string; kind: 'skill' | 'persona'; name: string; description: string; hash: string;
  state: 'active' | 'disabled' | 'retired'; origin: string; device: string; created_at: number; updated_at: number;
  paths: string[]; size: number; shadowed: boolean; files: Record<string, string> };

/** An item as the API lists it: without its files. */
function withoutFiles(item: Item): Omit<Item, 'files'> {
  const listed: Partial<Item> = { ...item };
  delete listed.files;
  return listed as Omit<Item, 'files'>;
}

const skillFile = (name: string, description: string, body: string) =>
  `---\nname: ${name}\ndescription: ${description}\n---\n\n${body}\n`;

const folders = [
  { kind: 'skill', folder: '/home/me/.claude/skills', clients: ['Claude Code', 'Cursor'] },
  { kind: 'skill', folder: '/home/me/.agents/skills', clients: ['Codex', 'Cursor'] },
  { kind: 'persona', folder: '/home/me/.claude/agents', clients: ['Claude Code'] },
];

// Mirrors imprint/api.py's /api/skills (ADRs 0168, 0169): GET lists items
// without files; POST /apply writes the clients' folders and lists what they
// hold that Imprint does not (found); GET /{id} adds files and history;
// PATCH edits files or state; POST /adopt keeps a found one.
async function mockSkills(page: Page) {
  let clock = 1_000;
  const items: Item[] = [
    { id: 'a'.repeat(32), kind: 'skill', name: 'cut-a-release', description: 'Cut a release', hash: 'h1', state: 'active',
      origin: 'user', device: 'laptop', created_at: 1, updated_at: 900, paths: ['SKILL.md', 'scripts/tag.sh'], size: 90,
      shadowed: false, files: { 'SKILL.md': skillFile('cut-a-release', 'Cut a release', 'Bump, tag, write notes.'),
                                'scripts/tag.sh': 'git tag "$1"\n' } },
    { id: 'b'.repeat(32), kind: 'persona', name: 'code-reviewer', description: 'Reviews diffs', hash: 'h2', state: 'active',
      origin: 'adopted', device: 'laptop', created_at: 2, updated_at: 950, paths: ['PERSONA.md'], size: 60, shadowed: false,
      files: { 'PERSONA.md': skillFile('code-reviewer', 'Reviews diffs', 'You review code.') } },
  ];
  const found = [{ kind: 'skill', name: 'lint', path: '/home/me/.cursor/skills/lint', folder: '/home/me/.cursor/skills',
                   clients: ['Cursor'], description: 'Lint the change', problem: '' }];
  const report = { folders, written: [], removed: [], taken: [], kept: [], at: 990,
                   problems: [{ path: '/home/me/.claude/skills/cut-a-release', problem: 'SKILL.md needs a description' }] };
  const state = { patches: [] as object[], posts: [] as object[], adopts: [] as object[] };
  const listed = () => items.map(withoutFiles);
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/skills', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      state.posts.push(body);
      const added: Item = { id: 'c'.repeat(32), kind: body.kind, name: body.name, description: body.description, hash: 'h3',
        state: 'active', origin: 'user', device: 'laptop', created_at: ++clock, updated_at: clock, paths: ['SKILL.md'],
        size: 10, shadowed: false, files: { 'SKILL.md': skillFile(body.name, body.description, body.instructions) } };
      items.push(added);
      return route.fulfill({ json: { ...added, report: { ...report, problems: [] } } });
    }
    await route.fulfill({ json: { items: listed(), report, kinds: ['skill', 'persona'],
      main: { skill: 'SKILL.md', persona: 'PERSONA.md' }, max_name: 64, max_description: 1024, max_file: 262144, max_total: 1048576 } });
  });
  await page.route('**/api/skills/*', async route => {
    const id = route.request().url().split('/').pop()!;
    const item = items.find(i => i.id === id)!;
    if (route.request().method() === 'PATCH') {
      const change = route.request().postDataJSON();
      state.patches.push({ id, ...change });
      Object.assign(item, change.files ? { files: change.files, hash: `h${++clock}` } : {},
                    change.state ? { state: change.state } : {}, { updated_at: ++clock });
      return route.fulfill({ json: { ...withoutFiles(item), report: { ...report, problems: [] } } });
    }
    await route.fulfill({ json: { ...item, history: [{ hash: 'old', state: 'active', device: 'desktop', updated_at: 800,
                                                         replaced_at: 850, paths: ['SKILL.md'] }] } });
  });
  await page.route('**/api/skills/apply', route => route.fulfill({ json: { items: listed(), report, found } }));
  await page.route('**/api/skills/adopt', async route => {
    const body = route.request().postDataJSON();
    state.adopts.push(body);
    found.splice(0);
    const adopted: Item = { id: 'd'.repeat(32), kind: 'skill', name: 'lint', description: 'Lint the change', hash: 'h4',
      state: 'active', origin: 'adopted', device: 'laptop', created_at: ++clock, updated_at: clock, paths: ['SKILL.md'], size: 10,
      shadowed: false, files: { 'SKILL.md': skillFile('lint', 'Lint the change', 'Run the linter.') } };
    items.push(adopted);
    await route.fulfill({ json: withoutFiles(adopted) });
  });
  return state;
}

test('lists skills and personas, where they go, and what was left alone', async ({ page }) => {
  await mockSkills(page);
  await page.goto('/skills/');
  const nav = page.getByRole('navigation', { name: 'Skills and personas' });
  await expect(nav.getByRole('button', { name: 'cut-a-release' })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'code-reviewer' })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'lint' })).toBeVisible();
  await expect(page.getByText('/home/me/.agents/skills')).toBeVisible();
  await expect(page.getByText('Codex, Cursor')).toBeVisible();
  await expect(page.getByText('SKILL.md needs a description')).toBeVisible();
});

test('edits a skill file and saves it, and disables it', async ({ page }) => {
  const state = await mockSkills(page);
  await page.goto('/skills/');
  await page.getByRole('navigation', { name: 'Skills and personas' }).getByRole('button', { name: 'cut-a-release' }).click();
  // The problem reported for its folder shows beside it.
  await expect(page.getByText('SKILL.md needs a description')).toBeVisible();
  await page.getByRole('tab', { name: 'scripts/tag.sh' }).click();
  const editor = page.getByRole('textbox', { name: 'scripts/tag.sh' });
  await expect(editor).toHaveValue('git tag "$1"\n');
  await editor.fill('git tag -s "$1"\n');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect.poll(() => state.patches.length).toBe(1);
  expect(state.patches[0]).toMatchObject({ files: { 'scripts/tag.sh': 'git tag -s "$1"\n' } });
  await page.getByRole('button', { name: 'Disable' }).click();
  await expect.poll(() => state.patches.length).toBe(2);
  expect(state.patches[1]).toMatchObject({ state: 'disabled' });
  await expect(page.getByRole('button', { name: 'Enable' })).toBeVisible();
});

test('writes a new skill from the form', async ({ page }) => {
  const state = await mockSkills(page);
  await page.goto('/skills/');
  await page.getByRole('button', { name: 'New skill' }).first().click();
  await page.getByLabel('Name').fill('Deploy App');
  await expect(page.getByLabel('Name')).toHaveValue('deploy-app');
  await page.getByLabel('Description').fill('Deploy the app to staging');
  await page.getByLabel('Instructions').fill('Run make deploy.');
  await page.getByRole('button', { name: 'Add skill' }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toEqual({ kind: 'skill', name: 'deploy-app', description: 'Deploy the app to staging', instructions: 'Run make deploy.' });
  await expect(page.getByRole('navigation', { name: 'Skills and personas' }).getByRole('button', { name: 'deploy-app' })).toBeVisible();
});

test('adopts a skill found in a client folder', async ({ page }) => {
  const state = await mockSkills(page);
  await page.goto('/skills/');
  await page.getByRole('navigation', { name: 'Skills and personas' }).getByRole('button', { name: 'lint' }).click();
  await expect(page.getByText('/home/me/.cursor/skills/lint')).toBeVisible();
  await page.getByRole('button', { name: 'Adopt', exact: true }).click();
  await expect.poll(() => state.adopts).toEqual([{ path: '/home/me/.cursor/skills/lint', kind: 'skill' }]);
  await expect(page.getByText('In your clients')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Skills and personas' }).getByRole('button', { name: 'lint' })).toBeVisible();
});
