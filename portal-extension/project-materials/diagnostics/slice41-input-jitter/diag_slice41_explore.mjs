import { chromium } from 'playwright-core';

const BASE = process.env.PORTAL_DIAG_BASE || 'http://172.16.10.180';
const PORTAL = BASE + '/portal';
const PORTAL_HOST = new URL(BASE).hostname;
const ADMIN_USERNAME = process.env.PORTAL_DIAG_USERNAME || 'admin';
const ADMIN_PASSWORD = process.env.PORTAL_DIAG_PASSWORD;
if (!ADMIN_PASSWORD) { console.error('FATAL: set PORTAL_DIAG_PASSWORD'); process.exit(2); }

// 1. login via API, grab cookie
const loginResp = await fetch(PORTAL + '/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD }),
});
const loginJson = await loginResp.json();
console.log('[explore] login:', loginResp.status, JSON.stringify(loginJson));
const setCookie = loginResp.headers.get('set-cookie') || '';
const cookieMatch = setCookie.match(/portal_session=([^;]+)/);
if (!cookieMatch) { console.error('no portal_session cookie'); process.exit(1); }
const portalSession = cookieMatch[1];
console.log('[explore] cookie portal_session len:', portalSession.length);

// 2. launch browser with cookie
const CHROME = process.env.CHROME_BIN ||
  '/Users/xijuangu/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
const browser = await chromium.launch({ headless: true, executablePath: CHROME });
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
});
await context.addCookies([{
  name: 'portal_session', value: portalSession,
  domain: PORTAL_HOST, path: '/',
}]);
const page = await context.newPage();

// 3. go to share page detail (loads iframe)
console.log('[explore] goto share page detail');
await page.goto(PORTAL + '/share-pages/sp_default', { waitUntil: 'networkidle' });
await page.waitForTimeout(2500);

// 4. find iframe
const iframeEl = await page.$('iframe');
if (!iframeEl) { console.error('no iframe found'); await browser.close(); process.exit(1); }
const frame = await iframeEl.contentFrame();
console.log('[explore] iframe url:', frame.url());

// 5. explore: find textareas, inputs, buttons
const info = await frame.evaluate(() => {
  const result = { textareas: [], inputs: [], sendBtns: [], contentEditable: [] };
  document.querySelectorAll('textarea').forEach((el, i) => {
    const r = el.getBoundingClientRect();
    result.textareas.push({ i, cls: el.className, ph: el.placeholder, id: el.id,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height } });
  });
  document.querySelectorAll('input[type="text"], input:not([type])').forEach((el, i) => {
    const r = el.getBoundingClientRect();
    result.inputs.push({ i, cls: el.className, ph: el.placeholder, id: el.id,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height } });
  });
  document.querySelectorAll('[contenteditable="true"]').forEach((el, i) => {
    const r = el.getBoundingClientRect();
    result.contentEditable.push({ i, cls: el.className, tag: el.tagName,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height } });
  });
  const allBtns = [...document.querySelectorAll('button')];
  result.allBtnCount = allBtns.length;
  result.bottomBtns = allBtns.filter(b => {
    const r = b.getBoundingClientRect();
    return r.y > 600;
  }).map((b, i) => ({
    i, txt: (b.textContent || '').trim().slice(0, 20),
    aria: b.getAttribute('aria-label') || '',
    title: b.getAttribute('title') || '',
    cls: b.className.slice(0, 50),
  }));
  return result;
});
console.log('[explore] iframe DOM info:\n' + JSON.stringify(info, null, 2));

const bodyInfo = await frame.evaluate(() => {
  const b = document.body;
  const r = b.getBoundingClientRect();
  return { scrollHeight: b.scrollHeight, clientHeight: b.clientHeight,
    bodyRect: { x: r.x, y: r.y, w: r.width, h: r.height } };
});
console.log('[explore] body info:', JSON.stringify(bodyInfo));

await browser.close();
