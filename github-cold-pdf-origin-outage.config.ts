import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import { LOCAL_ONLY_PROXY } from './global-network-guard';

const evidence = resolve(process.cwd(), 'qa-evidence/cold-pdf-origin-outage');

export default defineConfig({
  testDir: '.',
  testMatch: 'github-cold-pdf-origin-outage.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  maxFailures: 0,
  timeout: 90_000,
  expect: { timeout: 5_000 },
  globalSetup: './global-network-guard.ts',
  outputDir: resolve(evidence, 'test-results'),
  reporter: [['line'], ['json', { outputFile: resolve(evidence, 'results.json') }]],
  use: {
    browserName: 'webkit',
    headless: true,
    locale: 'vi-VN',
    colorScheme: 'light',
    baseURL: 'http://localhost:8787',
    viewport: { width: 375, height: 844 },
    serviceWorkers: 'allow',
    proxy: LOCAL_ONLY_PROXY,
    launchOptions: { proxy: LOCAL_ONLY_PROXY },
    trace: 'on',
    screenshot: 'only-on-failure',
  },
});
