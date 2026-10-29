import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  // The default, half the CPUs, gives 8 workers on the 16-thread CI host,
  // which its other jobs and VMs share: timing-bound tests then miss their
  // waits. Two match the hosted runners these tests were tuned on.
  workers: process.env.CI ? 2 : undefined,
  use: { baseURL: 'http://127.0.0.1:3100', headless: true, trace: 'retain-on-failure' },
  webServer: {
    command: 'python3 -m http.server 3100 --bind 127.0.0.1 --directory out',
    url: 'http://127.0.0.1:3100',
    reuseExistingServer: !process.env.CI,
  },
});
