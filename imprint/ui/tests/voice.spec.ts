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
  await expect(input).toBeEnabled(); // Typing again starts a new conversation.
});

test('compact companion hides dashboard chrome and exposes hands-free controls', async ({ page }) => {
  await prepare(page);
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await expect(page.getByRole('link', { name: 'Dashboard', exact: true })).toHaveCount(0);
  // Only talking stays on screen; everything else waits behind Companion settings.
  await expect(page.getByRole('button', { name: 'Close companion' })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Voice reasoning model' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Hands-free', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Hands-free', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await expect(page.getByRole('button', { name: 'Close companion' })).toBeVisible();
  await expect(page.getByRole('slider', { name: 'Speech detection threshold' })).toBeVisible();
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await expect(page.locator('.voice-compact')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('.voice-compact')).toHaveCSS('background-image', 'none');
  await expect(page.locator('.voice-compact')).toHaveCSS('border-top-width', '0px');
});

// The desktop bridge as the floating companion sees it; the main process owns pinning.
async function companionBridge(page: Page) {
  await page.addInitScript(() => {
    const calls: unknown[][] = [];
    let state = { open: true, pinned: false, shortcutAvailable: true };
    const listeners = new Set<(value: typeof state) => void>();
    const publish = (next: Partial<typeof state>) => { state = { ...state, ...next }; listeners.forEach(listener => listener(state)); };
    Object.assign(window, { companionCalls: calls, publishCompanion: publish });
    window.electronAPI = { isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() { calls.push(['close']); },
      pinCompanion(pinned) { calls.push(['pin', pinned]); publish({ pinned }); },
      beginCompanionDrag(point) { calls.push(['drag-start', point.x, point.y]); },
      moveCompanionDrag(point) { calls.push(['drag-move', point.x, point.y]); },
      endCompanionDrag() { calls.push(['drag-end']); },
      getCompanionState: async () => state,
      onCompanionState: listener => { listeners.add(listener); return () => listeners.delete(listener); } };
  });
  return { calls: () => page.evaluate(() => (window as unknown as { companionCalls: unknown[][] }).companionCalls.map(call => call[0])) };
}

test('companion: a typed question starts the conversation; controls never drag the window', async ({ page }) => {
  const server = await prepare(page);
  const bridge = await companionBridge(page);
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await expect(page.getByRole('button', { name: 'Start conversation' })).toBeVisible();
  await page.locator('.voice-privacy').click();
  await page.getByRole('button', { name: 'Companion settings' }).click();
  const question = page.getByRole('textbox', { name: 'Type a question' });
  await question.click();
  await page.keyboard.type('What did I decide about caching?');
  await expect(question).toHaveValue('What did I decide about caching?');
  await page.locator('.voice-text').click({ position: { x: 4, y: 16 } }); // the box's padding beside the field
  await page.getByText('Voice reasoning').click();
  await page.getByText('Companion settings', { exact: true }).click();
  expect(await bridge.calls()).not.toContain('drag-start');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect.poll(() => server.controls.find(control => control.type === 'turn')?.text).toBe('What did I decide about caching?');
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
  // Every control in the panel is outside the native drag region.
  const regions = await page.getByRole('dialog', { name: 'Companion settings' }).locator('select,input,button,label')
    .evaluateAll(elements => elements.map(element => getComputedStyle(element).getPropertyValue('app-region')));
  expect(regions.length).toBeGreaterThan(3);
  expect(regions.every(value => value === 'no-drag')).toBe(true);
});

test('companion: settings close with Escape; while open, a press on the head closes them instead of dragging', async ({ page }) => {
  await prepare(page);
  const bridge = await companionBridge(page);
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  const dialog = page.getByRole('dialog', { name: 'Companion settings' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await expect(dialog).toBeVisible();
  // A point on the head that the panel does not cover.
  const box = (await page.locator('.voice-stage').boundingBox())!, panel = (await dialog.boundingBox())!;
  const head = { x: box.x + 6, y: Math.max(box.y + 6, Math.min(box.y + box.height - 6, panel.y + panel.height + 6)) };
  expect(head.x < panel.x || head.y > panel.y + panel.height).toBe(true);
  await page.mouse.click(head.x, head.y);
  await expect(dialog).toHaveCount(0);
  expect(await bridge.calls()).not.toContain('drag-start');
  await page.mouse.click(head.x, head.y);
  expect(await bridge.calls()).toEqual(['drag-start', 'drag-end']);
});

test('companion: the head drags the window, and a release it missed ends the drag', async ({ page }) => {
  await prepare(page);
  const bridge = await companionBridge(page);
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  const head = page.locator('.voice-stage');
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  const box = (await head.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 20);
  await page.mouse.up();
  expect(await bridge.calls()).toEqual(['drag-start', 'drag-move', 'drag-end']);
  // The OS can swallow a release, for example when the window moves under it.
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await head.dispatchEvent('pointermove', { pointerId: 1, pointerType: 'mouse', isPrimary: true, buttons: 0, screenX: 5, screenY: 5 });
  await page.mouse.move(box.x + 10, box.y + 10);
  const calls = await bridge.calls();
  expect(calls.slice(3)).toEqual(['drag-start', 'drag-end']);
  await page.mouse.up();
});

test('companion: pin shows the on-top state the main process reports', async ({ page }) => {
  await prepare(page);
  const bridge = await companionBridge(page);
  await page.goto('/companion/');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  const pin = page.getByRole('button', { name: 'Keep companion on top' });
  const unpinnedColor = await pin.evaluate(element => getComputedStyle(element).color);
  await pin.click();
  const unpin = page.getByRole('button', { name: 'Unpin companion' });
  await expect(unpin).toHaveAttribute('aria-pressed', 'true');
  expect(await unpin.evaluate(element => getComputedStyle(element).color)).not.toBe(unpinnedColor);
  expect(await bridge.calls()).toEqual(['pin']);
  // Unpinned elsewhere (the main process is the owner): the button follows.
  await page.evaluate(() => (window as unknown as { publishCompanion: (state: { pinned: boolean }) => void }).publishCompanion({ pinned: false }));
  await expect(page.getByRole('button', { name: 'Keep companion on top' })).toHaveAttribute('aria-pressed', 'false');
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

test('a microphone that fails to open names the failure; only a refusal mentions permission', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    window.electronAPI = { isElectron: true, platform: 'win32', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {} };
    const failures = [['Could not start audio source', 'NotReadableError'], ['Requested device not found', 'NotFoundError'],
      ['Permission denied', 'NotAllowedError']];
    navigator.mediaDevices.getUserMedia = async () => { const [message, name] = failures.shift()!; throw new DOMException(message, name); };
  });
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  const status = page.locator('.voice-privacy');
  const enable = page.getByRole('button', { name: 'Enable microphone' });
  await enable.click();
  await expect(status).toHaveText("The microphone couldn't start. Another app may be using it; close that app or choose another input in sound settings. (NotReadableError: Could not start audio source)");
  await enable.click();
  await expect(status).toHaveText('No microphone found. Connect or turn on a microphone, then try again. (NotFoundError: Requested device not found)');
  await enable.click();
  await expect(status).toHaveText('Microphone permission denied. In Windows Settings → Privacy & security → Microphone, turn on Microphone access and Let desktop apps access your microphone, then try again.');
  // A mode change rewrites the caption, not the explanation.
  await page.getByRole('button', { name: 'Hands-free', exact: true }).click();
  await expect(status).toContainText('Microphone permission denied');
});

test('a capture worklet that fails to load is reported as audio processing, in full in the companion', async ({ page }) => {
  await prepare(page);
  // Worklet module fetches bypass page routing; reject the way Chromium does when the fetch fails.
  await page.addInitScript(() => {
    AudioWorklet.prototype.addModule = async () => { throw new DOMException("Unable to load a worklet's module.", 'AbortError'); };
  });
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  const status = page.locator('.voice-privacy');
  await expect(status).toHaveText("Microphone audio processing failed to load. (AbortError: Unable to load a worklet's module.)");
  await expect(status).not.toContainText('permission');
  expect(await status.evaluate(element => element.scrollHeight <= element.clientHeight)).toBe(true);
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
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
  await page.getByRole('button', { name: 'Companion settings' }).click();
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
    { value: 'echo', label: 'Echo', detail: 'Holographic core, rings that turn and pulse as it speaks', unavailable: '' },
    { value: 'iris', label: 'Iris', detail: 'Indigo straight cut, pale lavender eyes', unavailable: '' },
    { value: 'kai', label: 'Kai', detail: 'Spiky orange hair, a determined frown', unavailable: '' },
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

  await looks.getByRole('button', { name: /^Kai:/ }).click();
  await expect.poll(() => state.puts).toEqual([{ value: 'kai' }]);
  await expect.poll(() => models).toEqual(['imprint-echo.glb', 'imprint-kai.glb']);
  await expect(page.locator('.echo-canvas')).toHaveAttribute('data-loaded', 'true');
  await expect(looks.getByRole('button', { name: /^Kai:/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('link', { name: 'Download 3D model' })).toHaveAttribute('href', '/models/imprint-kai.glb');
});

// The desktop bridge's OS consent check (ADR 0069), as each platform answers it.
for (const platform of ['darwin', 'win32', 'linux']) {
  test(`${platform}: an OS refusal stays visible with its own recovery, and a retry captures`, async ({ page }) => {
    await prepare(page);
    await page.addInitScript(platform => {
      let attempts = 0;
      Object.assign(window, { microphoneChecks: 0, microphoneSettingsOpened: 0 });
      window.electronAPI = { isElectron: true, platform, close() {}, quit() {}, minimize() {}, maximize() {},
        openCompanion() {}, closeCompanion() {}, pinCompanion() {},
        requestMicrophoneAccess: async () => {
          (window as unknown as { microphoneChecks: number }).microphoneChecks++;
          return { status: ++attempts === 1 ? 'denied' : platform === 'linux' ? 'unmanaged' : 'granted' };
        },
        openMicrophoneSettings: async () => { (window as unknown as { microphoneSettingsOpened: number }).microphoneSettingsOpened++; return true; } };
    }, platform);
    await page.goto('/voice/');
    await page.getByRole('button', { name: 'Start conversation' }).click();
    await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { microphoneChecks: number }).microphoneChecks)).toBe(0);
    await page.getByRole('button', { name: 'Enable microphone' }).click();
    const status = page.locator('.voice-privacy');
    await expect(status).toContainText('Microphone permission denied');
    if (platform === 'darwin') await expect(status).toContainText('then quit and reopen Imprint');
    if (platform === 'win32') await expect(status).toContainText('Let desktop apps access your microphone');
    if (platform === 'linux') await expect(status).toContainText('audio input');
    await page.getByRole('button', { name: 'Hands-free', exact: true }).click();
    await expect(status).toContainText('Microphone permission denied');
    if (platform !== 'linux') {
      await page.getByRole('button', { name: 'Open microphone settings' }).click();
      expect(await page.evaluate(() => (window as unknown as { microphoneSettingsOpened: number }).microphoneSettingsOpened)).toBe(1);
    } else await expect(page.getByRole('button', { name: 'Open microphone settings' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Enable microphone' }).click();
    await expect(page.getByRole('button', { name: 'Listening · interrupt' })).toBeVisible();
    await expect(status).toContainText('Mic on');
    await expect(page.getByRole('button', { name: 'Open microphone settings' })).toHaveCount(0);
  });
}

test('a system policy restriction or a failed consent check is not reported as a refusal', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    const answers = ['restricted', 'error', 'unknown'];
    window.electronAPI = { isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {}, openMicrophoneSettings: async () => true,
      requestMicrophoneAccess: async () => ({ status: answers.shift()! }) };
  });
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  const status = page.locator('.voice-privacy'), enable = page.getByRole('button', { name: 'Enable microphone' });
  await enable.click();
  await expect(status).toHaveText('Microphone access is restricted by a system policy. Ask your device administrator; you can still type.');
  await enable.click();
  await expect(status).toHaveText("Imprint couldn't check microphone permission. Quit and reopen Imprint, then try again. (The system reports microphone access as error)");
  await enable.click(); // macOS has already asked; anything but a grant cannot proceed
  await expect(status).toContainText('as unknown');
  await expect(page.getByRole('button', { name: 'Open microphone settings' })).toHaveCount(0);
});

test('a missing microphone stays visible in the companion without suggesting permission settings', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Requested device not found', 'NotFoundError'); };
  });
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  await expect(page.locator('.voice-privacy')).toContainText('No microphone found');
  await expect(page.getByRole('button', { name: 'Open microphone settings' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await expect(page.getByRole('textbox', { name: 'Type a question' })).toBeEnabled();
});

test('stopping while OS consent is pending never opens a microphone after consent resolves', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    Object.assign(window, { resolveConsent: null, captureCalls: 0 });
    window.electronAPI = { isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {},
      openCompanion() {}, closeCompanion() {}, pinCompanion() {},
      requestMicrophoneAccess: () => new Promise(resolve => Object.assign(window, { resolveConsent: resolve })) };
    navigator.mediaDevices.getUserMedia = async () => { (window as unknown as { captureCalls: number }).captureCalls++; throw Error('unexpected capture'); };
  });
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  await expect.poll(() => page.evaluate(() => typeof (window as unknown as { resolveConsent: unknown }).resolveConsent)).toBe('function');
  await page.getByRole('button', { name: 'Stop conversation and microphone' }).click();
  await page.evaluate(() => (window as unknown as { resolveConsent: (value: object) => void }).resolveConsent({ status: 'granted' }));
  await expect(page.getByRole('button', { name: 'Start conversation' })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { captureCalls: number }).captureCalls)).toBe(0);
});

test('an enabled microphone and the talk mode are remembered; clearing the choice stops automatic capture', async ({ page }) => {
  await prepare(page);
  await page.goto('/voice/');
  // Nothing remembered yet: a new conversation is typed until the user enables the microphone.
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await page.getByRole('button', { name: 'Enable microphone' }).click();
  await expect(page.getByRole('button', { name: 'Hold to talk', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Hands-free', exact: true }).click();
  await page.goto('/companion/');
  // Opening the companion alone never captures.
  await expect(page.getByRole('status').filter({ hasText: 'Microphone off' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hands-free', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await expect(page.getByRole('button', { name: 'Listening · interrupt' })).toBeVisible();
  await page.getByRole('button', { name: 'Companion settings' }).click();
  const remember = page.getByRole('checkbox', { name: 'Use microphone for conversations' });
  await expect(remember).toBeChecked();
  await page.getByText('Use microphone for conversations', { exact: true }).click();
  await expect(remember).not.toBeChecked();
  // Turning it off releases capture now and keeps the conversation for typing.
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Type a question' })).toBeEnabled();
  await page.getByRole('button', { name: 'Stop conversation and microphone' }).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
});

test('checking the remembered microphone in the companion never drags it; clearing it during consent drops the capture', async ({ page }) => {
  await prepare(page);
  const bridge = await companionBridge(page);
  await page.addInitScript(() => {
    Object.assign(window, { captureCalls: 0, resolveConsent: null });
    window.electronAPI!.requestMicrophoneAccess = () => new Promise(resolve => Object.assign(window, { resolveConsent: resolve }));
    navigator.mediaDevices.getUserMedia = async () => { (window as unknown as { captureCalls: number }).captureCalls++; throw Error('unexpected capture'); };
  });
  await page.setViewportSize({ width: 380, height: 610 });
  await page.goto('/companion/');
  await page.getByRole('button', { name: 'Start conversation' }).click();
  await expect(page.getByRole('button', { name: 'Enable microphone' })).toBeVisible();
  await page.getByRole('button', { name: 'Companion settings' }).click();
  const checkbox = page.getByRole('checkbox', { name: 'Use microphone for conversations' });
  await page.getByText('Use microphone for conversations', { exact: true }).click();
  await expect(checkbox).toBeChecked();
  await expect.poll(() => page.evaluate(() => typeof (window as unknown as { resolveConsent: unknown }).resolveConsent)).toBe('function');
  await checkbox.click();
  await page.evaluate(() => (window as unknown as { resolveConsent: (value: object) => void }).resolveConsent({ status: 'granted' }));
  await expect(checkbox).not.toBeChecked();
  await expect(page.getByRole('textbox', { name: 'Type a question' })).toBeEnabled();
  expect(await page.evaluate(() => (window as unknown as { captureCalls: number }).captureCalls)).toBe(0);
  expect(await bridge.calls()).not.toContain('drag-start');
});
