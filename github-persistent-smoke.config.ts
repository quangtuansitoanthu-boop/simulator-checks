import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'github-persistent-smoke.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  outputDir: 'qa-evidence/webkit-persistent-smoke/test-results',
  reporter: [['line'], ['json', { outputFile: 'qa-evidence/webkit-persistent-smoke/results.json' }]],
  use: {
    browserName: 'webkit',
    headless: true,
    viewport: { width: 375, height: 844 },
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
