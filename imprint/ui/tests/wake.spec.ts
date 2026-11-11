import { test, expect, type Page } from '@playwright/test';
test.use({ launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] } });

// "Hey Echo" (ADR 0137), driven by a stand-in for the desktop bridge. The
// main process owns the hidden listener window and the wake state; here the
// pages only listen, ask and draw.

type Calls = unknown[][];
const calls = (page: Page) => page.evaluate(() => (window as unknown as { calls: Calls }).calls);

/** A desktop bridge whose wake state and events the test controls. */
async function desktop(page: Page, options: { wake?: Record<string, unknown>; session?: { id: number; text: string | null } | null } = {}) {
  await page.addInitScript(({ wake, session }) => {
    const calls: unknown[][] = [];
    const listeners: Record<string, ((value: unknown) => void)[]> = {};
    const listen = (name: string, callback: (value: unknown) => void) => { (listeners[name] ||= []).push(callback); return () => {}; };
    Object.assign(window, {
      calls,
      emit: (name: string, value: unknown) => listeners[name]?.forEach(callback => callback(value)),
      electronAPI: {
        isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {},
        openCompanion() {}, closeCompanion() {}, pinCompanion() {},
        requestMicrophoneAccess: async () => { calls.push(['consent']); return { status: 'granted' }; },
        getCompanionState: async () => ({ open: true, docked: true, pinned: false }), onCompanionState: () => () => {},
        getWakeState: async () => wake, onWakeState: (callback: (value: unknown) => void) => listen('wake', callback),
        setWake: async (choice: unknown) => { calls.push(['set', choice]); return wake; },
        controlWake: (action: string) => calls.push(['control', action]),
        reportMicrophone: (on: boolean) => calls.push(['microphone', on]),
        getWakeSession: async () => session,
        onWake: (callback: (value: unknown) => void) => listen('event', callback),
        wakeDone: (id: number, restore: boolean) => calls.push(['done', id, restore]),
        getLauncherState: async () => ({ ring: false, panel: null, echo: false, echoSide: 0, activity: null, wake,
          recorder: { phase: 'idle', id: '', title: '', project: '', startedAt: 0, others: false, error: '' } }),
        onLauncherState: (callback: (value: unknown) => void) => listen('launcher', callback),
        getRecorder: async () => ({ phase: 'idle', id: '', title: '', project: '', startedAt: 0, others: false, error: '' }), onRecorder: () => () => {},
      },
    });
  }, { wake: options.wake ?? null, session: options.session ?? null });
}

const LISTENING = { enabled: true, listening: true, reason: 'conversation', pausedUntil: 0, battery: 'listen', problem: '', checking: false, heard: 0 };
const OFF = { ...LISTENING, enabled: false, listening: false, reason: 'off' };

function voiceApi(page: Page, wakeStatus: () => object = () => ({ installed: true, idle_seconds: 15, setup: { stage: 'idle', busy: false } })) {
  return page.route('**/api/**', route => {
    const url = route.request().url();
    if (url.endsWith('/voice/status')) return route.fulfill({ json: { installed: true, configured: true, supported: true, chat_provider: 'local', workspace: 'default' } });
    if (url.endsWith('/voice/setup')) return route.fulfill({ json: { stage: 'ready', installed: true, supported: true, busy: false } });
    if (url.includes('/voice/wake/')) return route.fulfill({ json: wakeStatus() });
    if (url.endsWith('/voice/sessions')) return route.fulfill({ json: { token: 'fixture', workspace: 'default' } });
    if (url.endsWith('/workspaces')) return route.fulfill({ json: { active: 'default', known: ['default'] } });
    return route.fulfill({ json: { ok: true, settings: [], jobs: [], requests: [] } });
  });
}

test.describe('the hidden listener page', () => {
  /** The microphone plays 1.2 s of "speech" (a loud tone) after 0.4 s of quiet. */
  async function listener(page: Page, sessions: (route: import('@playwright/test').Route) => Promise<void> | void) {
    await page.addInitScript(() => {
      const reports: unknown[] = [];
      navigator.mediaDevices.getUserMedia = async () => {
        const context = new AudioContext();
        const tone = context.createOscillator(), gain = context.createGain(), out = context.createMediaStreamDestination();
        tone.frequency.value = 220; gain.gain.value = 0;
        gain.gain.setValueAtTime(.5, context.currentTime + .4); gain.gain.setValueAtTime(0, context.currentTime + 1.6);
        tone.connect(gain).connect(out); tone.start();
        return out.stream;
      };
      Object.assign(window, {
        reports,
        electronAPI: { isElectron: true, platform: 'darwin', close() {}, quit() {}, minimize() {}, maximize() {}, openCompanion() {}, closeCompanion() {}, pinCompanion() {},
          requestMicrophoneAccess: async () => ({ status: 'granted' }), wakeListenerConfig: async () => ({ battery: true }),
          onWakeListenerConfig: () => () => {}, reportWakeListener: (report: unknown) => reports.push(report) },
      });
    });
    await page.route('**/api/voice/wake/sessions', sessions);
    await page.route('**/api/**', route => route.fallback());
  }
  const reports = (page: Page) => page.evaluate(() => (window as unknown as { reports: { type: string }[] }).reports);

  test('sends one 16 kHz probe for a burst of speech, then hands over the question after a wake', async ({ page }) => {
    const messages: { type?: string; id?: number; audio?: string; quiet?: number; token?: string }[] = [];
    let session: unknown = null;
    await listener(page, route => { session = JSON.parse(route.request().postData() || '{}'); return route.fulfill({ json: { token: 'wake-token' } }); });
    await page.routeWebSocket('**/api/voice/wake/stream', socket => socket.onMessage(raw => {
      const message = JSON.parse(raw as string);
      messages.push(message);
      if (message.token) socket.send(JSON.stringify({ type: 'ready' }));
      if (message.type === 'probe') socket.send(JSON.stringify({ type: 'probe', id: message.id, wake: true }));
      if (message.type === 'utterance') socket.send(JSON.stringify({ type: 'question', id: message.id, text: 'what did we decide' }));
    }));
    await page.goto('/wake/');
    await expect.poll(() => reports(page)).toContainEqual({ type: 'question', id: 1, text: 'what did we decide' });
    expect(session).toEqual({ battery: true });
    expect(messages[0]).toEqual({ token: 'wake-token' });
    const probe = messages.find(message => message.type === 'probe')!;
    // A WAV header, then 16-bit samples at 16 kHz: at most the first 2 s of the burst.
    const bytes = Buffer.from(probe.audio!, 'base64');
    expect(bytes.subarray(0, 4).toString()).toBe('RIFF');
    expect(bytes.readUInt32LE(24)).toBe(16000);
    expect(bytes.length - 44).toBeLessThanOrEqual(2 * 16000 * 2);
    expect(probe.quiet).toBeGreaterThan(0);
    expect(messages.filter(message => message.type === 'probe')).toHaveLength(1);
    expect((await reports(page)).map(report => report.type)).toEqual(['listening', 'checking', 'checking', 'heard', 'question']);
  });

  test('a missing model or a refused microphone is reported, and a plain browser listens to nothing', async ({ page }) => {
    await listener(page, route => route.fulfill({ status: 409, json: { detail: 'Turn on “Hey Echo” to download its model first' } }));
    await page.goto('/wake/');
    await expect.poll(() => reports(page)).toEqual([{ type: 'error', kind: 'model', message: 'Turn on “Hey Echo” to download its model first' }]);

    const other = await page.context().newPage();
    let asked = false;
    await other.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { (window as unknown as { asked: boolean }).asked = true; throw new Error('no'); }; });
    await other.goto('/wake/');
    await other.waitForTimeout(500);
    asked = await other.evaluate(() => !!(window as unknown as { asked?: boolean }).asked);
    expect(asked).toBe(false);
  });
});

test('in a browser the switch says it needs the desktop app', async ({ page }) => {
  await voiceApi(page);
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await expect(page.getByRole('switch', { name: 'Listen for “Hey Echo”' })).toBeDisabled();
  await expect(page.getByText('“Hey Echo” needs the Imprint desktop app.')).toBeVisible();
});

test('turning it on explains first, asks the OS, downloads the model with its progress, then turns on', async ({ page }) => {
  let polls = 0, posted = false;
  await desktop(page, { wake: OFF });
  await voiceApi(page, () => {
    if (!posted) return { installed: false, idle_seconds: 20, setup: { stage: 'idle', downloaded: 0, total: 78000000, busy: false } };
    polls++;
    return polls < 3 ? { installed: false, idle_seconds: 20, setup: { stage: 'downloading', downloaded: 39000000, total: 78000000, busy: true } }
      : { installed: true, idle_seconds: 20, setup: { stage: 'ready', downloaded: 78000000, total: 78000000, busy: false } };
  });
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/voice/wake/setup')) posted = true; });
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await expect(page.getByText('Off. Turn it on to wake Echo by voice.')).toBeVisible();
  await page.getByRole('switch', { name: 'Listen for “Hey Echo”' }).click();
  const sheet = page.getByRole('dialog', { name: 'Listen for “Hey Echo”?' });
  await expect(sheet).toContainText('first two seconds are checked on this computer');
  await expect(sheet).toContainText('78 MB');
  expect((await calls(page)).filter(call => call[0] !== 'microphone')).toEqual([]);
  await sheet.getByRole('checkbox', { name: 'Pause while on battery' }).click();
  await sheet.getByRole('button', { name: 'Turn on' }).click();
  await expect(sheet).toBeHidden();
  const made = await calls(page);
  expect(made).toContainEqual(['consent']);
  expect(made).toContainEqual(['set', { enabled: true, battery: 'pause' }]);
  expect(posted).toBe(true);
  await page.evaluate(state => (window as unknown as { emit: (name: string, value: unknown) => void }).emit('wake', state), LISTENING);
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await expect(page.getByRole('switch', { name: 'Listen for “Hey Echo”' })).toBeChecked();
  await expect(page.locator('.voice-wake-status')).toHaveText('Listening for “Hey Echo”');
  await page.getByRole('button', { name: 'Pause for 1 hour' }).click();
  expect(await calls(page)).toContainEqual(['control', 'pause']);
});

test('the explanation fits the floating Echo window: its points scroll and Turn on stays in reach', async ({ page }) => {
  // Echo's window is at most 400 × 640 and can be 420 high; the sheet must not run off it.
  await page.setViewportSize({ width: 400, height: 420 });
  await desktop(page, { wake: OFF });
  await voiceApi(page, () => ({ installed: false, idle_seconds: 20, setup: { stage: 'idle', downloaded: 0, total: 78000000, busy: false } }));
  await page.goto('/companion/');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await page.getByRole('switch', { name: 'Listen for “Hey Echo”' }).click();
  const sheet = page.getByRole('dialog', { name: 'Listen for “Hey Echo”?' });
  await expect(sheet).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('wake-sheet-420.png') });
  const box = (await sheet.boundingBox())!;
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(420);
  await expect(sheet.getByRole('button', { name: 'Turn on' })).toBeInViewport();
  await expect(sheet.getByRole('button', { name: 'Cancel' })).toBeInViewport();
  const last = sheet.getByText('downloads a 78 MB English speech model once');
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeInViewport();
  await expect(sheet.getByRole('button', { name: 'Turn on' })).toBeInViewport();
});

test('a refused microphone keeps it off and says why', async ({ page }) => {
  await desktop(page, { wake: OFF });
  await page.addInitScript(() => { window.electronAPI!.requestMicrophoneAccess = async () => ({ status: 'denied' }); });
  await voiceApi(page);
  await page.goto('/voice/');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await page.getByRole('switch', { name: 'Listen for “Hey Echo”' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Turn on' }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Microphone permission denied');
  expect((await calls(page)).filter(call => call[0] === 'set')).toEqual([]);
});

test('a wake starts Echo hands-free with the question, and Echo ends when asked', async ({ page }) => {
  const turns: Record<string, unknown>[] = [];
  await desktop(page, { wake: LISTENING, session: { id: 5, text: 'what did we decide' } });
  await voiceApi(page, () => ({ installed: true, idle_seconds: 30, setup: { stage: 'idle', busy: false } }));
  await page.addInitScript(() => { navigator.mediaDevices.enumerateDevices = async () => []; });
  await page.routeWebSocket('**/api/voice/stream', socket => socket.onMessage(raw => {
    const message = JSON.parse(raw as string);
    if (message.token) { socket.send(JSON.stringify({ type: 'ready', workspace: 'default', engine: 'chat' })); return; }
    turns.push(message);
    if (message.type === 'turn') {
      socket.send(JSON.stringify({ type: 'user', id: message.id, text: message.text }));
      socket.send(JSON.stringify({ type: 'end', id: message.id }));
    }
  }));
  await page.goto('/companion/');
  await expect.poll(() => turns).toContainEqual({ type: 'turn', id: 1, text: 'what did we decide', wake: true });
  await expect.poll(() => calls(page)).toContainEqual(['done', 5, true]);
  await expect(page.getByRole('button', { name: 'Push to talk', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('a wake during a typed conversation carries it on hands-free instead of starting another', async ({ page }) => {
  const turns: Record<string, unknown>[] = [];
  let sessions = 0;
  await desktop(page, { wake: LISTENING });
  await voiceApi(page, () => ({ installed: true, idle_seconds: 30, setup: { stage: 'idle', busy: false } }));
  page.on('request', request => { if (request.url().endsWith('/voice/sessions')) sessions++; });
  await page.routeWebSocket('**/api/voice/stream', socket => socket.onMessage(raw => {
    const message = JSON.parse(raw as string);
    if (message.token) { socket.send(JSON.stringify({ type: 'ready', workspace: 'default', engine: 'chat' })); return; }
    turns.push(message);
    if (message.type === 'turn') socket.send(JSON.stringify({ type: 'done', id: message.id }));
  }));
  await page.goto('/companion/');
  await page.getByRole('button', { name: 'Companion settings' }).click();
  await page.getByRole('textbox', { name: 'Type a question' }).fill('hello');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect.poll(() => turns).toContainEqual({ type: 'turn', id: 1, text: 'hello' });
  await page.keyboard.press('Escape');
  const emit = (value: unknown) => page.evaluate(event => (window as unknown as { emit: (name: string, value: unknown) => void }).emit('event', event), value);
  await emit({ type: 'start', id: 9 });
  await expect(page.getByText(/Heard “Hey Echo”/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hands-free' })).toHaveAttribute('aria-pressed', 'true');
  await emit({ type: 'question', id: 9, text: 'and yesterday?' });
  await expect.poll(() => turns).toContainEqual({ type: 'turn', id: 2, text: 'and yesterday?', wake: true });
  expect(sessions).toBe(1);
});

test('a click in Echo during a wake-started conversation keeps it open', async ({ page }) => {
  await desktop(page, { wake: LISTENING, session: { id: 6, text: null } });
  await voiceApi(page, () => ({ installed: true, idle_seconds: 30, setup: { stage: 'idle', busy: false } }));
  await page.routeWebSocket('**/api/voice/stream', socket => socket.onMessage(raw => {
    if (JSON.parse(raw as string).token) socket.send(JSON.stringify({ type: 'ready', workspace: 'default', engine: 'chat' }));
  }));
  await page.goto('/companion/');
  await expect(page.getByText('Heard “Hey Echo” · ends after 30 s of quiet or when you say “stop”')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hands-free' })).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.voice-stage').click();
  await expect.poll(() => calls(page)).toContainEqual(['done', 6, false]);
  await expect(page.getByText(/Heard “Hey Echo”/)).toBeHidden();
});

test('the top bar shows listening with Pause and Off; the orb shows its ear', async ({ page }) => {
  await desktop(page, { wake: LISTENING });
  await voiceApi(page);
  // A page the fixture API can feed; the chip is in the top bar of every page.
  await page.goto('/voice/');
  const chip = page.getByRole('status', { name: 'Listening for Hey Echo' });
  await expect(chip).toContainText('Listening for “Hey Echo”');
  await chip.getByRole('button', { name: 'Pause listening for 1 hour' }).click();
  await chip.getByRole('button', { name: 'Turn off Hey Echo' }).click();
  expect(await calls(page)).toEqual(expect.arrayContaining([['control', 'pause'], ['control', 'off']]));
  await page.evaluate(state => (window as unknown as { emit: (name: string, value: unknown) => void }).emit('wake', state), OFF);
  await expect(chip).toBeHidden();

  await page.setViewportSize({ width: 72, height: 72 });
  await page.goto('/launcher/');
  await expect(page.getByRole('button', { name: /Listening for Hey Echo$/ })).toBeVisible();
  await expect(page.locator('.orb-ear')).toHaveAttribute('data-state', 'listening');
  await page.evaluate(state => (window as unknown as { emit: (name: string, value: unknown) => void }).emit('launcher', {
    ring: false, panel: null, echo: false, echoSide: 0, activity: null, wake: state,
    recorder: { phase: 'idle', id: '', title: '', project: '', startedAt: 0, others: false, error: '' } }), { ...LISTENING, heard: Date.now() });
  await expect(page.locator('.orb-wake-pulse')).toBeAttached();
  await page.evaluate(state => (window as unknown as { emit: (name: string, value: unknown) => void }).emit('launcher', {
    ring: false, panel: null, echo: false, echoSide: 0, activity: null, wake: state,
    recorder: { phase: 'idle', id: '', title: '', project: '', startedAt: 0, others: false, error: '' } }), { ...LISTENING, listening: false, reason: 'recording' });
  await expect(page.locator('.orb-ear')).toHaveAttribute('data-state', 'paused');
});
