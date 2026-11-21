// Artifact-only probe. Run from an isolated CI checkout after apply-current.py.
// Uses existing locked dependencies; serves only the eight original test modules.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

const app = await fs.realpath(process.argv[2] || '.');
const output = path.resolve(app, process.argv[3] || 'build/github-ui-evidence/idb-probe');
if (!output.startsWith(app + path.sep)) throw new Error('Evidence output must stay inside the app checkout');
await fs.mkdir(output, { recursive: true });
const require = createRequire(path.join(app, 'package.json'));
const { webkit } = require('@playwright/test');
const ts = require('typescript');
const modules = [
  'src/lib/browser/offline-content-packages', 'src/lib/browser/offline-content-index',
  'src/lib/browser/ios-content-policy', 'src/lib/browser/native-platform',
  'src/lib/domain/ios-content-policy', 'src/lib/domain/content-update-model',
  'src/lib/domain/online-only-policy', 'src/lib/contracts/content-updates',
];
const report = { startedAt: new Date().toISOString(), platform: process.platform,
  node: process.version, playwright: require('@playwright/test/package.json').version,
  sourceSha256: {}, modes: [], blockedRequests: [], httpRequests: [] };
const compiled = new Map();
for (const name of modules) {
  const bytes = await fs.readFile(path.join(app, name + '.ts'));
  report.sourceSha256[name + '.ts'] = crypto.createHash('sha256').update(bytes).digest('hex');
  // Match the original offline-content-index.spec.ts isolated HTTP harness.
  const code = ts.transpileModule(bytes.toString('utf8'), { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
  } }).outputText.replaceAll("'$lib/", "'/src/lib/").replaceAll('"$lib/', '"/src/lib/')
    .replaceAll('import.meta.env.VITE_CONTENT_PLATFORM', '"web"');
  compiled.set('/' + name, code);
}
const server = http.createServer((request, response) => {
  const name = new URL(request.url || '/', 'http://isolated.invalid').pathname;
  report.httpRequests.push({ method: request.method, path: name });
  response.setHeader('cache-control', 'no-store');
  response.setHeader('content-security-policy', "default-src 'none'; script-src 'self'; connect-src 'self'");
  if (name === '/') {
    response.writeHead(200, { 'content-type': 'text/html' });
    response.end('<!doctype html><title>Local WebKit IndexedDB diagnostic</title>');
  } else if (compiled.has(name.replace(/\.ts$/, ''))) {
    response.writeHead(200, { 'content-type': 'text/javascript' });
    response.end(compiled.get(name.replace(/\.ts$/, '')));
  } else { response.writeHead(404); response.end(); }
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
const origin = `http://127.0.0.1:${server.address().port}`;
report.origin = origin;

// Instrument listeners without replacing the application's IDB event handlers.
function installInstrumentation() {
  window.__idbEvents = [];
  window.__probePhase = 'initializing';
  let requestSequence = 0, transactionSequence = 0;
  const transactions = new WeakMap();
  const error = value => value == null ? { value: value ?? null } : {
    name: value.name ?? typeof value, message: value.message ?? String(value), code: value.code ?? null,
  };
  const emit = (kind, details) => window.__idbEvents.push({ time: performance.now(),
    phase: window.__probePhase, kind, ...details });
  const describe = value => value instanceof Blob ? { type: 'Blob', bytes: value.size, mime: value.type }
    : typeof value === 'string' ? { type: 'string', characters: value.length }
    : value && typeof value === 'object' ? { type: Object.prototype.toString.call(value), keys: Object.keys(value),
      chapterData: value.chapterData instanceof Blob ? { type: 'Blob', bytes: value.chapterData.size }
        : { type: typeof value.chapterData } } : { type: typeof value };
  const listen = (request, operation, details = {}) => {
    const id = ++requestSequence;
    emit('request-start', { id, operation, ...details });
    request.addEventListener('success', () => emit('request-success', { id, operation,
      result: describe(request.result), ...details }));
    request.addEventListener('error', () => emit('request-error', { id, operation,
      error: error(request.error), ...details }));
    return request;
  };
  const originalTransaction = IDBDatabase.prototype.transaction;
  IDBDatabase.prototype.transaction = function(...args) {
    try {
      const transaction = originalTransaction.apply(this, args), id = ++transactionSequence;
      transactions.set(transaction, id);
      const base = { id, database: this.name, stores: Array.from(transaction.objectStoreNames), mode: transaction.mode };
      emit('transaction-start', base);
      for (const type of ['complete', 'abort', 'error']) transaction.addEventListener(type, event => {
        emit('transaction-' + type, { ...base, error: error(transaction.error),
          targetError: error(event.target?.error), targetType: event.target?.constructor?.name });
      });
      return transaction;
    } catch (cause) { emit('transaction-throw', { error: error(cause) }); throw cause; }
  };
  for (const method of ['put', 'add', 'get', 'getAll']) {
    const original = IDBObjectStore.prototype[method];
    IDBObjectStore.prototype[method] = function(...args) {
      const details = { store: this.name, transaction: transactions.get(this.transaction),
        ...(method === 'put' || method === 'add' ? { value: describe(args[0]) } : {}) };
      try { return listen(original.apply(this, args), method, details); }
      catch (cause) { emit('request-throw', { operation: method, error: error(cause), ...details }); throw cause; }
    };
  }
  const originalOpen = IDBFactory.prototype.open;
  IDBFactory.prototype.open = function(...args) {
    const request = listen(originalOpen.apply(this, args), 'open', { database: args[0] });
    request.addEventListener('upgradeneeded', event => emit('upgrade', {
      database: args[0], oldVersion: event.oldVersion, newVersion: event.newVersion,
    }));
    request.addEventListener('blocked', () => emit('open-blocked', { database: args[0] }));
    return request;
  };
  window.addEventListener('error', event => emit('window-error', { message: event.message, error: error(event.error) }));
  window.addEventListener('unhandledrejection', event => emit('unhandled-rejection', { error: error(event.reason) }));
}

async function evaluateCase(page, kind) {
  return page.evaluate(async kind => {
    window.__probePhase = kind;
    if (kind === 'product-first-save') {
      // Same web policy, forced absence of Web Locks and payload as the original spec.
      Object.defineProperty(navigator, 'locks', { value: undefined, configurable: true });
      const api = await import('/src/lib/browser/offline-content-packages');
      const row = { id: 'idb-probe', tieu_de: 'idb-probe', has_muc_luc: 1,
        trang_thai: 'Hoàn thành', updated_at: '2026-10-01', noi_dung: 'one' };
      await api.saveContentPackage({ kind: 'article', group: 'book', mode: 'full', version: 'one', row },
        row, [{ id: 'idb-probe-chapter', noi_dung: 'one' }]);
      const index = await import('/src/lib/browser/offline-content-index');
      const saved = await api.savedContentItems(true), item = await index.readIndexedContentItem('article:idb-probe');
      if (saved.length !== 1 || !item) throw new Error('Product save did not retain its record');
      const chapters = JSON.parse(await item.chapterData.text());
      if (chapters[0]?.noi_dung !== 'one') throw new Error('Product chapter readback differs');
      return { savedIds: saved.map(item => item.row.id), chapterCount: chapters.length };
    }
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open('tutam_idb_probe_' + kind, 1);
      request.onupgradeneeded = () => request.result.createObjectStore('items', { keyPath: 'key' });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('Raw database open failed without an error'));
    });
    try {
      const chapters = [{ id: 'chapter', noi_dung: 'one' }];
      const value = kind === 'plain-json' ? { chapters }
        : kind === 'blob' ? new Blob([JSON.stringify(chapters)], { type: 'application/json' })
          : JSON.stringify(chapters);
      await new Promise((resolve, reject) => {
        const transaction = database.transaction('items', 'readwrite');
        transaction.oncomplete = resolve;
        transaction.onabort = () => reject(transaction.error ?? new Error('Raw transaction aborted with null error'));
        transaction.onerror = () => reject(transaction.error ?? new Error('Raw transaction errored with null error'));
        transaction.objectStore('items').put({ key: 'row', chapterData: value });
      });
      const result = await new Promise((resolve, reject) => {
        const request = database.transaction('items', 'readonly').objectStore('items').get('row');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error ?? new Error('Raw read failed without an error'));
      });
      const decoded = result.chapterData instanceof Blob ? JSON.parse(await result.chapterData.text())
        : typeof result.chapterData === 'string' ? JSON.parse(result.chapterData) : result.chapterData.chapters;
      if (decoded?.[0]?.noi_dung !== 'one') throw new Error('Raw chapter readback differs');
      return { recordRetained: true, valueType: kind, chapterCount: decoded.length };
    } finally { database.close(); }
  }, kind);
}

let browser, profile;
try {
  for (const mode of ['nonpersistent', 'persistent']) {
    const entry = { mode, cases: [], console: [], pageErrors: [] };
    report.modes.push(entry);
    let context;
    try {
      if (mode === 'nonpersistent') {
        browser = await webkit.launch({ headless: true });
        context = await browser.newContext({ serviceWorkers: 'block' });
      } else {
        profile = await fs.mkdtemp(path.join(os.tmpdir(), 'tutam-idb-probe-'));
        context = await webkit.launchPersistentContext(profile, { headless: true, serviceWorkers: 'block' });
      }
      entry.browserVersion = context.browser()?.version() ?? 'persistent';
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin === origin) return route.continue();
        report.blockedRequests.push({ mode, origin: url.origin, pathname: url.pathname });
        return route.abort('blockedbyclient');
      });
      await context.addInitScript(installInstrumentation);
      for (const kind of ['plain-json', 'blob', 'json-string', 'product-first-save']) {
        const page = await context.newPage(), result = { kind, events: [] };
        entry.cases.push(result);
        page.on('console', message => { if (['warning', 'error'].includes(message.type())) entry.console.push({ kind, type: message.type(), text: message.text() }); });
        page.on('pageerror', error => entry.pageErrors.push({ kind, name: error.name, message: error.message }));
        let timer;
        try {
          await page.goto(origin, { waitUntil: 'load', timeout: 15_000 });
          result.environment = await page.evaluate(() => ({ isSecureContext, origin: location.origin,
            indexedDB: typeof indexedDB, locks: typeof navigator.locks, userAgent: navigator.userAgent }));
          result.value = await Promise.race([evaluateCase(page, kind), new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error('Diagnostic case exceeded 15000ms')), 15_000);
          })]);
          result.status = 'PASSED';
        } catch (error) {
          result.status = 'FAILED'; result.error = { name: error?.name ?? typeof error,
            message: error?.message ?? String(error), stack: error?.stack ?? null };
          await page.screenshot({ path: path.join(output, `${mode}-${kind}.png`) }).catch(() => {});
        } finally {
          clearTimeout(timer);
          result.events = await page.evaluate(() => window.__idbEvents ?? []).catch(() => []);
          await fs.writeFile(path.join(output, `${mode}-${kind}.json`), JSON.stringify(result, null, 2) + '\n');
          await page.close();
        }
      }
    } catch (error) { entry.setupError = { name: error.name, message: error.message, stack: error.stack }; }
    finally { await context?.close(); await browser?.close(); browser = undefined; }
  }
} finally {
  await new Promise(resolve => server.close(resolve));
  // Retain the owned temporary profile for runner diagnostics; never delete user data.
  report.temporaryProfile = profile ?? null;
  report.finishedAt = new Date().toISOString();
  report.failedCases = report.modes.flatMap(mode => mode.cases).filter(item => item.status !== 'PASSED').length;
  report.gate = report.failedCases || report.modes.some(mode => mode.setupError) || report.blockedRequests.length ? 'FAILED' : 'PASSED';
  await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ gate: report.gate, failedCases: report.failedCases,
    modes: report.modes.map(mode => ({ mode: mode.mode, setupError: mode.setupError,
      cases: mode.cases.map(item => ({ kind: item.kind, status: item.status, error: item.error })) })) }, null, 2));
  process.exitCode = report.gate === 'PASSED' ? 0 : 1;
}
