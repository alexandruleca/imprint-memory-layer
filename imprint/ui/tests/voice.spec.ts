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

test('a desktop without WebGL still projects the real 3D particle head onto transparent canvas', async ({ page }) => {
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
