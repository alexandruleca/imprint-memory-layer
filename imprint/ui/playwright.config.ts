import { defineConfig } from '@playwright/test';

// The trusted CI runners share their host's network, so concurrent jobs (and
// a local run on that host) would meet on one fixed port; CI passes a free one.
const port = Number(process.env.PLAYWRIGHT_PORT) || 3100;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  // The default, half the CPUs, gives 8 workers on the 16-thread CI host,
  // which its other jobs and VMs share: timing-bound tests then miss their
  // waits. Two match the hosted runners these tests were tuned on.
  workers: process.env.CI ? 2 : undefined,
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true, trace: 'retain-on-failure' },
  webServer: {
    command: `python3 -m http.server ${port} --bind 127.0.0.1 --directory out`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
  },
});
