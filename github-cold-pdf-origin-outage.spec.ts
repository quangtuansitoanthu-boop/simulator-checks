import { createHash, randomUUID } from 'node:crypto';
import { readFile, rename, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { test } from './helpers/webkit-persistent-fixtures';
import { loginAsMember, settleClient, choosePdfOnline, openReaderTools, savePdfOffline, TEST_MEMBER_ID } from './runtime-helpers';
import { principalStorageKey } from '../../src/lib/browser/principal-storage';
import { OFFLINE_PDF_DATABASE, OFFLINE_PDF_STORE, offlinePdfRecordKey } from '../../src/lib/browser/offline-pdf-database';

const title = 'Bài PDF kiểm thử E2E';
const pdfPath = '/assets/pdfs/bai-viet-mau-gieo-nhan-lanh.pdf';
const documentKey = offlinePdfRecordKey(TEST_MEMBER_ID, 'e2e-pdf');
// Read-only assertions use the existing owner contract; the test never writes progress.
const progressKey = principalStorageKey(TEST_MEMBER_ID, 'quangtu_reading_progress_v1');
const sourceManifest = '2c91b9b11751a53b9f6ded01aedff82858de6a07335d19789f7ffe786b11bacd';

async function jsonFile(path: string): Promise<Record<string, unknown> | null> {
  try { return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>; }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null; throw error; }
}

async function atomicJson(path: string, value: unknown): Promise<void> {
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(value));
  await rename(temporary, path);
}

async function control(operation: 'stop' | 'reconnect', sequence: number, info: TestInfo, startedAt: number) {
  expect(process.env.GITHUB_ACTIONS).toBe('true');
  const directory = process.env.TUTAM_PDF_ORIGIN_CONTROL;
  const nonce = process.env.TUTAM_PDF_ORIGIN_NONCE;
  expect(directory).toBeTruthy();
  expect(nonce).toMatch(/^[a-f0-9]{64}$/u);
  const owner = await jsonFile(resolve(directory!, 'owner.json'));
  expect(owner?.nonce).toBe(nonce);
  await atomicJson(resolve(directory!, `request-${sequence}.json`), { nonce, operation, sequence, requestedAt: Date.now() });
  const deadline = startedAt + info.timeout - 2000;
  while (Date.now() < deadline) {
    const ack = await jsonFile(resolve(directory!, `ack-${sequence}.json`));
    if (ack) {
      expect(ack.nonce).toBe(nonce);
      expect(ack.sequence).toBe(sequence);
      expect(ack.operation).toBe(operation);
      expect(ack.status, JSON.stringify(ack)).toBe('ok');
      expect(ack.phase).toBe(operation === 'stop' ? 'DOWN' : 'RECONNECTED');
      return ack;
    }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(`Controller did not acknowledge ${operation} within the existing test budget`);
}

async function offlineRecord(page: Page) {
  return page.evaluate(async ({ databaseName, store, key }) => {
    const opening = indexedDB.open(databaseName, 1);
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(opening.error);
    });
    try {
      const record = await new Promise<{ status: string; sizeBytes: number; checksum: string; blob: Blob } | null>((resolve, reject) => {
        const request = db.transaction(store, 'readonly').objectStore(store).get(key);
        request.onsuccess = () => resolve(request.result ?? null);
        request.onerror = () => reject(request.error);
      });
      if (!record) return null;
      const hash = await crypto.subtle.digest('SHA-256', await record.blob.arrayBuffer());
      return { status: record.status, size: record.sizeBytes, blobSize: record.blob.size,
        checksum: record.checksum, actualChecksum: [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, '0')).join('') };
    } finally { db.close(); }
  }, { databaseName: OFFLINE_PDF_DATABASE, store: OFFLINE_PDF_STORE, key: documentKey });
}

async function progress(page: Page) {
  return page.evaluate(key => {
    const rows = JSON.parse(localStorage.getItem(key) || '[]') as { contentKind: string; contentId: string; position: { page?: number } }[];
    return rows.find(row => row.contentKind === 'pdf' && row.contentId === 'e2e-pdf')?.position ?? null;
  }, progressKey);
}

async function openPdf(page: Page): Promise<Locator> {
  await page.goto('/kinh-sach');
  await settleClient(page);
  await page.getByRole('button', { name: `Đọc ngay ${title}`, exact: true }).click();
  const reader = page.getByRole('dialog', { name: title, exact: true });
  await expect(reader).toBeVisible();
  await choosePdfOnline(page);
  return reader;
}

async function renderedPageTwo(reader: Locator): Promise<void> {
  const canvas = reader.locator('.pdf-page[data-page="2"] canvas');
  await expect(canvas).toHaveAttribute('data-rendered', /\d+/u);
  expect(await canvas.evaluate((element: HTMLCanvasElement) => {
    if (element.width <= 100 || element.height <= 100) return false;
    const context = element.getContext('2d');
    if (!context) return false;
    const data = context.getImageData(0, 0, element.width, element.height).data;
    const first = data.slice(0, 4);
    for (let offset = 0; offset < data.length; offset += 4) {
      if (data[offset] !== first[0] || data[offset + 1] !== first[1] || data[offset + 2] !== first[2]) return true;
    }
    return false;
  })).toBe(true);
}

test('current app cold PDF survives owned origin outage and authenticated reconnect', async ({ page, context, browserName }, info) => {
  const startedAt = Date.now();
  expect(browserName).toBe('webkit');
  const guardian = context.pages()[0];
  expect(guardian).not.toBe(page);
  expect(guardian.url()).toBe('about:blank');
  const bytes = await readFile(`assets${pdfPath.slice('/assets'.length)}`);
  const checksum = createHash('sha256').update(bytes).digest('hex');
  const expectedRecord = { status: 'complete', size: bytes.length, blobSize: bytes.length, checksum, actualChecksum: checksum };
  const evidence: Record<string, unknown> = { outageKind: 'workflow_owned_origin_process_group_stopped',
    browserOfflineEmulation: false, equivalentToAirplaneMode: false, sourceManifest,
    privateCanvasDuringOutageClaimed: false, pdfChecksum: checksum, browserVersion: context.browser()?.version() };
  let cold: Page | undefined;
  try {
    await loginAsMember(page);
    await page.goto('/ca-nhan?tab=settings');
    await settleClient(page);
    const font = page.getByRole('group', { name: 'Cỡ chữ giao diện', exact: true });
    if (!await font.isVisible()) await page.locator('#settings-display > summary').click();
    await expect(font.locator('output')).toHaveText('100%');
    await font.getByRole('button', { name: 'Tăng cỡ chữ giao diện', exact: true }).click();
    await expect(font.locator('output')).toHaveText('110%');
    const interfaceFont = await page.locator('body').evaluate(element => getComputedStyle(element).fontSize);
    const reader = await openPdf(page);
    await openReaderTools(page);
    await reader.getByRole('combobox', { name: 'Bố cục trang', exact: true }).selectOption('single');
    await reader.getByRole('combobox', { name: 'Nền giấy', exact: true }).selectOption('gray');
    const number = reader.getByRole('spinbutton', { name: 'Số trang PDF', exact: true });
    await number.fill('2');
    await number.press('Enter');
    await expect(number).toHaveValue('2');
    await renderedPageTwo(reader);
    const manager = await savePdfOffline(page);
    await expect(manager).toContainText('Sẵn sàng khi mất mạng.');
    await expect.poll(() => offlineRecord(page)).toEqual(expectedRecord);
    await expect.poll(async () => (await progress(page))?.page).toBe(2);
    const savedProgress = await progress(page);
    await page.evaluate(async path => {
      for (const name of await caches.keys()) await (await caches.open(name)).delete(path);
    }, pdfPath);
    await page.evaluate(async () => { await navigator.serviceWorker.ready; });
    expect(await page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
    await info.attach('downloaded-before-origin-outage', { body: await page.screenshot(), contentType: 'image/png' });
    await page.close();
    expect(context.pages()).toEqual([guardian]);
    cold = await context.newPage();
    const down = await control('stop', 1, info, startedAt);
    evidence.stopAcknowledgement = { ...down, nonce: undefined };
    expect(down.testAlive).toBe(true);
    expect(down.proxyAlive).toBe(true);
    expect(down.port8787Refused).toBe(true);
    const navigationStartedAt = Date.now();
    const response = await cold.goto('/?article=e2e-pdf');
    const navigationCompletedAt = Date.now();
    expect(response?.status()).toBe(200);
    expect(response?.fromServiceWorker()).toBe(true);
    await settleClient(cold);
    const health = await cold.evaluate(async () => {
      try { const response = await fetch('/api/health', { cache: 'no-store' }); return { failed: false, status: response.status }; }
      catch (error) { return { failed: true, error: String(error) }; }
    });
    expect(health.failed).toBe(true);
    const reopened = cold.getByRole('dialog', { name: title, exact: true });
    await expect(cold.getByRole('heading', { name: 'Quý Phật Tử', exact: true })).toBeVisible();
    await cold.getByRole('link', { name: 'Trang cá nhân & Cài đặt', exact: true }).click();
    await cold.locator('.cn-tab-theme-auth').click();
    await expect(cold).toHaveURL(/\/ca-nhan\?tab=account/);
    await expect(cold.getByRole('button', { name: 'Đăng nhập bằng Google', exact: true })).toBeVisible();
    await expect(reopened).toHaveCount(0);
    const retained = await offlineRecord(cold);
    expect(retained).toEqual(expectedRecord);
    expect(await progress(cold)).toEqual(savedProgress);
    await expect(cold.locator('body')).toHaveCSS('font-size', interfaceFont);
    expect(guardian.isClosed()).toBe(false);
    const proof = { nonce: process.env.TUTAM_PDF_ORIGIN_NONCE, phase: 'COLD_VERIFIED', sourceManifest,
      writtenAt: Date.now(), stopSequence: 1, navigationStartedAt, navigationCompletedAt,
      navigation: { status: response?.status(), fromServiceWorker: response?.fromServiceWorker() },
      health, privatePdfRetained: true, progressRetained: true, guestLoginGateVerified: true,
      fontRetained: true, retained, progress: savedProgress };
    await atomicJson(resolve(process.env.TUTAM_PDF_ORIGIN_CONTROL!, 'cold-proof.json'), proof);
    evidence.coldProof = { ...proof, nonce: undefined };
    await info.attach('cold-origin-down-login-gate', { body: await cold.screenshot(), contentType: 'image/png' });
    const restored = await control('reconnect', 2, info, startedAt);
    expect(restored.persistenceId).toBe(down.persistenceId);
    expect(restored.runtimePgid).not.toBe(down.runtimePgid);
    evidence.reconnectAcknowledgement = { ...restored, nonce: undefined };
    await loginAsMember(cold);
    await cold.goto('/?article=e2e-pdf');
    await settleClient(cold);
    await expect(reopened).toBeVisible();
    await openReaderTools(cold);
    await expect(reopened.getByRole('spinbutton', { name: 'Số trang PDF', exact: true })).toHaveValue('2');
    await expect(reopened.getByRole('combobox', { name: 'Nền giấy', exact: true })).toHaveValue('gray');
    await renderedPageTwo(reopened);
    expect(await offlineRecord(cold)).toEqual(expectedRecord);
    await cold.locator('.pdf-offline-main-btn').click();
    await expect(reopened.getByRole('region', { name: 'Quản lý PDF ngoại tuyến', exact: true })).toContainText('Sẵn sàng khi mất mạng.');
    await info.attach('authenticated-pdf-after-origin-reconnect', { body: await cold.screenshot(), contentType: 'image/png' });
    await reopened.getByRole('button', { name: 'Xóa và giải phóng', exact: true }).click();
    await expect(cold.locator('.pdf-offline-main-btn')).toContainText('Tải để đọc offline');
    expect(await offlineRecord(cold)).toBeNull();
    await cold.keyboard.press('Escape');
    await cold.goto('/ca-nhan?tab=settings');
    await settleClient(cold);
    const restoredFont = cold.getByRole('group', { name: 'Cỡ chữ giao diện', exact: true });
    if (!await restoredFont.isVisible()) await cold.locator('#settings-display > summary').click();
    await expect(restoredFont.locator('output')).toHaveText('110%');
    evidence.completed = true;
  } finally {
    await info.attach('owned-origin-outage-result', { contentType: 'application/json', body: Buffer.from(JSON.stringify(evidence, null, 2)) });
    if (cold && !cold.isClosed()) await cold.close();
  }
});
