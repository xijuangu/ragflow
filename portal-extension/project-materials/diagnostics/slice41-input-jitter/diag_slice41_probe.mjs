/**
 * Phase 4 — probe 验证假设。
 * 模式:
 *   observe  — 观察式:计数 adjustHeight(height 写次数)、scrollTo 次数、DOM mutation
 *   h1       — 干预 H1:拦截 style.height='auto' 写(阻止 textarea 塌缩)
 *   h2       — 干预 H2:no-op scrollTo(阻止滚动定时器效果)
 *   h1h2     — 同时干预 H1+H2
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

// login
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
console.log(`[probe:${MODE}] iframe ready`);

// === 注入 probe:在 frame 内安装观察/拦截器 ===
const probeSetup = await frame.evaluate((mode) => {
  const ta = document.querySelector('textarea[placeholder*="message"]');
  if (!ta) return { error: 'no textarea' };
  const stats = {
    heightWrites: 0,          // style.height 写次数
    heightAutoWrites: 0,     // style.height = 'auto' 次数
    heightPxWrites: 0,       // style.height = 'Npx' 次数
    heightValues: [],        // 记录前 30 个 height 值
    scrollToCalls: 0,        // scrollTo 调用次数
    scrollToTimestamps: [],  // scrollTo 时间戳(前 20)
    domMutations: 0,         // 消息容器内 DOM 变化次数
    mutationTimestamps: [],  // mutation 时间戳(前 20)
  };
  window.__probeStats = stats;

  // --- Probe: 拦截 style.height 写 ---
  const styleProto = HTMLElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(styleProto, 'style');
  // 用 MutationObserver 观察 textarea style 属性变化(观察式)
  const mo = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.attributeName === 'style') {
        stats.heightWrites++;
        const h = ta.style.height;
        if (h === 'auto') stats.heightAutoWrites++;
        else if (h && h.endsWith('px')) stats.heightPxWrites++;
        if (stats.heightValues.length < 30) stats.heightValues.push({ t: +performance.now().toFixed(0), h });
      }
    }
  });
  mo.observe(ta, { attributes: true, attributeFilter: ['style'] });

  // --- H1 干预:拦截 style.height = 'auto' ---
  if (mode === 'h1' || mode === 'h1h2') {
    // 保存原始 height,拦截 'auto' 写(阻止塌缩)
    const originalSetter = Object.getOwnPropertyDescriptor(CSSStyleDeclaration.prototype, 'height');
    if (originalSetter && originalSetter.set) {
      Object.defineProperty(ta.style, 'height', {
        get: originalSetter.get,
        set: function(v) {
          if (v === 'auto') {
            // 跳过 auto 塌缩,保持当前高度
            stats.heightAutoWrites++;
            return;
          }
          originalSetter.set.call(this, v);
        },
        configurable: true,
      });
    }
  }

  // --- Probe: 拦截 scrollTo ---
  const origScrollTo = Element.prototype.scrollTo;
  Element.prototype.scrollTo = function(...args) {
    if (this === ta || ta.contains(this) || this.contains?.(ta)) {
      // textarea 或其祖先/后代的 scrollTo,可能不相关
    }
    stats.scrollToCalls++;
    if (stats.scrollToTimestamps.length < 20) stats.scrollToTimestamps.push(+performance.now().toFixed(0));
    if (mode === 'h2' || mode === 'h1h2') {
      // no-op scrollTo(阻止滚动)
      return;
    }
    return origScrollTo.apply(this, args);
  };
  window.__origScrollTo = origScrollTo;

  // --- Probe: 消息容器 DOM mutation 计数 ---
  const msgContainer = document.querySelector('[class*="overflow-auto"]');
  if (msgContainer) {
    const mo2 = new MutationObserver((mutations) => {
      stats.domMutations++;
      if (stats.mutationTimestamps.length < 20) stats.mutationTimestamps.push(+performance.now().toFixed(0));
    });
    mo2.observe(msgContainer, { childList: true, subtree: true, characterData: true });
  }

  return { ok: true, mode, taFound: !!ta, msgContainerFound: !!msgContainer };
});
console.log(`[probe:${MODE}] setup:`, JSON.stringify(probeSetup));

// === 采样 textarea bbox(同 Phase 1) ===
async function samplePositions(durationMs) {
  return await frame.evaluate(async ({ d, interval }) => {
    const ta = document.querySelector('textarea[placeholder*="message"]');
    if (!ta) return [];
    const samples = [];
    const t0 = performance.now();
    return new Promise((resolve) => {
      const iv = setInterval(() => {
        const r = ta.getBoundingClientRect();
        samples.push({ t: +(performance.now() - t0).toFixed(1),
          y: +r.y.toFixed(2), h: +r.height.toFixed(2) });
        if (performance.now() - t0 > d) { clearInterval(iv); resolve(samples); }
      }, interval);
    });
  }, { d: durationMs, interval: 8 });
}

function analyze(samples, label) {
  if (!samples.length) return { jitter: 0, range: 0 };
  const ys = samples.map(s => s.y);
  const hs = samples.map(s => s.h);
  let jitter = 0, dirCh = 0;
  for (let i = 1; i < ys.length; i++) if (Math.abs(ys[i] - ys[i-1]) > 1.5) jitter++;
  for (let i = 2; i < ys.length; i++) {
    const d1 = ys[i]-ys[i-1], d2 = ys[i-1]-ys[i-2];
    if (Math.abs(d1)>0.5 && Math.abs(d2)>0.5 && d1*d2<0) dirCh++;
  }
  const m = { label, count: samples.length, yRange: +(Math.max(...ys)-Math.min(...ys)).toFixed(2),
    hRange: +(Math.max(...hs)-Math.min(...hs)).toFixed(2), jitter, dirCh };
  console.log(`[probe:${MODE}] ${label}: ${JSON.stringify(m)}`);
  return m;
}

// baseline
const baseline = await samplePositions(2000);
analyze(baseline, 'baseline');

// streaming
const ta = await frame.$('textarea[placeholder*="message"]');
await ta.fill(QUESTION);
await page.waitForTimeout(300);
const sendBtn = await frame.$('button:has-text("Send message")');
const streamP = samplePositions(12000);
await sendBtn.click();
const stream = await streamP;
const streamM = analyze(stream, 'streaming');

// 收集 probe 统计
const stats = await frame.evaluate(() => {
  const s = window.__probeStats;
  // 恢复 scrollTo
  if (window.__origScrollTo) Element.prototype.scrollTo = window.__origScrollTo;
  return s;
});
console.log(`\n[probe:${MODE}] === PROBE STATS ===`);
console.log(`[probe:${MODE}] heightWrites: ${stats.heightWrites} (auto: ${stats.heightAutoWrites}, px: ${stats.heightPxWrites})`);
console.log(`[probe:${MODE}] scrollToCalls: ${stats.scrollToCalls}`);
console.log(`[probe:${MODE}] domMutations: ${stats.domMutations}`);
if (stats.heightValues.length) {
  console.log(`[probe:${MODE}] heightValues (first ${stats.heightValues.length}): ${JSON.stringify(stats.heightValues)}`);
}
if (stats.scrollToTimestamps.length) {
  console.log(`[probe:${MODE}] scrollTo timestamps: ${JSON.stringify(stats.scrollToTimestamps)}`);
}

console.log(`\n[probe:${MODE}] === VERDICT ===`);
console.log(`[probe:${MODE}] stream jitter: ${streamM.jitter}, yRange: ${streamM.yRange}, hRange: ${streamM.hRange}`);
const isRed = streamM.jitter > 5;
console.log(`[probe:${MODE}] RED: ${isRed ? 'YES (bug present)' : 'NO (bug suppressed)'}`);

await browser.close();
process.exit(0);
