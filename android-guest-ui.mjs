// Actual guest UI in the installed QA WebView; no stores, seeded state or app hooks.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';

const root = process.cwd();
const require = createRequire(path.join(root, 'package.json'));
const { chromium, expect } = require('@playwright/test');
const packageId = 'vn.quangtu.tutam.securityqa';
const serial = process.env.ANDROID_SERIAL || 'emulator-5554';
const adbExecutable = process.env.ADB || 'adb';
const evidence = path.resolve('build/android-device-evidence/guest-ui');
await fs.mkdir(evidence, { recursive: true });
if (process.env.GITHUB_ACTIONS !== 'true') throw new Error('Only the approved ephemeral CI emulator may be controlled');
const events = [], pageErrors = [], consoleErrors = [], outcomes = [];
let browser, page;
const adb = (...args) => {
  const result = spawnSync(adbExecutable, ['-s', serial, ...args], { timeout: 45_000, maxBuffer: 12 * 1024 * 1024 });
  if (result.status !== 0) throw new Error('adb ' + args.slice(0, 3).join(' ') + ': ' + result.stderr?.toString());
  return result.stdout;
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function offline() {
  const connectivity = adb('shell', 'dumpsys', 'connectivity').toString();
  if (!connectivity.includes('Active default network: none')) throw new Error('The QA device has an active default network');
  for (const [key, value] of [['airplane_mode_on', '1'], ['wifi_on', '0'], ['mobile_data', '0']]) {
    if (adb('shell', 'settings', 'get', 'global', key).toString().trim() !== value) throw new Error('Offline state changed: ' + key);
  }
  return connectivity;
}
async function screenshot(name) {
  await fs.writeFile(path.join(evidence, name + '-native.png'), adb('exec-out', 'screencap', '-p'));
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(evidence, name + '-webview.png') });
  }
}
async function connect() {
  offline();
  adb('shell', 'am', 'start', '-n', packageId + '/vn.quangtu.tutam.StartupActivity');
  let pid;
  for (let attempt = 0; attempt < 60; attempt++) {
    const result = spawnSync(adbExecutable, ['-s', serial, 'shell', 'pidof', '-s', packageId], { timeout: 5000 });
    pid = result.stdout?.toString().trim();
    if (/^\d+$/.test(pid || '')) {
      const sockets = adb('shell', 'cat', '/proc/net/unix').toString();
      if (sockets.includes('webview_devtools_remote_' + pid)) break;
    }
    await pause(500);
  }
  if (!/^\d+$/.test(pid || '')) throw new Error('QA app PID unavailable');
  adb('forward', 'tcp:9222', 'localabstract:webview_devtools_remote_' + pid);
  let lastError;
  for (let attempt = 0; attempt < 30; attempt++) {
    try {
      browser = await chromium.connectOverCDP('http://127.0.0.1:9222', { timeout: 5000 });
      break;
    } catch (error) { lastError = error; await pause(500); }
  }
  if (!browser) throw lastError || new Error('QA WebView CDP unavailable');
  const context = browser.contexts()[0];
  if (!context) throw new Error('CDP did not expose the installed app context');
  for (let attempt = 0; attempt < 60; attempt++) {
    page = context.pages().find(item => item.url().startsWith('https://localhost'));
    if (page) break;
    await pause(500);
  }
  if (!page) throw new Error('The bundled https://localhost page is unavailable');
  page.setDefaultTimeout(15_000);
  page.on('pageerror', error => pageErrors.push({ time: new Date().toISOString(), message: error.message, stack: error.stack }));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push({ time: new Date().toISOString(), text: message.text() }); });
  await expect(page.getByRole('link', { name: 'Cá nhân', exact: true }).filter({ visible: true }).last()).toBeVisible({ timeout: 45_000 });
  events.push({ action: 'connect', pid, url: page.url() });
}
async function nav(label) {
  const footer = page.getByRole('navigation', { name: /Điều hướng nhanh|Quick navigation/u });
  await expect(footer).toBeVisible();
  await footer.getByRole('link', { name: label, exact: true }).click();
}
async function back() { adb('shell', 'input', 'keyevent', '4'); await pause(300); }
async function flow(name, run) {
  try {
    offline();
    await run();
    await screenshot(name);
    outcomes.push({ name, status: 'PASS' });
  } catch (error) {
    outcomes.push({ name, status: 'FAIL', error: error.stack || String(error) });
    try { await screenshot(name + '-failure'); } catch (capture) { events.push({ screenshotError: String(capture) }); }
    throw error;
  } finally {
    await fs.writeFile(path.join(evidence, 'outcomes.json'), JSON.stringify(outcomes, null, 2));
  }
}

let failure;
try {
  await connect();
  await flow('01-home', async () => {
    await expect(page.getByRole('link', { name: 'Tu tập Phật pháp — Trang chủ', exact: true })).toBeVisible();
    const later = page.getByRole('button', { name: 'Để sau', exact: true }).filter({ visible: true });
    if (await later.count()) await later.first().click();
    await expect(page.getByText('LỊCH PHẬT GIÁO', { exact: true })).toBeVisible();
  });
  await flow('02-library-search-reading-font', async () => {
    await nav('Kinh sách');
    const search = page.getByRole('searchbox', { name: 'Tìm kiếm bài viết', exact: true });
    await expect(search).toBeVisible();
    await search.fill('Kinh Dia Tang Bo Tat Bon Nguyen Giang Giai');
    await page.getByRole('button', { name: 'Đọc ngay Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải', exact: true }).click();
    await page.getByRole('button', { name: /Duyên Khởi Của Pháp Hội Giảng Kinh/u }).filter({ visible: true }).first().click();
    await expect(page.locator('.reader-content')).toContainText('Tất cả mọi nơi đều là đạo tràng');
    await page.getByRole('button', { name: 'Chữ lớn hơn', exact: true }).click();
    await expect(page.locator('.reader-modal').getByText('16px', { exact: true })).toBeVisible();
  });
  await flow('03-reader-android-back', async () => {
    await back();
    await expect(page.getByText('LẦN TRƯỚC BẠN ĐANG ĐỌC', { exact: true })).toBeVisible();
    await back();
    await expect(page.getByRole('button', { name: 'Đọc ngay Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải', exact: true })).toBeVisible();
  });
  await flow('04-settings-dark', async () => {
    await nav('Cá nhân');
    await page.getByRole('button', { name: /^Cài đặt ứng dụng /u }).click();
    await page.getByRole('button', { name: /^Giao diện & ngôn ngữ /u }).click();
    await page.getByRole('button', { name: 'Tối', exact: true }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });
  await flow('05-dark-persists-cold-process', async () => {
    await browser.close().catch(() => {});
    browser = undefined; page = undefined;
    adb('shell', 'am', 'force-stop', packageId);
    await connect();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });
  await flow('06-rosary-native-back-count', async () => {
    await nav('Tu tập');
    await page.getByRole('button', { name: /^Lần tràng hạt /u }).click();
    await page.getByRole('button', { name: 'Bắt đầu 🙏', exact: true }).click();
    await expect(page.getByText('0/108', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Niệm một hạt', exact: true }).click();
    await expect(page.getByText('1/108', { exact: true })).toBeVisible();
    await back();
    await expect(page.getByText('Chế Độ Tu Tập', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: /^Lần tràng hạt /u }).click();
    await page.getByRole('button', { name: 'Bắt đầu 🙏', exact: true }).click();
    await expect(page.getByText('1/108', { exact: true })).toBeVisible();
    await back();
  });
  await flow('07-copying-actual-user-input-prefix', async () => {
    adb('shell', 'am', 'start', '-n', packageId + '/vn.quangtu.tutam.MainActivity', '-a', 'android.intent.action.VIEW', '-d', 'vn.quangtu.tutam:///tu-tap/chep-kinh');
    const book = page.locator('nav.books button').filter({ hasText: /Kinh Địa Tạng Bồ Tát Bổn Nguyện/u });
    await expect(book).toHaveCount(1);
    await expect(book).toBeEnabled();
    await book.click();
    await page.getByTestId('confirm-copying-guide').click();
    const editor = page.getByRole('textbox', { name: 'Tự gõ bản chép của bạn', exact: true });
    await editor.click();
    await editor.pressSequentially('TINH KHAU', { delay: 80 });
    await expect(editor).toContainText('TINH KHAU');
  });
  if (pageErrors.length) throw new Error('Guest UI emitted uncaught JavaScript errors');
} catch (error) {
  failure = error;
} finally {
  try {
    await fs.writeFile(path.join(evidence, 'final-connectivity.txt'), offline());
    await fs.writeFile(path.join(evidence, 'logcat.txt'), adb('logcat', '-d', '-v', 'threadtime'));
    await fs.writeFile(path.join(evidence, 'crash-logcat.txt'), adb('logcat', '-d', '-b', 'crash'));
    adb('shell', 'am', 'force-stop', packageId);
    adb('forward', '--remove', 'tcp:9222');
  } catch (error) { failure ||= error; }
  if (browser) await browser.close().catch(() => {});
  await fs.writeFile(path.join(evidence, 'pageerrors.json'), JSON.stringify(pageErrors, null, 2));
  await fs.writeFile(path.join(evidence, 'console-errors.json'), JSON.stringify(consoleErrors, null, 2));
  await fs.writeFile(path.join(evidence, 'events.json'), JSON.stringify(events, null, 2));
  await fs.writeFile(path.join(evidence, 'summary.json'), JSON.stringify({
    scope: 'Seven bounded offline guest UI flows; not exhaustive Android functionality',
    package: packageId, expectedFlows: 7, passedFlows: outcomes.filter(item => item.status === 'PASS').length,
    completeAndPassed: !failure && outcomes.length === 7,
    outcomes, pageErrorCount: pageErrors.length, failure: failure?.stack || null,
  }, null, 2));
}
if (failure) throw failure;
