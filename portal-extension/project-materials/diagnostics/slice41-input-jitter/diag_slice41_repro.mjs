/**
 * Phase 1 red-capable repro — SSE 流式输出期间输入框抖动。
 * 流程:登录 → 分享页 → iframe 内输入 → 发送 → SSE 期间高频采样 textarea bbox → 抖动指标。
 * 断言:流式期抖动次数 > 阈值(红),基线期抖动次数 ≤ 阈值(绿)。
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

const QUESTION = '请详细介绍人工智能的发展历史,要详细一点,从图灵讲到现在';
const SAMPLE_INTERVAL_MS = 8;       // 高频采样
const BASELINE_MS = 2000;           // 基线期(不发消息)
const STREAM_SAMPLE_MS = 8000;      // 流式期采样 8 秒
const JITTER_THRESHOLD_PX = 2;     // y 变化 > 2px 算一次抖动

// 1. login
const loginResp = await fetch(PORTAL + '/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD }),
});
const setCookie = loginResp.headers.get('set-cookie') || '';
const cookieMatch = setCookie.match(/portal_session=([^;]+)/);
if (!cookieMatch) { console.error('FATAL: no portal_session cookie'); process.exit(2); }
const portalSession = cookieMatch[1];
console.log('[s41] login ok');

// 2. browser
const browser = await chromium.launch({ headless: true, executablePath: CHROME });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await context.addCookies([{ name: 'portal_session', value: portalSession, domain: PORTAL_HOST, path: '/' }]);
const page = await context.newPage();

console.log('[s41] goto share page detail');
await page.goto(PORTAL + '/share-pages/sp_default', { waitUntil: 'networkidle' });
await page.waitForTimeout(2500);

const iframeEl = await page.$('iframe');
if (!iframeEl) { console.error('FATAL: no iframe'); await browser.close(); process.exit(2); }
const frame = await iframeEl.contentFrame();
console.log('[s41] iframe loaded:', frame.url().slice(0, 60));

// 等待 textarea 出现
await frame.waitForSelector('textarea[placeholder*="message"]', { timeout: 15000 });
const textarea = await frame.$('textarea[placeholder*="message"]');
if (!textarea) { console.error('FATAL: no textarea'); await browser.close(); process.exit(2); }
console.log('[s41] textarea found');

// 采样函数:在 frame 内高频采集 textarea 的 getBoundingClientRect + 容器信息
async function samplePositions(durationMs, label) {
  const samples = await frame.evaluate(async (d) => {
    const ta = document.querySelector('textarea[placeholder*="message"]');
    if (!ta) return { error: 'no textarea' };
    const samples = [];
    const t0 = performance.now();
    return new Promise((resolve) => {
      const iv = setInterval(() => {
        const r = ta.getBoundingClientRect();
        // 找 textarea 的最近可滚动祖先(消息容器),测其 scrollTop/scrollHeight
        let scrollParent = ta.parentElement;
        let scrollInfo = null;
        while (scrollParent && scrollParent !== document.body) {
          const s = getComputedStyle(scrollParent);
          if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && scrollParent.scrollHeight > scrollParent.clientHeight) {
            scrollInfo = { cls: scrollParent.className.slice(0, 40), scrollTop: scrollParent.scrollTop,
              scrollHeight: scrollParent.scrollHeight, clientHeight: scrollParent.clientHeight };
            break;
          }
          scrollParent = scrollParent.parentElement;
        }
        samples.push({
          t: performance.now() - t0,
          y: r.y, x: r.x, h: r.height, w: r.width, bottom: r.bottom,
          scroll: scrollInfo,
        });
        if (performance.now() - t0 > d) { clearInterval(iv); resolve(samples); }
      }, 8);
    });
  }, durationMs);
  console.log(`[s41] ${label}: ${samples.length} samples`);
  return samples;
}

// 分析抖动指标
function analyze(samples, label) {
  if (!samples || !samples.length) { console.log(`[s41] ${label}: NO SAMPLES`); return { jitter: 0 }; }
  const ys = samples.map(s => s.y);
  const min = Math.min(...ys), max = Math.max(...ys);
  const mean = ys.reduce((a, b) => a + b, 0) / ys.length;
  const variance = ys.reduce((a, b) => a + (b - mean) ** 2, 0) / ys.length;
  const std = Math.sqrt(variance);
  // 抖动次数:相邻采样 y 变化 > 阈值
  let jitterCount = 0;
  const changes = [];
  for (let i = 1; i < ys.length; i++) {
    const delta = Math.abs(ys[i] - ys[i - 1]);
    if (delta > JITTER_THRESHOLD_PX) { jitterCount++; changes.push({ i, delta: +delta.toFixed(2), from: +ys[i-1].toFixed(2), to: +ys[i].toFixed(2) }); }
  }
  // 方向变化次数(上下弹跳)
  let dirChanges = 0;
  for (let i = 2; i < ys.length; i++) {
    const d1 = ys[i] - ys[i - 1], d2 = ys[i - 1] - ys[i - 2];
    if (Math.abs(d1) > 0.5 && Math.abs(d2) > 0.5 && d1 * d2 < 0) dirChanges++;
  }
  const result = { label, count: samples.length, yMin: +min.toFixed(2), yMax: +max.toFixed(2),
    yRange: +(max - min).toFixed(2), yStd: +std.toFixed(3), jitterCount, dirChanges };
  console.log(`[s41] ${label}: ${JSON.stringify(result)}`);
  if (changes.length) console.log(`[s41]   first 5 changes: ${JSON.stringify(changes.slice(0, 5))}`);
  // scroll 变化(消息容器是否在滚动)
  const scrolls = samples.filter(s => s.scroll).map(s => s.scroll.scrollTop);
  if (scrolls.length) {
    const sMin = Math.min(...scrolls), sMax = Math.max(...scrolls);
    console.log(`[s41]   scrollParent: scrollTop min=${sMin.toFixed(1)} max=${sMax.toFixed(1)} range=${(sMax-sMin).toFixed(1)}`);
  }
  return result;
}

// === 基线期:不发消息,采样 2 秒,应稳定(绿) ===
console.log('\n[s41] === BASELINE (no SSE) ===');
const baseline = await samplePositions(BASELINE_MS, 'baseline');
const baselineMetrics = analyze(baseline, 'baseline');

// === 流式期:输入问题 → 发送 → SSE 期间采样 ===
console.log('\n[s41] === STREAMING (SSE) ===');
await textarea.fill(QUESTION);
await page.waitForTimeout(300);
// 找发送按钮
const sendBtn = await frame.$('button:has-text("Send message")');
if (!sendBtn) { console.error('FATAL: no send button'); await browser.close(); process.exit(2); }
console.log('[s41] send button found, clicking...');
// 并行:启动采样 + 点击发送
const streamSamplePromise = samplePositions(STREAM_SAMPLE_MS, 'streaming');
await sendBtn.click();
const streamSamples = await streamSamplePromise;
const streamMetrics = analyze(streamSamples, 'streaming');

// === 判定 ===
console.log('\n[s41] === VERDICT ===');
const baselineJitter = baselineMetrics.jitterCount;
const streamJitter = streamMetrics.jitterCount;
console.log(`[s41] baseline jitter count: ${baselineJitter} (expect low, green)`);
console.log(`[s41] streaming jitter count: ${streamJitter} (expect high if bug present)`);
console.log(`[s41] streaming yRange: ${streamMetrics.yRange}px (baseline: ${baselineMetrics.yRange}px)`);
console.log(`[s41] streaming yStd: ${streamMetrics.yStd} (baseline: ${baselineMetrics.yStd})`);
console.log(`[s41] streaming dirChanges: ${streamMetrics.dirChanges} (baseline: ${baselineMetrics.dirChanges})`);

const RED_THRESHOLD = 5; // 流式期抖动 > 5 次算红
const isRed = streamJitter > RED_THRESHOLD && streamMetrics.yRange > 3;
console.log(`\n[s41] RED CAPABLE: ${isRed ? 'YES (bug reproduced)' : 'NO (could not reproduce)'}`);
if (isRed) {
  console.log(`[s41] ✅ Phase 1 PASS: red-capable loop established. jitter=${streamJitter} range=${streamMetrics.yRange}px`);
} else {
  console.log(`[s41] ⚠️ Phase 1 FAIL: cannot reproduce jitter in headless. may need headed mode or longer sample.`);
}

await browser.close();
process.exit(isRed ? 0 : 1);
