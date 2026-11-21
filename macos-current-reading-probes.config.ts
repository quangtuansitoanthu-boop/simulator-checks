// CI-only diagnostics for three unchanged original macOS WebKit journeys.
import { defineConfig } from '@playwright/test';
import webkit from './github-webkit.config';

export default defineConfig({
  ...webkit,
  use: { ...webkit.use, trace: 'on', screenshot: 'on' },
});
