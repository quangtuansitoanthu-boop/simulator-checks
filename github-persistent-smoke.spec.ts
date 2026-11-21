import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect, type BrowserContext, type Page } from '@playwright/test';
import { test } from './helpers/webkit-persistent-fixtures';

let server: Server;
let origin: string;

test.beforeAll(async () => {
  server = createServer((request, response) => {
    if (request.method !== 'GET' || request.url !== '/') {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, {
      'content-type': 'text/html; charset=utf-8',
      'cache-control': 'no-store',
      'content-security-policy': "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    });
    response.end('<!doctype html><html lang="en"><meta charset="utf-8"><title>Persistent WebKit smoke</title><h1>Loopback smoke ready</h1></html>');
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

test.afterAll(async () => {
  if (!server?.listening) return;
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
});

async function openLocalPage(context: BrowserContext, page: Page): Promise<void> {
  await context.route('**/*', route => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort('blockedbyclient'));
  const response = await page.goto(origin);
  expect(response?.status()).toBe(200);
  expect(response?.headers()['content-security-policy']).toContain("default-src 'self'");
  await expect(page.getByRole('heading', { name: 'Loopback smoke ready', exact: true })).toBeVisible();
}

async function storedBlob(page: Page, key: string, write?: string) {
  return page.evaluate(async ({ key, write }) => {
    const opened = indexedDB.open('codex-persistent-webkit-smoke-v1', 1);
    opened.onupgradeneeded = () => opened.result.createObjectStore('blobs');
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      opened.onsuccess = () => resolve(opened.result);
      opened.onerror = () => reject(new Error(`IDB open: ${opened.error?.name}`));
      opened.onblocked = () => reject(new Error('IDB open blocked'));
    });
    try {
      if (write !== undefined) {
        const transaction = database.transaction('blobs', 'readwrite');
        const completed = new Promise<void>((resolve, reject) => {
          transaction.oncomplete = () => resolve();
          transaction.onerror = () => reject(new Error(`IDB write: ${transaction.error?.name}`));
          transaction.onabort = () => reject(new Error(`IDB abort: ${transaction.error?.name}`));
        });
        transaction.objectStore('blobs').put(new Blob([write], { type: 'application/pdf' }), key);
        await completed;
      }
      const request = database.transaction('blobs', 'readonly').objectStore('blobs').get(key);
      const value = await new Promise<unknown>((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(new Error(`IDB read: ${request.error?.name}`));
      });
      const frames: number[] = [];
      for (let count = 0; count < 2; count++) {
        frames.push(await new Promise<number>(resolve => requestAnimationFrame(resolve)));
      }
      return {
        found: value !== undefined,
        isBlob: value instanceof Blob,
        type: value instanceof Blob ? value.type : null,
        size: value instanceof Blob ? value.size : null,
        text: value instanceof Blob ? await value.text() : null,
        frames,
      };
    } finally { database.close(); }
  }, { key, write });
}

function expectBlob(result: Awaited<ReturnType<typeof storedBlob>>, text: string): void {
  expect(result).toMatchObject({ found: true, isBlob: true, type: 'application/pdf', size: Buffer.byteLength(text), text });
  expect(result.frames).toHaveLength(2);
  expect(result.frames[1]).toBeGreaterThan(result.frames[0]);
}

test('default persistent page stays alive and retains a real PDF Blob across reload', async ({ page, context, browserName }) => {
  expect(browserName).toBe('webkit');
  const guardian = context.pages()[0];
  expect(guardian).not.toBe(page);
  expect(guardian.url()).toBe('about:blank');
  expect(guardian.isClosed()).toBe(false);
  expect(context.pages()).toEqual([guardian, page]);
  expect(page.isClosed()).toBe(false);
  await openLocalPage(context, page);
  const text = '%PDF-1.7\nDefault persistent profile\n';
  expectBlob(await storedBlob(page, 'document', text), text);
  const reloaded = await page.reload();
  expect(reloaded?.status()).toBe(200);
  expectBlob(await storedBlob(page, 'document'), text);
  expect(context.pages()).toEqual([guardian, page]);
  expect(guardian.url()).toBe('about:blank');
  expect(guardian.isClosed()).toBe(false);
  expect(page.isClosed()).toBe(false);
});

test('explicit persistent profiles isolate cookies and real PDF Blobs and remain alive', async ({ appBrowserContext, browserName }) => {
  expect(browserName).toBe('webkit');
  // The factory owns both temporary profiles and closes/removes them in its finally block.
  const first = await appBrowserContext({ viewport: { width: 375, height: 844 }, serviceWorkers: 'block' });
  const second = await appBrowserContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block' });
  expect(first).not.toBe(second);
  const firstGuardian = first.pages()[0];
  const secondGuardian = second.pages()[0];
  const firstPage = await first.newPage();
  const secondPage = await second.newPage();
  expect(first.pages()).toEqual([firstGuardian, firstPage]);
  expect(second.pages()).toEqual([secondGuardian, secondPage]);
  expect(firstGuardian.url()).toBe('about:blank');
  expect(secondGuardian.url()).toBe('about:blank');
  expect(firstGuardian.isClosed()).toBe(false);
  expect(secondGuardian.isClosed()).toBe(false);
  await openLocalPage(first, firstPage);
  await openLocalPage(second, secondPage);
  await first.addCookies([{ name: 'smoke-profile', value: 'first', url: origin, sameSite: 'Lax' }]);
  expect((await second.cookies(origin)).filter(cookie => cookie.name === 'smoke-profile')).toEqual([]);
  const firstText = '%PDF-1.7\nFirst isolated profile\n';
  expectBlob(await storedBlob(firstPage, 'same-document-key', firstText), firstText);
  const absent = await storedBlob(secondPage, 'same-document-key');
  expect(absent).toMatchObject({ found: false, isBlob: false, type: null, size: null, text: null });
  expect(absent.frames[1]).toBeGreaterThan(absent.frames[0]);
  const secondText = '%PDF-1.7\nSecond isolated profile\n';
  expectBlob(await storedBlob(secondPage, 'same-document-key', secondText), secondText);
  expectBlob(await storedBlob(firstPage, 'same-document-key'), firstText);
  await second.addCookies([{ name: 'smoke-profile', value: 'second', url: origin, sameSite: 'Lax' }]);
  expect((await first.cookies(origin)).find(cookie => cookie.name === 'smoke-profile')?.value).toBe('first');
  expect((await second.cookies(origin)).find(cookie => cookie.name === 'smoke-profile')?.value).toBe('second');
  expect(firstPage.isClosed()).toBe(false);
  expect(secondPage.isClosed()).toBe(false);
  expect(firstGuardian.url()).toBe('about:blank');
  expect(secondGuardian.url()).toBe('about:blank');
  expect(firstGuardian.isClosed()).toBe(false);
  expect(secondGuardian.isClosed()).toBe(false);
});

test('closing the app page preserves its guardian and a real PDF Blob in a newly opened page', async ({ page, context, browserName }) => {
  expect(browserName).toBe('webkit');
  const guardian = context.pages()[0];
  expect(guardian).not.toBe(page);
  expect(context.pages()).toEqual([guardian, page]);
  expect(guardian.url()).toBe('about:blank');
  expect(guardian.isClosed()).toBe(false);
  await openLocalPage(context, page);
  const text = '%PDF-1.7\nClosed app page retained this document\n';
  expectBlob(await storedBlob(page, 'closed-page-document', text), text);
  await page.close();
  expect(page.isClosed()).toBe(true);
  expect(context.pages()).toEqual([guardian]);
  expect(guardian.url()).toBe('about:blank');
  expect(guardian.isClosed()).toBe(false);
  const reopened = await context.newPage();
  expect(context.pages()).toEqual([guardian, reopened]);
  await openLocalPage(context, reopened);
  expectBlob(await storedBlob(reopened, 'closed-page-document'), text);
  expect(reopened.isClosed()).toBe(false);
  expect(guardian.url()).toBe('about:blank');
  expect(guardian.isClosed()).toBe(false);
});
