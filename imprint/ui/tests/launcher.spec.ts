import { test, expect, type Page } from '@playwright/test';

// The orb's ring of actions and its recording panels (ADR 0135), driven by a
// stand-in for the desktop bridge. The main process places the window and
// relays the dashboard's recorder; here the page only draws and asks.

type Recorder = { phase: string; id: string; title: string; project: string; startedAt: number; others: boolean; error: string };
const idle: Recorder = { phase: 'idle', id: '', title: '', project: '', startedAt: 0, others: false, error: '' };

// A ring opening to the right of an orb at (40, 85), as companion.js lays it out.
const ring = (over: Record<string, unknown> = {}) => ({
  kind: 'ring', side: 1, tilt: 'level', bounds: { x: 100, y: 100, width: 290, height: 173 }, orb: { x: 40, y: 85 }, radius: 68,
  satellites: [
    { angle: -45, x: 88, y: 37, slot: { x: 108, y: 5, width: 150, height: 26 } },
    { angle: 0, x: 108, y: 85, slot: { x: 136, y: 72, width: 150, height: 26 } },
    { angle: 45, x: 88, y: 133, slot: { x: 108, y: 136, width: 168, height: 32 } },
  ],
  seq: 1, focus: null, echo: false, recorder: idle, ...over,
});
const panel = (kind: string, over: Record<string, unknown> = {}) => ({
  kind, side: 1, tilt: 'level', bounds: { x: 100, y: 100, width: 344, height: 402 }, orb: { x: -28, y: 36 }, origin: 'left top',
  seq: 2, focus: null, echo: false, recorder: idle, ...over,
});

async function open(page: Page, layout: Record<string, unknown>, recorder: Recorder = idle) {
  await page.addInitScript(({ layout, recorder }) => {
    const calls: unknown[] = [];
    const listeners: { layout?: (value: unknown) => void; recorder?: (value: unknown) => void } = {};
    Object.assign(window, {
      haloCalls: calls,
      emit: (name: 'layout' | 'recorder', value: unknown) => listeners[name]?.(value),
      electronAPI: {
        isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {},
        openCompanion() {}, closeCompanion() {}, pinCompanion() {},
        getHaloLayout: async () => layout,
        onHaloLayout: (callback: (value: unknown) => void) => { listeners.layout = callback; return () => {}; },
        haloReady: (seq: number) => calls.push(['ready', seq]),
        haloAction: (action: string) => calls.push(['action', action]),
        haloSize: (height: number) => calls.push(['size', height]),
        getRecorder: async () => recorder,
        onRecorder: (callback: (value: unknown) => void) => { listeners.recorder = callback; return () => {}; },
        startRecording: (options: unknown) => calls.push(['start', options]),
        stopRecording: () => calls.push(['stop']),
      },
    });
  }, { layout, recorder });
  await page.route('**/api/**', route => {
    const url = route.request().url();
    if (url.endsWith('/api/recordings')) return route.fulfill({ json: { recordings: [], default_project: 'meetings' } });
    if (url.includes('/api/stats')) return route.fulfill({ json: { projects: [['meetings', 3], ['imprint', 40]], total_memories: 43 } });
    if (url.endsWith('/api/ping')) return route.fulfill({ json: { ok: true } });
    return route.fulfill({ json: {} });
  });
  const bounds = layout.bounds as { width: number; height: number };
  await page.setViewportSize({ width: bounds.width, height: bounds.height });
  await page.goto('/launcher/halo/');
}
const calls = (page: Page) => page.evaluate(() => (window as unknown as { haloCalls: unknown[] }).haloCalls.splice(0));
const actions = async (page: Page) => (await calls(page)).filter(call => (call as unknown[])[0] === 'action').map(call => (call as unknown[])[1]);

test('the ring fans Dashboard, Echo and Record around the orb, says it is drawn, and acts', async ({ page }) => {
  await open(page, ring());
  const menu = page.getByRole('menu', { name: 'Imprint actions' });
  await expect(menu.getByRole('menuitem')).toHaveCount(3);
  await expect.poll(async () => (await calls(page)).some(call => (call as unknown[])[0] === 'ready' && (call as unknown[])[1] === 1)).toBe(true);
  for (const [name, x, y] of [['Open dashboard', 88, 37], ['Show Echo', 108, 85], ['New recording', 88, 133]] as const) {
    // Once each has travelled out from the orb.
    await expect.poll(async () => {
      const box = (await menu.getByRole('menuitem', { name }).boundingBox())!;
      return [Math.round(box.x + box.width / 2), Math.round(box.y + box.height / 2)];
    }).toEqual([x, y]);
  }
  await expect(page.getByRole('link', { name: 'Skip to content' })).toHaveCount(0);
  // A name shows on hover, not before.
  await expect(page.getByText('Show Echo', { exact: true })).toHaveCount(0);
  await menu.getByRole('menuitem', { name: 'Show Echo' }).hover();
  await expect(page.getByText('Show Echo', { exact: true })).toBeVisible();
  await menu.getByRole('menuitem', { name: 'Show Echo' }).click();
  await menu.getByRole('menuitem', { name: 'New recording' }).click();
  await page.keyboard.press('Escape');
  expect(await actions(page)).toEqual(['echo', 'record', 'close']);
  // Closing plays out, then main hides the window.
  await page.evaluate(() => (window as unknown as { emit: (n: string, v: unknown) => void }).emit('layout', null));
  await expect(page.locator('.imprint-halo[data-closing]')).toBeAttached();
});

test('a keyboard-opened ring takes focus and arrows move through it, wrapping', async ({ page }) => {
  await open(page, ring({ focus: 'first', echo: true }));
  const menu = page.getByRole('menu', { name: 'Imprint actions' });
  await expect(menu.getByRole('menuitem', { name: 'Open dashboard' })).toBeFocused();
  await expect(menu.getByRole('menuitem', { name: 'Collapse Echo' })).toHaveAttribute('data-on', 'true');
  await page.keyboard.press('ArrowDown'); await expect(menu.getByRole('menuitem', { name: 'Collapse Echo' })).toBeFocused();
  await page.keyboard.press('End'); await expect(menu.getByRole('menuitem', { name: 'New recording' })).toBeFocused();
  await page.keyboard.press('ArrowDown'); await expect(menu.getByRole('menuitem', { name: 'Open dashboard' })).toBeFocused();
  await page.keyboard.press('Enter');
  expect(await actions(page)).toEqual(['dashboard']);
});

test('while recording, Record becomes Stop and the capsule shows the time and opens the details', async ({ page }) => {
  const recording = { ...idle, phase: 'recording', id: 'r1', title: 'Weekly sync', project: 'meetings', startedAt: Date.now() - 64_000, others: true };
  await open(page, ring({ focus: 'first', recorder: recording }), recording);
  const menu = page.getByRole('menu', { name: 'Imprint actions' });
  const capsule = menu.getByRole('menuitem', { name: /^Recording details, 1 minute 4 seconds|^Recording details, 1 minute 5 seconds/ });
  await expect(capsule).toBeFocused();
  await expect(capsule).toContainText(/01:0[45]/);
  const stop = menu.getByRole('menuitem', { name: 'Stop recording and transcribe' });
  await stop.focus();
  await expect(capsule).toContainText('Stop and transcribe');
  await page.evaluate(() => (window as unknown as { emit: (n: string, v: unknown) => void }).emit('recorder', { levels: [0.5, 0.25] }));
  await stop.press('Enter');
  await capsule.click();
  expect(await actions(page)).toEqual(['stop', 'details']);
});

test('a recording starts only after the consent box, with the remembered project, and errors offer a way on', async ({ page }) => {
  await open(page, panel('record'));
  const dialog = page.getByRole('dialog', { name: 'New recording' });
  const consent = dialog.getByRole('checkbox', { name: /Everyone on this call knows/ });
  await expect(consent).toBeFocused();
  await expect(dialog.getByRole('combobox', { name: 'Project' })).toHaveValue('meetings');
  await expect(dialog.getByRole('textbox', { name: 'Title' })).toHaveAttribute('placeholder', /^Meeting \d{4}-\d\d-\d\d \d\d:\d\d$/);
  const start = dialog.getByRole('button', { name: 'Start recording' });
  await expect(start).toBeDisabled();
  await expect.poll(async () => (await calls(page)).some(call => (call as unknown[])[0] === 'size')).toBe(true);
  await consent.press('Space');
  await expect(start).toBeEnabled();
  await dialog.getByRole('switch', { name: /Include what this computer plays/ }).click();
  await dialog.getByRole('combobox', { name: 'Project' }).fill('imprint');
  await dialog.getByRole('textbox', { name: 'Title' }).fill('Design review');
  await start.click();
  const started = (await calls(page)).filter(call => (call as unknown[])[0] === 'start');
  expect(started).toEqual([['start', { title: 'Design review', project: 'imprint', others: false, consent: true }]]);
  // The dashboard reports a failure: the panel stays, says why and offers to retry.
  const emit = (value: unknown) => page.evaluate(v => (window as unknown as { emit: (n: string, v: unknown) => void }).emit('recorder', v), value);
  await emit({ state: { ...idle, phase: 'starting' } });
  await expect(dialog.getByRole('button', { name: 'Starting…' })).toBeDisabled();
  await emit({ state: { ...idle, error: 'The microphone or the computer\'s audio was not allowed.' } });
  await expect(dialog.getByRole('alert')).toContainText('was not allowed');
  await dialog.getByRole('button', { name: 'Microphone settings' }).click();
  await dialog.getByRole('button', { name: 'Try again' }).click();
  const after = await calls(page);
  expect(after.filter(call => (call as unknown[])[0] === 'action')).toEqual([['action', 'microphone-settings']]);
  expect(after.filter(call => (call as unknown[])[0] === 'start')).toHaveLength(1);
  // The project and computer-audio choice are remembered on this device; consent is not.
  const remembered = await page.evaluate(() => localStorage.getItem('imprint.launcher.recording'));
  expect(JSON.parse(remembered!)).toEqual({ project: 'imprint', others: false });
});

test('the live panel shows the recording and stops it', async ({ page }) => {
  const recording = { ...idle, phase: 'recording', id: 'r1', title: 'Weekly sync', project: 'meetings', startedAt: Date.now() - 5_000, others: false };
  await open(page, panel('live', { bounds: { x: 0, y: 0, width: 344, height: 256 } }), recording);
  const dialog = page.getByRole('dialog', { name: 'Recording' });
  await expect(dialog).toContainText('Weekly sync');
  await expect(dialog).toContainText('meetings');
  await expect(dialog).toContainText('Only your microphone is being recorded.');
  await expect(dialog.getByRole('button', { name: 'Stop and transcribe' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Stop and transcribe' }).click();
  await dialog.getByRole('button', { name: 'Collapse into the Imprint button' }).click();
  const all = await calls(page);
  expect(all.filter(call => ['stop', 'action'].includes((call as unknown[])[0] as string))).toEqual([['stop'], ['action', 'close']]);
});

test('the orb shows a recording: its timer, a red rim, and a name that says so', async ({ page }) => {
  await page.addInitScript(() => {
    const recorder = { phase: 'recording' as const, id: 'r1', title: 'Weekly', project: 'meetings', startedAt: Date.now() - 125_000, others: true, error: '' };
    window.electronAPI = { isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {},
      getRecorder: async () => recorder, onRecorder: () => () => {},
      getLauncherState: async () => ({ ring: false, panel: null, echo: true, echoSide: -1, recorder, activity: null }),
      onLauncherState: () => () => {} };
  });
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.setViewportSize({ width: 72, height: 72 });
  await page.goto('/launcher/');
  const orb = page.getByRole('button', { name: /^Imprint\. Recording, 2 minutes$/ });
  await expect(orb).toContainText(/02:0[5-7]/);
  await expect(page.locator('.orb-rim-recording')).toBeAttached();
  await expect(page.locator('.orb-rim-notch')).toBeAttached();
});
