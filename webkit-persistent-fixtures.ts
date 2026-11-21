// CI-only: each default WebKit context owns a fresh disk profile, like WKWebView.
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { test as base, type BrowserContext, type BrowserContextOptions } from '@playwright/test';

export * from '@playwright/test';

type AppBrowserContext = (options?: BrowserContextOptions) => Promise<BrowserContext>;

async function removeOwnedProfile(profile: string, parent: string) {
  const owned = resolve(profile);
  if (dirname(owned) !== parent || !basename(owned).startsWith('tutam-webkit-test-')
    || await realpath(owned) !== owned) {
    throw new Error('Refusing to remove an unowned WebKit profile');
  }
  await rm(owned, { recursive: true, force: true });
}

export const test = base.extend<{ appBrowserContext: AppBrowserContext }>({
  // Additive factory: browser.newContext itself remains unchanged for other tests.
  appBrowserContext: async ({
    browser, browserName, playwright, headless, channel, launchOptions, connectOptions,
    actionTimeout, navigationTimeout,
  }, use, testInfo) => {
    const parent = await realpath(tmpdir());
    const owned: { context?: BrowserContext; profile?: string }[] = [];
    try {
      await use(async (options = {}) => {
        const entry: (typeof owned)[number] = {};
        owned.push(entry);
        if (browserName !== 'webkit') {
          entry.context = await browser.newContext(options);
        } else {
          if (connectOptions) throw new Error('Persistent WebKit CI requires a local browser, not connectOptions');
          entry.profile = await mkdtemp(join(parent, 'tutam-webkit-test-'));
          const { storageState, ...persistentOptions } = options;
          entry.context = await playwright.webkit.launchPersistentContext(entry.profile, {
            handleSIGINT: false,
            ...launchOptions,
            headless,
            ...(channel === undefined ? {} : { channel }),
            ...persistentOptions,
          });
          // Explicit calls keep their own options; the default context passes resolved defaults.
          if (storageState !== undefined) await entry.context.setStorageState(storageState);
          // Keep the untouched about:blank page so closing an app page cannot quit WebKit.
          await testInfo.attach(`webkit-persistent-profile-${owned.length}`, {
            contentType: 'application/json',
            body: Buffer.from(JSON.stringify({ fresh: true, profile: basename(entry.profile) })),
          });
        }
        entry.context.setDefaultTimeout(actionTimeout);
        entry.context.setDefaultNavigationTimeout(navigationTimeout);
        return entry.context;
      });
    } finally {
      const failures: unknown[] = [];
      for (const entry of owned.reverse()) {
        try {
          try {
            // close() remains safe after the original test explicitly closes its context.
            if (entry.context) await entry.context.close();
          } finally {
            if (entry.profile) await removeOwnedProfile(entry.profile, parent);
          }
        } catch (error) {
          failures.push(error);
        }
      }
      if (failures.length) throw new AggregateError(failures, 'WebKit test context cleanup failed');
    }
  },
  context: async ({
    appBrowserContext,
    contextOptions, acceptDownloads, bypassCSP, clientCertificates, colorScheme,
    deviceScaleFactor, extraHTTPHeaders, geolocation, hasTouch, httpCredentials,
    ignoreHTTPSErrors, isMobile, javaScriptEnabled, locale, offline, permissions,
    proxy, storageState, timezoneId, userAgent, viewport, baseURL, serviceWorkers,
  }, use) => {
    // Resolved option fixtures retain test.use/project.use overrides over contextOptions.
    const options: BrowserContextOptions = { ...contextOptions };
    const resolvedOptions = {
      acceptDownloads, bypassCSP, clientCertificates, colorScheme, deviceScaleFactor,
      extraHTTPHeaders, geolocation, hasTouch, httpCredentials, ignoreHTTPSErrors,
      isMobile, javaScriptEnabled, locale, offline, permissions, proxy, storageState,
      timezoneId, userAgent, viewport, baseURL, serviceWorkers,
    };
    for (const [key, value] of Object.entries(resolvedOptions)) {
      if (value !== undefined) Object.assign(options, { [key]: value });
    }
    await use(await appBrowserContext(options));
  },
  page: async ({ context }, use) => {
    // Use a distinct app page; the persistent initial page stays as a guardian.
    const page = await context.newPage();
    await use(page);
  },
});
