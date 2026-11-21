import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import { test } from './helpers/webkit-persistent-fixtures';

const marker = 'Synthetic SW offline fallback';
const fallbackHtml = `<!doctype html><meta charset="utf-8"><h1>${marker}</h1>`;
const worker = `
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.mode === 'navigate') {
    event.respondWith(Promise.resolve(new Response(${JSON.stringify(fallbackHtml)}, {
      headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'x-probe-worker': 'literal' }
    })));
  }
});`;

async function stopServer(server: Server): Promise<void> {
  if (!server.listening) return;
  const closed = new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  server.closeAllConnections();
  await closed;
}

async function probe(mode: 'offline-emulation' | 'origin-unavailable', page: Page,
  context: BrowserContext, browserName: string, testInfo: TestInfo): Promise<void> {
  expect(browserName).toBe('webkit');
  const guardian = context.pages()[0];
  expect(guardian).not.toBe(page);
  expect(context.pages()).toEqual([guardian, page]);
  expect(guardian.url()).toBe('about:blank');
  const requests: { method: string | undefined; url: string | undefined }[] = [];
  const failedRequests: { url: string; error: string | undefined }[] = [];
  const observation: Record<string, unknown> = {
    diagnosticOnly: true, applicationLoaded: false, mode, browserName,
    browserVersion: context.browser()?.version(), requests, failedRequests,
  };
  const server = createServer((request, response) => {
    requests.push({ method: request.method, url: request.url });
    response.setHeader('cache-control', 'no-store');
    if (request.method !== 'GET') { response.writeHead(405).end(); return; }
    if (request.url === '/sw.js') {
      response.writeHead(200, { 'content-type': 'application/javascript; charset=utf-8' }).end(worker);
    } else if (request.url === '/api/health') {
      response.writeHead(200, { 'content-type': 'application/json' }).end('{"ok":true}');
    } else {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8',
        'content-security-policy': "default-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'" });
      response.end('<!doctype html><h1>Synthetic origin online</h1>');
    }
  });
  let cold: Page | undefined;
  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    observation.origin = origin;
    await page.goto(origin);
    expect(await page.evaluate(() => fetch('/api/health', { cache: 'no-store' }).then(response => response.status))).toBe(200);
    await page.evaluate(async () => {
      await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
    });
    await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
    expect(await page.evaluate(() => navigator.serviceWorker.controller?.scriptURL)).toBe(`${origin}/sw.js`);
    const warm = await page.reload();
    expect(warm?.status()).toBe(200);
    expect(warm?.headers()['x-probe-worker']).toBe('literal');
    await expect(page.getByRole('heading', { name: marker, exact: true })).toBeVisible();
    observation.warmFromServiceWorker = warm?.fromServiceWorker();
    await page.close();
    expect(context.pages()).toEqual([guardian]);
    expect(guardian.isClosed()).toBe(false);
    cold = await context.newPage();
    cold.on('requestfailed', request => failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
    if (mode === 'offline-emulation') await context.setOffline(true);
    else await stopServer(server); // Origin outage is a control, not an equivalent offline simulation.
    observation.serverListeningAtNavigation = server.listening;
    const response = await cold.goto(`${origin}/uncached-target?article=probe`);
    observation.navigation = { status: response?.status(), fromServiceWorker: response?.fromServiceWorker() };
    expect(response?.status()).toBe(200);
    expect(response?.fromServiceWorker()).toBe(true);
    await expect(cold.getByRole('heading', { name: marker, exact: true })).toBeVisible();
    const health = await cold.evaluate(async () => {
      try {
        const response = await fetch('/api/health', { cache: 'no-store' });
        return { networkFailed: false, status: response.status };
      } catch (error) { return { networkFailed: true, error: String(error) }; }
    });
    observation.networkOnlyHealth = health;
    expect(health.networkFailed).toBe(true);
    expect(guardian.url()).toBe('about:blank');
    expect(guardian.isClosed()).toBe(false);
  } catch (error) {
    observation.error = String(error);
    throw error;
  } finally {
    try {
      await context.setOffline(false);
    } finally {
      try { await stopServer(server); }
      finally {
        observation.guardianAliveAtCleanup = !guardian.isClosed();
        observation.coldPageUrl = cold?.url();
        await testInfo.attach(`stock-sw-${mode}`, {
          contentType: 'application/json', body: Buffer.from(JSON.stringify(observation, null, 2)),
        });
      }
    }
  }
}

test('stock WebKit offline emulation preserves literal service-worker cold navigation', async ({ page, context, browserName }, info) => {
  await probe('offline-emulation', page, context, browserName, info);
});

test('stock WebKit origin shutdown preserves literal service-worker cold navigation', async ({ page, context, browserName }, info) => {
  await probe('origin-unavailable', page, context, browserName, info);
});
