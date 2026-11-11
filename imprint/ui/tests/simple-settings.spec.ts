import { test, expect, type Page } from '@playwright/test';
import { base } from './ui-fixtures';

// Settings in the simple dashboard (ADR 0182): plain choices the server
// lists (imprint/config_simple.py), each over settings the advanced page
// shows too.

type Item = Record<string, unknown> & { id: string; kind: string };
const item = (id: string, title: string, rest: Record<string, unknown>): Item => ({ id, title, desc: `About ${title}.`, desktop: false, ...rest } as unknown as Item);

function groups() {
  return [
    { id: 'assistant', title: 'Assistant', desc: 'The model that answers.', advanced: [{ id: 'settings-chat', title: 'Chat' }], items: [
      item('assistant-runs', 'Where the assistant runs', { kind: 'choice', selected: 0, locked: '', keys: ['chat.provider'], options: [
        { label: 'On this computer', detail: 'Private.', unavailable: '' },
        { label: 'Anthropic (Claude)', detail: 'Needs a key.', unavailable: 'No key found on this computer (ANTHROPIC_API_KEY)' },
      ] }),
      item('assistant-depth', 'How far it looks before answering', { kind: 'choice', selected: null, locked: '', keys: ['chat.research_timeout_sec'], options: [
        { label: 'Quick', detail: 'Up to 2 minutes', unavailable: '' },
        { label: 'Balanced', detail: 'Up to 5 minutes', unavailable: '' },
        { label: 'Thorough', detail: 'Up to 15 minutes', unavailable: '' },
      ] }),
    ] },
    { id: 'recordings', title: 'Recordings', desc: 'Meetings and calls.', advanced: [], items: [
      item('recordings-voices', 'Tell voices apart', { kind: 'switch', on: true, locked: '', keys: ['audio.diarize'] }),
      item('recordings-locked', 'Language spoken', { kind: 'switch', on: false, locked: 'IMPRINT_AUDIO_LANGUAGE', keys: ['audio.language'] }),
    ] },
    { id: 'privacy', title: 'Privacy', desc: 'What leaves.', advanced: [], items: [
      item('privacy-leaves', 'What leaves this computer', { kind: 'report', ok: true, lines: ['Nothing. Chat, labels and recaps all run on this computer.'], note: 'Web pages you add are fetched from their sites.' }),
    ] },
    { id: 'app', title: 'App', desc: 'How the dashboard behaves.', advanced: [{ id: 'settings-ui', title: 'Dashboard' }], items: [
      item('app-theme', 'Appearance', { kind: 'theme' }),
      item('app-orb', 'Floating button', { kind: 'switch', on: false, locked: '', keys: ['desktop.floating_orb'], desktop: true }),
      item('app-workspaces', 'Separate memories', { kind: 'link', href: '#workspaces', label: 'Manage workspaces' }),
      item('devices-pair', 'Pair another device', { kind: 'link', href: '/sync/', label: 'Open Devices' }),
    ] },
  ];
}

async function settings(page: Page) {
  const state = groups();
  const writes: { id: string; body: unknown }[] = [];
  await page.route('**/api/settings/simple', route => route.fulfill({ json: { groups: state } }));
  await page.route('**/api/settings/simple/*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!;
    const body = route.request().postDataJSON();
    writes.push({ id, body });
    for (const group of state) for (const entry of group.items) {
      if (entry.id !== id) continue;
      if ('on' in body) entry.on = body.on;
      if ('option' in body) entry.selected = body.option;
    }
    await route.fulfill({ json: { ok: true, keys: ['chat.provider'], groups: state } });
  });
  return writes;
}

test('the simple dashboard shows a short list of choices and saves each as it is made', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  const writes = await settings(page);
  await page.goto('/settings/');
  await expect(page.getByRole('heading', { name: 'Settings', level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2 })).toHaveText(['Assistant', 'Recordings', 'Privacy', 'App']);
  // No technical names, no filter, no section list.
  await expect(page.getByText('chat.provider')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: 'Filter settings' })).toHaveCount(0);

  const runs = page.getByRole('radiogroup', { name: 'Where the assistant runs' });
  await expect(runs.getByRole('radio', { name: /On this computer/ })).toBeChecked();
  // A provider without its key says why and cannot be picked.
  await expect(runs.getByRole('radio', { name: /Anthropic/ })).toBeDisabled();
  await expect(runs).toContainText('No key found on this computer (ANTHROPIC_API_KEY)');

  const voices = page.getByRole('switch', { name: 'Tell voices apart' });
  await expect(voices).toBeChecked();
  await voices.click();
  await expect(voices).not.toBeChecked();
  expect(writes).toEqual([{ id: 'recordings-voices', body: { on: false } }]);
});

test('values set in the advanced settings show as custom until an option is picked', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  const writes = await settings(page);
  await page.goto('/settings/');
  const depth = page.getByRole('radiogroup', { name: 'How far it looks before answering' });
  await expect(depth.getByRole('radio', { checked: true })).toHaveCount(0);
  await expect(page.getByText('Custom: this was set in the advanced settings')).toBeVisible();
  await depth.getByRole('radio', { name: /Thorough/ }).click();
  await expect(depth.getByRole('radio', { name: /Thorough/ })).toBeChecked();
  await expect(page.getByText('Custom: this was set in the advanced settings')).toHaveCount(0);
  expect(writes).toEqual([{ id: 'assistant-depth', body: { option: 2 } }]);
});

test('a setting an environment variable holds is read-only and says so', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await settings(page);
  await page.goto('/settings/');
  await expect(page.getByRole('switch', { name: 'Language spoken' })).toBeDisabled();
  await expect(page.getByText('by the environment variable IMPRINT_AUDIO_LANGUAGE')).toBeVisible();
});

test('the report, the theme and what only the desktop app uses', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await settings(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/settings/');
  await expect(page.getByRole('list', { name: 'What leaves this computer' })).toContainText('Nothing. Chat, labels and recaps all run on this computer.');
  // A browser leaves out the desktop app's floating button.
  await expect(page.getByRole('switch', { name: 'Floating button' })).toHaveCount(0);
  await page.getByRole('radiogroup', { name: 'Appearance' }).getByRole('radio', { name: 'Dark', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true);
  await expect(page.getByRole('link', { name: 'Open Devices' })).toHaveAttribute('href', '/sync/');
});

test('a section link opens the advanced settings without leaving the simple dashboard', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await settings(page);
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'chat.temperature', value: 0.3, default: 0.3, type: 'float', source: 'default', env: 'IMPRINT_CHAT_TEMPERATURE', desc: 'Chat sampling temperature', choices: [] },
  ] } }));
  await page.goto('/settings/');
  await page.getByRole('main').getByRole('link', { name: 'Chat', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Temperature', exact: true })).toBeVisible();
  await expect(page.getByRole('note').filter({ hasText: 'These are the advanced settings' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Dashboard: Simple' })).toBeVisible();
  await page.getByRole('link', { name: 'Back to the short list' }).click();
  await expect(page.getByRole('heading', { name: 'Assistant', level: 2 })).toBeVisible();

  // Workspaces are a section of the same page.
  await page.getByRole('link', { name: 'Manage workspaces' }).click();
  await expect(page.getByRole('note').filter({ hasText: 'These are the advanced settings' })).toBeVisible();
});

test('All settings switches to the advanced dashboard', async ({ page }) => {
  const writes = await base(page, { mode: 'simple', tutorials: false });
  await settings(page);
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [] } }));
  await page.goto('/settings/');
  await page.getByRole('button', { name: 'All settings (Advanced)' }).click();
  await expect(page.getByRole('textbox', { name: 'Filter settings' })).toBeVisible();
  expect(writes).toEqual([{ key: 'ui.mode', value: 'advanced' }]);
});

test('the desktop app also offers what only it uses', async ({ page }) => {
  await base(page, { mode: 'simple', tutorials: false });
  await settings(page);
  await page.addInitScript(() => {
    Object.assign(window, { electronAPI: { isElectron: true, platform: 'linux', close() {}, quit() {}, minimize() {}, maximize() {}, openCompanion() {}, closeCompanion() {}, pinCompanion() {} } });
  });
  await page.goto('/settings/');
  await expect(page.getByRole('switch', { name: 'Floating button' })).toBeVisible();
});
