/**
 * Phase 4 probe v2 — 用 CSS !important 真正拦截 textarea 高度变化。
 * 模式:
 *   observe  — 纯观察
 *   css-fix  — 注入 CSS: textarea { height:48px !important; min-height:48px !important; }
 *   no-scroll — no-op scrollTo(测 H2)
 *   css-fix+no-scroll — 同时干预
 */
import { chromium } from 'playwright-core';

const BASE = process.env.PORTAL_DIAG_BASE || 'http://172.16.10.180';
const PORTAL = BASE + '/portal';
const PORTAL_HOST = new URL(BASE).hostname;
const ADMIN_USERNAME = process.env.PORTAL_DIAG_USERNAME || 'admin';
const ADMIN_PASSWORD = process.env.PORTAL_DIAG_PASSWORD;
if (!ADMIN_PASSWORD) { console.error('FATAL: set PORTAL_DIAG_PASSWORD'); process.exit(2); }
const CHROME = process.env.CHROME_BIN ||
  '/Users/xijuangu/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
const QUESTION = '请详细介绍人工智能的发展历史,要详细一点,从图灵讲到现在,至少2000字';
const MODE = process.env.PROBE_MODE || 'observe';

const loginResp = await fetch(PORTAL + '/login', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD }),
});
const cookieMatch = (loginResp.headers.get('set-cookie') || '').match(/portal_session=([^;]+)/);
if (!cookieMatch) { console.error('FATAL: no cookie'); process.exit(2); }
const portalSession = cookieMatch[1];

const browser = await chromium.launch({ headless: true, executablePath: CHROME });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await context.addCookies([{ name: 'portal_session', value: portalSession, domain: PORTAL_HOST, path: '/' }]);
const page = await context.newPage();

await page.goto(PORTAL + '/share-pages/sp_default', { waitUntil: 'networkidle' });
await page.waitForTimeout(2500);
const frame = await (await page.$('iframe')).contentFrame();
await frame.waitForSelector('textarea[placeholder*="message"]', { timeout: 15000 });
console.log(`[probe2:${MODE}] iframe ready`);

// === 干预注入 ===
if (MODE === 'css-fix' || MODE === 'css-fix+no-scroll') {
  // 注入 CSS 强制 textarea 高度(覆盖 inline style)
  await frame.addStyleTag({ content: `
    textarea[placeholder*="message"] {
      height: 48px !important;
      min-height: 48px !important;
      max-height: 48px !important;
    }
  `});
  console.log(`[probe2:${MODE}] CSS fix injected (force height=48px)`);
}

// 安装观察器 + 可选 no-op scrollTo
const probeSetup = await frame.evaluate((mode) => {
  const ta = document.querySelector('textarea[placeholder*="message"]');
  const stats = {
    heightWrites: 0, heightAutoWrites: 0, heightPxWrites: 0,
    heightValues: [], scrollToCalls: 0, scrollToTimestamps: [],
    rafCalls: 0, domMutations: 0,
  };
  window.__probeStats = stats;

  // 观察式:MutationObserver 计数 style 变化
  const mo = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.attributeName === 'style') {
        stats.heightWrites++;
        const h = ta.style.height;
        if (h === 'auto') stats.heightAutoWrites++;
        else if (h && h.endsWith('px')) stats.heightPxWrites++;
        if (stats.heightValues.length < 40) stats.heightValues.push({ t: +performance.now().toFixed(0), h });
      }
    }
  });
  mo.observe(ta, { attributes: true, attributeFilter: ['style'] });

  // 观察式:计数 requestAnimationFrame
  const origRaf = window.requestAnimationFrame;
  window.requestAnimationFrame = function(cb) { stats.rafCalls++; return origRaf.call(window, cb); };

  // 观察式 + H2 干预:scrollTo
  const origScrollTo = Element.prototype.scrollTo;
  Element.prototype.scrollTo = function(...args) {
    stats.scrollToCalls++;
    if (stats.scrollToTimestamps.length < 30) stats.scrollToTimestamps.push(+performance.now().toFixed(0));
    if (mode === 'no-scroll' || mode === 'css-fix+no-scroll') return; // no-op
    return origScrollTo.apply(this, args);
  };
  window.__origScrollTo = origScrollTo;

  // 观察式:消息容器 DOM mutation
  const msgContainer = document.querySelector('[class*="overflow-auto"]');
  if (msgContainer) {
    const mo2 = new MutationObserver(() => { stats.domMutations++; });
    mo2.observe(msgContainer, { childList: true, subtree: true, characterData: true });
  }
  return { ok: true, mode };
});
console.log(`[probe2:${MODE}] setup:`, JSON.stringify(probeSetup));

// 采样
async function samplePositions(durationMs) {
  return await frame.evaluate(async ({ d, interval }) => {
    const ta = document.querySelector('textarea[placeholder*="message"]');
    if (!ta) return [];
    const samples = [];
    const t0 = performance.now();
    return new Promise((resolve) => {
      const iv = setInterval(() => {
        const r = ta.getBoundingClientRect();
        samples.push({ t: +(performance.now() - t0).toFixed(1), y: +r.y.toFixed(2), h: +r.height.toFixed(2) });
        if (performance.now() - t0 > d) { clearInterval(iv); resolve(samples); }
      }, interval);
    });
  }, { d: durationMs, interval: 8 });
}

function analyze(samples, label) {
  if (!samples.length) return { jitter: 0, yRange: 0, hRange: 0 };
  const ys = samples.map(s => s.y), hs = samples.map(s => s.h);
  let jitter = 0, dirCh = 0;
  for (let i = 1; i < ys.length; i++) if (Math.abs(ys[i] - ys[i-1]) > 1.5) jitter++;
  for (let i = 2; i < ys.length; i++) {
    const d1 = ys[i]-ys[i-1], d2 = ys[i-1]-ys[i-2];
    if (Math.abs(d1)>0.5 && Math.abs(d2)>0.5 && d1*d2<0) dirCh++;
  }
  const m = { label, count: samples.length, yRange: +(Math.max(...ys)-Math.min(...ys)).toFixed(2),
    hRange: +(Math.max(...hs)-Math.min(...hs)).toFixed(2), jitter, dirCh };
  console.log(`[probe2:${MODE}] ${label}: ${JSON.stringify(m)}`);
  return m;
}

const baseline = await samplePositions(2000);
analyze(baseline, 'baseline');

const ta = await frame.$('textarea[placeholder*="message"]');
await ta.fill(QUESTION);
await page.waitForTimeout(300);
const sendBtn = await frame.$('button:has-text("Send message")');
const streamP = samplePositions(12000);
await sendBtn.click();
const stream = await streamP;
const streamM = analyze(stream, 'streaming');

const stats = await frame.evaluate(() => {
  if (window.__origScrollTo) Element.prototype.scrollTo = window.__origScrollTo;
  return window.__probeStats;
});
console.log(`\n[probe2:${MODE}] === PROBE STATS ===`);
console.log(`[probe2:${MODE}] heightWrites: ${stats.heightWrites} (auto:${stats.heightAutoWrites} px:${stats.heightPxWrites})`);
console.log(`[probe2:${MODE}] rafCalls: ${stats.rafCalls}`);
console.log(`[probe2:${MODE}] scrollToCalls: ${stats.scrollToCalls}`);
console.log(`[probe2:${MODE}] domMutations: ${stats.domMutations}`);
if (stats.heightValues.length) console.log(`[probe2:${MODE}] heightValues: ${JSON.stringify(stats.heightValues.slice(0,15))}`);

console.log(`\n[probe2:${MODE}] === VERDICT ===`);
console.log(`[probe2:${MODE}] jitter=${streamM.jitter} yRange=${streamM.yRange} hRange=${streamM.hRange}`);
const isRed = streamM.jitter > 5;
console.log(`[probe2:${MODE}] RED: ${isRed ? 'YES' : 'NO (suppressed)'}`);

await browser.close();
process.exit(0);
