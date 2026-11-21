// Stage this artifact at app/tests/e2e/github-webkit.config.ts.
import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// These twelve original cases require Chromium CDP. Every other case in the
// same files stays selected, including PDF lifecycle/contrast and navigation.
const cdpOnlyTitles = [
  'giu-gioi has a canonical catalog link, reloads once and follows Back/Forward',
  'bat-quan-trai has a canonical catalog link, reloads once and follows Back/Forward',
  'thanh-tinh has a canonical catalog link, reloads once and follows Back/Forward',
  'le-phat has a canonical catalog link, reloads once and follows Back/Forward',
  'meditation has a canonical catalog link, reloads once and follows Back/Forward',
  'rosary has a canonical catalog link, reloads once and follows Back/Forward',
  'cong-phu has a canonical catalog link, reloads once and follows Back/Forward',
  'thoi-khoa has a canonical catalog link, reloads once and follows Back/Forward',
  'tiep-tuc-cong-phu has a canonical catalog link, reloads once and follows Back/Forward',
  'meditation water preserves session and controls 375 false',
  'meditation water preserves session and controls 375 true',
  'canceling a partial offline download leaves no copy, then retry is complete and readable offline',
];
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const cdpOnly = new RegExp(`(?:${cdpOnlyTitles.map(escape).join('|')})$`);
const chromiumOnlyFiles = [
  '**/journal-keyboard-overlap.spec.ts',
  '**/personal-journal-zen.spec.ts',
];
const patterns = <T>(value: T | T[] | undefined | null): T[] =>
  value === undefined || value === null ? [] : Array.isArray(value) ? value : [value];

export default defineConfig({
  ...base,
  use: { ...base.use, browserName: 'webkit' },
  projects: (base.projects ?? []).map(project => ({
    ...project,
    use: { ...project.use, browserName: 'webkit' },
    testIgnore: [...patterns(project.testIgnore ?? base.testIgnore), ...chromiumOnlyFiles],
    grepInvert: [...patterns(project.grepInvert ?? base.grepInvert), cdpOnly],
  })),
});
