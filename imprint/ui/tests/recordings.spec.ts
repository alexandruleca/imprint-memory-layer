import { test, expect, type Page } from '@playwright/test';
test.use({ launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] } });

type Rec = Record<string, unknown> & { id: string; status: string };

// Mirrors imprint/api.py's /api/recordings. The computer's audio is an
// oscillator in place of the screen capture, with or without its audio track,
// or with one already ended, as macOS gives the desktop app without permission.
async function mockRecordings(page: Page, { computerAudio = true as boolean | 'ended', desktop = '', recordings = [] as Rec[] } = {}) {
  const state = { recordings, starts: [] as object[], pieces: 0, stops: [] as object[], transcribed: [] as string[], deleted: [] as string[] };
  await page.addInitScript(({ withAudio, platform }: { withAudio: boolean | 'ended'; platform: string }) => {
    if (platform) Object.assign(window, { electronAPI: { platform } });
    navigator.mediaDevices.enumerateDevices = async () => [];
    navigator.mediaDevices.getDisplayMedia = async () => {
      const canvas = Object.assign(document.createElement('canvas'), { width: 4, height: 4 });
      const tracks = [...canvas.captureStream().getVideoTracks()];
      if (withAudio) {
        const context = new AudioContext();
        const tone = context.createOscillator();
        const out = context.createMediaStreamDestination();
        tone.connect(out); tone.start();
        const audio = out.stream.getAudioTracks();
        if (withAudio === 'ended') audio.forEach((track) => track.stop());
        tracks.push(...audio);
      }
      return new MediaStream(tracks);
    };
  }, { withAudio: computerAudio, platform: desktop });
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'default', workspaces: ['default'] } }));
  await page.route('**/api/stats', route => route.fulfill({ json: { total: 1, projects: [['checkout-service', 3]], langs: [], domains: [], types: [], topics: [], layers: [] } }));
  await page.route('**/api/projects', route => route.fulfill({ json: { projects: [{ name: 'checkout-service', count: 3, color: '#888888' }], total: 3 } }));
  await page.route('**/api/recordings', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      state.starts.push(body);
      const rec = { id: 'r'.repeat(32), title: body.title || 'Meeting', project: body.project, others: body.others, status: 'recording',
        started_at: Date.now() / 1000, stopped_at: null, duration: 0, error: '', memories: 0, transcribed_at: null, audio_bytes: 0, transcript: false };
      state.recordings.unshift(rec);
      return route.fulfill({ json: rec });
    }
    await route.fulfill({ json: { recordings: state.recordings, default_project: 'meetings' } });
  });
  await page.route('**/api/recordings/*/audio', async route => {
    if (route.request().method() === 'POST') { state.pieces += 1; return route.fulfill({ json: { audio_bytes: state.pieces } }); }
    const id = route.request().url().split('/').at(-2)!;
    state.deleted.push(`${id}:audio`);
    await route.fulfill({ json: {} });
  });
  await page.route('**/api/recordings/*/stop', async route => {
    const id = route.request().url().split('/').at(-2)!;
    state.stops.push({ id, ...(route.request().postDataJSON() ?? {}) });
    const rec = state.recordings.find(r => r.id === id)!;
    Object.assign(rec, { status: 'stopped', duration: 3, audio_bytes: 100 });
    await route.fulfill({ json: { ...rec, job: { job_id: 'j1', position: 0 } } });
  });
  await page.route('**/api/recordings/*/transcribe', async route => {
    state.transcribed.push(route.request().url().split('/').at(-2)!);
    await route.fulfill({ json: { job: { job_id: 'j2', position: 0 } } });
  });
  await page.route('**/api/recordings/*/transcript', route => route.fulfill({ json: {
    text: '# Planning\n\nRecorded today.\n\n**You** (00:00:01): Morning.\n**Others** (00:00:04): Can you take the booking bug?\n' } }));
  await page.route(/\/api\/recordings\/[a-z0-9]+$/, async route => {
    state.deleted.push(route.request().url().split('/').at(-1)!);
    state.recordings = state.recordings.filter(r => !route.request().url().endsWith(r.id));
    await route.fulfill({ json: { deleted: true } });
  });
  return state;
}

test('a meeting is recorded only with consent, keeps recording across pages, and stops from the top bar', async ({ page }) => {
  const state = await mockRecordings(page);
  await page.goto('/recordings/');
  const start = page.getByRole('button', { name: 'Start recording' });
  await page.getByRole('textbox', { name: 'Recording title' }).fill('Planning');
  await expect(page.getByRole('combobox', { name: 'Project' })).toHaveValue('meetings');
  await expect(start).toBeDisabled();
  await page.getByRole('checkbox', { name: 'Everyone knows it is being recorded' }).click();
  await start.click();
  await expect.poll(() => state.starts).toEqual([{ title: 'Planning', project: 'meetings', others: true, consent: true }]);
  const indicator = page.getByRole('status', { name: 'Recording' });
  await expect(indicator).toContainText(/Recording 00:0\d/);
  await expect(page.getByRole('button', { name: 'Stop and transcribe' })).toBeVisible();

  await page.getByRole('link', { name: 'Search', exact: true }).click();
  await expect(page).toHaveURL(/\/search\/?$/);
  await expect(indicator).toBeVisible();
  await page.waitForTimeout(1200);
  await indicator.getByRole('button', { name: 'Stop recording' }).click();
  await expect(indicator).toHaveCount(0);
  expect(state.pieces).toBeGreaterThan(0);
  expect(state.stops).toEqual([{ id: 'r'.repeat(32), duration: expect.any(Number) }]);
});

test('without the computer\'s audio only the microphone is recorded, and says so', async ({ page }) => {
  const state = await mockRecordings(page, { computerAudio: false });
  await page.goto('/recordings/');
  await page.getByRole('checkbox', { name: 'Everyone knows it is being recorded' }).click();
  await page.getByRole('button', { name: 'Start recording' }).click();
  await expect(page.getByText('Only your microphone is being recorded.')).toBeVisible();
  expect(state.starts).toEqual([expect.objectContaining({ others: false })]);
  await page.getByRole('button', { name: 'Stop and transcribe' }).click();
  await expect(page.getByRole('button', { name: 'Start recording' })).toBeVisible();
});

test('the desktop app on macOS says how to allow the computer\'s audio instead of recording silence', async ({ page }) => {
  const state = await mockRecordings(page, { computerAudio: 'ended', desktop: 'darwin' });
  await page.goto('/recordings/');
  await page.getByRole('checkbox', { name: 'Everyone knows it is being recorded' }).click();
  await page.getByRole('button', { name: 'Start recording' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'macOS did not let Imprint' })).toContainText('Screen & System Audio Recording');
  await expect(page.getByRole('button', { name: 'Start recording' })).toBeEnabled();
  expect(state.starts).toEqual([]);
  // Without the computer's audio, the microphone alone records.
  await page.getByRole('switch', { name: "Include the computer's audio" }).click();
  await page.getByRole('button', { name: 'Start recording' }).click();
  await expect(page.getByText('Only your microphone is being recorded.')).toBeVisible();
  expect(state.starts).toEqual([expect.objectContaining({ others: false })]);
});

test('recordings show their transcript, can be transcribed again, finished after an interruption and deleted', async ({ page }) => {
  const base = { project: 'meetings', others: true, started_at: 1_790_000_000, stopped_at: 1_790_000_600, duration: 600, error: '', transcribed_at: 1 };
  const state = await mockRecordings(page, { recordings: [
    { ...base, id: 'a'.repeat(32), title: 'Planning', status: 'ready', memories: 4, audio_bytes: 5000, transcript: true },
    { ...base, id: 'b'.repeat(32), title: 'Crashed call', status: 'recording', memories: 0, audio_bytes: 900, transcript: false },
    { ...base, id: 'c'.repeat(32), title: 'Muted call', status: 'failed', error: 'Audio ingestion is off; enable audio.enabled in Settings', memories: 0, audio_bytes: 900, transcript: false },
  ] });
  await page.goto('/recordings/');
  const planning = page.getByRole('listitem', { name: 'Planning' });
  await expect(planning).toContainText('Transcribed');
  await expect(planning).toContainText('10 min · meetings · 4 memories');
  await planning.getByRole('button', { name: 'Transcript' }).click();
  const dialog = page.getByRole('dialog', { name: 'Planning' });
  await expect(dialog).toContainText('You (00:00:01): Morning.');
  await page.keyboard.press('Escape');

  await expect(page.getByRole('listitem', { name: 'Muted call' })).toContainText('Audio ingestion is off');
  await page.getByRole('listitem', { name: 'Muted call' }).getByRole('button', { name: 'Transcribe again' }).click();
  await expect.poll(() => state.transcribed).toEqual(['c'.repeat(32)]);

  const crashed = page.getByRole('listitem', { name: 'Crashed call' });
  await expect(crashed).toContainText('Interrupted');
  await crashed.getByRole('button', { name: 'Finish and transcribe' }).click();
  await expect.poll(() => state.stops).toEqual([{ id: 'b'.repeat(32) }]);

  await planning.getByRole('button', { name: 'Delete audio' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
  await expect.poll(() => state.deleted).toEqual([`${'a'.repeat(32)}:audio`]);
  await page.getByRole('button', { name: 'Delete Planning' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('listitem', { name: 'Planning' })).toHaveCount(0);
});

test('speakers told apart in a transcript can be renamed, and the transcript follows', async ({ page }) => {
  const id = 'd'.repeat(32);
  await mockRecordings(page, { recordings: [
    { id, title: 'Standup', project: 'meetings', others: true, status: 'ready', started_at: 1_790_000_000, stopped_at: 1_790_000_600,
      duration: 600, error: '', memories: 3, transcribed_at: 1, audio_bytes: 5000, transcript: true },
  ] });
  let names: Record<string, string> = { 'mic-1': 'You', 'call-1': 'Speaker 1', 'call-2': 'Speaker 2' };
  const renames: object[] = [];
  // Mirrors GET /transcript and PATCH /speakers (ADR 0180).
  await page.route(`**/api/recordings/${id}/transcript`, route => route.fulfill({ json: {
    speakers: names,
    text: `# Standup\n\n**${names['mic-1']}** (00:00:01): Morning.\n\n**${names['call-1']}** (00:00:04): Hi.\n\n**${names['call-2']}** (00:00:09): Booking bug is mine.\n` } }));
  await page.route(`**/api/recordings/${id}/speakers`, async route => {
    const body = route.request().postDataJSON() as { speakers: Record<string, string> };
    renames.push({ method: route.request().method(), ...body });
    names = { ...names, ...body.speakers };
    await route.fulfill({ json: { id, speakers: names } });
  });
  await page.goto('/recordings/');
  await page.getByRole('listitem', { name: 'Standup' }).getByRole('button', { name: 'Transcript' }).click();
  const dialog = page.getByRole('dialog', { name: 'Standup' });
  await expect(dialog).toContainText('Speaker 2 (00:00:09): Booking bug is mine.');
  const save = dialog.getByRole('button', { name: 'Save names' });
  await expect(save).toBeDisabled();
  const second = dialog.getByRole('textbox', { name: 'Name for Speaker 2' });
  await second.fill('  ');
  await expect(save).toBeDisabled();
  await second.fill('Ana');
  await save.click();
  await expect.poll(() => renames).toEqual([{ method: 'PATCH', speakers: { 'call-2': 'Ana' } }]);
  await expect(dialog).toContainText('Ana (00:00:09): Booking bug is mine.');
  await expect(dialog.getByRole('textbox', { name: 'Name for Ana' })).toHaveValue('Ana');
  await expect(save).toBeDisabled();
});
