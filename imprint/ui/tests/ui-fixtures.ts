import type { Page } from '@playwright/test';

/** The dashboard's mode and tutorials as the API serves and stores them
 * (`/api/ui`, `ui.mode`, `ui.tutorials`). Without this route the catch-all
 * `{}` answer names no mode, and the dashboard stays advanced without
 * tutorials, which is what every other spec runs in. */
export type Ui = { mode: 'simple' | 'advanced'; tutorials: boolean };

export async function mockUi(page: Page, ui: Ui) {
  const writes: { key: string; value: unknown }[] = [];
  await page.route('**/api/ui', route => route.fulfill({ json: ui }));
  await page.route('**/api/config/ui.*', async route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!;
    const { value } = route.request().postDataJSON();
    writes.push({ key, value });
    if (key === 'ui.mode') ui.mode = value;
    if (key === 'ui.tutorials') ui.tutorials = value === true || value === 'true';
    await route.fulfill({ json: { ok: true, value } });
  });
  return writes;
}

/** Every request answered with `{}`, one workspace, and the given mode. */
export async function base(page: Page, ui?: Ui) {
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  return ui ? mockUi(page, ui) : [];
}
