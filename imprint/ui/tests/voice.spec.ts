import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
test.use({ launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] } });

async function prepare(page: Page) {
  const controls: { type?: string; id?: number; text?: string; audio?: string }[] = [];
  let socket: WebSocketRoute;
  await page.route('**/api/**', route => {
    const url = route.request().url();
    if (url.endsWith('/voice/status')) return route.fulfill({ json: { installed: true, configured: true, supported: true, chat_provider: 'local', workspace: 'default' } });
    if (url.endsWith('/voice/sessions')) return route.fulfill({ json: { token: 'fixture-capability', workspace: 'default' } });
    if (url.endsWith('/workspaces')) return route.fulfill({ json: { active: 'default', known: ['default'] } });
    return route.fulfill({ json: { ok: true, version: 'test', jobs: [], requests: [] } });
  });
  await page.routeWebSocket('**/api/voice/stream', route => {
    socket = route;
    route.onMessage(message => {
      const event = JSON.parse(message as string);
      controls.push(event);
      if (event.token) route.send(JSON.stringify({ type: 'ready', workspace: 'default', engine: 'chat' }));
    });
  });
  return { controls, send: (event: object) => socket.send(JSON.stringify(event)) };
}

test('Echo loads its real 3D asset; microphone is off until explicitly enabled', async ({ page }) => {
  await prepare(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/voice/');
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await expect(page.getByRole('button', { name: 'Push to talk', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('status').filter({ hasText: 'Microphone off' })).toBeVisible();
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Type a question' })).toBeEnabled();
  expect(errors).toEqual([]);
});

test('a desktop without WebGL still projects the textured 3D anime head onto transparent canvas', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(this: HTMLCanvasElement, type: string, ...args: unknown[]) {
      if (type === 'webgl' || type === 'webgl2') return null;
      return Reflect.apply(original, this, [type, ...args]);
    } as typeof original;
  });
  await page.goto('/companion/');
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-renderer', 'canvas-3d');
  await expect(page.locator('.voice-compact')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect.poll(() => page.locator('.echo-canvas canvas').evaluate(element => {
    const canvas = element as HTMLCanvasElement;
    const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
    let visible = 0;
    for (let index = 3; index < data.length; index += 4) if (data[index]) visible++;
    return visible;
  })).toBeGreaterThan(500);
  expect(await page.locator('.echo-canvas canvas').evaluate(element =>
    (element as HTMLCanvasElement).getContext('2d')!.getImageData(0, 0, 1, 1).data[3])).toBe(0);
});

test('interrupt flushes playback and ignores stale response chunks; stop releases connection', async ({ page }) => {
  const server = await prepare(page);
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  const input = page.getByRole('textbox', { name: 'Type a question' });
  await input.fill('Hello Echo');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect.poll(() => server.controls.find(c => c.type === 'turn')?.text).toBe('Hello Echo');
  server.send({ type: 'text', id: 1, text: 'Hello back.' });
  server.send({ type: 'audio', id: 1, format: 'pcm16', sample_rate: 24000, data: Buffer.alloc(24000 * 2 * 3, 4).toString('base64') });
  await expect(page.locator('.voice-surface')).toHaveAttribute('data-phase', 'speaking');
  await page.getByRole('button', { name: 'Interrupt reply' }).click();
  await expect.poll(() => server.controls.some(c => c.type === 'cancel')).toBe(true);
  server.send({ type: 'text', id: 1, text: 'STALE' });
  server.send({ type: 'audio', id: 1, format: 'pcm16', sample_rate: 24000, data: 'AAAAAA==' });
  await expect(page.locator('.voice-surface')).toHaveAttribute('data-phase', 'ready');
  await expect(page.locator('.voice-transcript')).not.toContainText('STALE');
  await page.getByRole('button', { name: 'Stop conversation and microphone' }).click();
  await expect(page.getByRole('button', { name: 'Start conversation' })).toBeVisible();
  await expect(input).toBeDisabled();
});

test('compact companion hides dashboard chrome and exposes hands-free controls', async ({ page }) => {
  await prepare(page);
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await expect(page.getByRole('link', { name: 'Dashboard', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Close companion' })).toBeVisible();
  await page.getByRole('button', { name: 'Hands-free', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Hands-free', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('slider', { name: 'Speech detection threshold' })).toBeVisible();
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await expect(page.locator('.voice-compact')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('.voice-compact')).toHaveCSS('background-image', 'none');
  await expect(page.locator('.voice-compact')).toHaveCSS('border-top-width', '0px');
});

test('dashboard Floating Echo switch reflects native close and stops dashboard voice when showing', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    let state = { open: false, pinned: false };
    const listeners = new Set<(state: { open: boolean; pinned: boolean }) => void>();
    window.electronAPI = { isElectron: true, platform: 'win32', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {},
      getCompanionState: async () => state,
      setCompanionVisible: async open => { state = { ...state, open }; listeners.forEach(listener => listener(state)); return state; },
      onCompanionState: listener => { listeners.add(listener); return () => listeners.delete(listener); } };
    window.addEventListener('test:companion-close', () => {
      state = { open: false, pinned: false }; listeners.forEach(listener => listener(state));
    });
  });
  await page.goto('/voice/');
  const toggle = page.getByRole('switch', { name: 'Floating Echo' });
  await expect(toggle).not.toBeChecked();
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
  await toggle.click();
  await expect(toggle).toBeChecked();
  await expect(page.getByRole('button', { name: 'Start conversation' })).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event('test:companion-close')));
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect(toggle).not.toBeChecked();
});

test('push-to-talk captures through AudioWorklet and sends a bounded WAV on release', async ({ page }) => {
  const server = await prepare(page);
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  const hold = page.getByRole('button', { name: 'Hold to talk', exact: true });
  await expect(hold).toBeVisible();
  await hold.focus();
  await page.keyboard.down('Space');
  await expect(hold).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(400); // Capture real worklet frames from Chromium's synthetic mic.
  await page.keyboard.up('Space');
  await expect.poll(() => server.controls.find(c => c.type === 'turn')?.audio).toBeTruthy();
  const wav = Buffer.from(server.controls.find(c => c.type === 'turn')!.audio!, 'base64');
  expect(wav.toString('ascii', 0, 4)).toBe('RIFF');
  expect(wav.readUInt32LE(24)).toBe(24000);
  expect(wav.length).toBeGreaterThan(4000);
  expect(wav.length).toBeLessThan(1440044);
  await page.getByRole('button', { name: 'Stop conversation and microphone' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Microphone off' })).toBeVisible();
});

test('first-time chat setup shows real download progress in Voice and ignores late progress after interruption', async ({ page }) => {
  const server = await prepare(page);
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('textbox', { name: 'Type a question' }).fill('Hello');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect.poll(() => server.controls.some(c => c.type === 'turn')).toBe(true);
  server.send({ type: 'provision', id: 1, stage: 'install' });
  await expect(page.getByRole('heading', { name: 'Installing local chat support' })).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Local chat model preparation' })).not.toHaveAttribute('aria-valuenow');
  server.send({ type: 'provision', id: 1, stage: 'download', downloaded: 500000000, total: 1000000000 });
  await expect(page.getByRole('progressbar', { name: 'Local chat model preparation' })).toHaveAttribute('aria-valuenow', '50');
  await expect(page.getByText('500 MB of 1.00 GB')).toBeVisible();
  await expect(page.getByText(/Chat setup continues if you stop voice/)).toBeVisible();
  await page.getByRole('button', { name: 'Interrupt reply' }).click();
  server.send({ type: 'provision', id: 1, stage: 'download', downloaded: 900000000, total: 1000000000 });
  await expect(page.getByRole('region', { name: 'Local chat preparation' })).toHaveCount(0);
});

test('compact chat download keeps cancellation controls inside the viewport', async ({ page }) => {
  const server = await prepare(page); await page.setViewportSize({ width: 320, height: 420 });
  await page.goto('/companion/'); await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('textbox', { name: 'Type a question' }).fill('Hello');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect.poll(() => server.controls.some(c => c.type === 'turn')).toBe(true);
  server.send({ type: 'provision', id: 1, stage: 'download', downloaded: 1024, total: 4096 });
  await expect(page.getByRole('region', { name: 'Local chat preparation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Interrupt reply' })).toBeVisible();
  expect(await page.locator('.voice-control-area').evaluate(el => el.getBoundingClientRect().bottom)).toBeLessThanOrEqual(420);
});

test('floating launcher distinguishes dragging from opening and supports keyboard after a drag', async ({ page }) => {
  await page.addInitScript(() => {
    const calls: string[] = []; Object.assign(window, { launcherCalls: calls });
    window.electronAPI = { isElectron: true, platform: 'win32', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {},
      openDashboard() { calls.push('open'); }, beginLauncherDrag() { calls.push('start'); },
      moveLauncherDrag() { calls.push('move'); }, endLauncherDrag() { calls.push('end'); } };
  });
  await prepare(page);
  await page.route('**/api/ping', route => route.fulfill({ status: 503, json: {} }));
  await page.setViewportSize({ width: 56, height: 56 }); await page.goto('/launcher/');
  await page.locator('.imprint-launcher[data-ready=true]').waitFor();
  const button = page.getByRole('button', { name: 'Open Imprint' }); await button.waitFor();
  await expect(page.getByRole('link', { name: 'Skip to content' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Retry connection' })).toHaveCount(0);
  await page.keyboard.press('Tab'); await expect(button).toBeFocused();
  await page.mouse.move(20,20); await page.mouse.down(); await page.mouse.move(40,35); await page.mouse.up();
  const calls = () => page.evaluate(() => (window as unknown as { launcherCalls: string[] }).launcherCalls);
  expect(await calls()).toContain('move'); expect(await calls()).not.toContain('open');
  await button.press('Enter'); expect(await calls()).toContain('open');
});

test("Echo's look is picked from previews, saved, and loads its own model", async ({ page }) => {
  await prepare(page);
  const state = { value: 'echo', puts: [] as unknown[] };
  const choices = [
    { value: 'echo', label: 'Echo', detail: 'Brown bob, warm hairband with ribbon bows', unavailable: '' },
    { value: 'iris', label: 'Iris', detail: 'Indigo straight cut, pale lavender eyes', unavailable: '' },
  ];
  await page.route('**/api/config', route => route.fulfill({ json: { settings: [
    { key: 'voice.avatar', value: state.value, source: 'default', default: 'echo', type: 'str', env: 'IMPRINT_VOICE_AVATAR',
      desc: 'How Echo looks; change it anytime', choices, custom: false },
  ] } }));
  await page.route('**/api/config/voice.avatar', async route => {
    const body = route.request().postDataJSON();
    state.puts.push(body);
    state.value = body.value;
    await route.fulfill({ json: { ok: true, value: body.value } });
  });
  const models: string[] = [];
  page.on('request', request => { if (/\/models\/imprint-\w+\.glb$/.test(request.url())) models.push(request.url().split('/').pop()!); });

  await page.goto('/voice/');
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  expect(models).toEqual(['imprint-echo.glb']);
  const looks = page.getByRole('group', { name: "Echo's look" });
  await expect(looks.getByRole('button', { name: /^Echo:/ })).toHaveAttribute('aria-pressed', 'true');

  await looks.getByRole('button', { name: /^Iris:/ }).click();
  await expect.poll(() => state.puts).toEqual([{ value: 'iris' }]);
  await expect.poll(() => models).toEqual(['imprint-echo.glb', 'imprint-iris.glb']);
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await expect(looks.getByRole('button', { name: /^Iris:/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('link', { name: 'Download 3D model' })).toHaveAttribute('href', '/models/imprint-iris.glb');
});
