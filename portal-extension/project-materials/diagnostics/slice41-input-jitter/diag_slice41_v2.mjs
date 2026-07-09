/**
 * Phase 1 v2 — 增强诊断:捕获 SSE 内容增长 + scrollHeight + 多指标 + 支持 headed。
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
const HEADED = process.env.HEADED === '1';

const QUESTION = '请详细介绍人工智能的发展历史,要详细一点,从图灵讲到现在,至少2000字';
const SAMPLE_INTERVAL_MS = 8;
const STREAM_SAMPLE_MS = 12000;  // 加长到 12 秒
const JITTER_THRESHOLD_PX = 1.5; // 降低阈值看细微抖动

// login
const loginResp = await fetch(PORTAL + '/login', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD }),
});
const cookieMatch = (loginResp.headers.get('set-cookie') || '').match(/portal_session=([^;]+)/);
if (!cookieMatch) { console.error('FATAL: no cookie'); process.exit(2); }
const portalSession = cookieMatch[1];
console.log('[s41v2] login ok, headed=' + HEADED);

const browser = await chromium.launch({ headless: !HEADED, executablePath: CHROME });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await context.addCookies([{ name: 'portal_session', value: portalSession, domain: PORTAL_HOST, path: '/' }]);
const page = await context.newPage();

await page.goto(PORTAL + '/share-pages/sp_default', { waitUntil: 'networkidle' });
await page.waitForTimeout(2500);
const frame = await (await page.$('iframe')).contentFrame();
await frame.waitForSelector('textarea[placeholder*="message"]', { timeout: 15000 });
console.log('[s41v2] iframe + textarea ready');

// 增强采样:textarea bbox + scrollParent + body scrollHeight + 消息内容长度
async function sampleEnhanced(durationMs, label) {
  const samples = await frame.evaluate(async ({ d, interval }) => {
    const ta = document.querySelector('textarea[placeholder*="message"]');
    if (!ta) return { error: 'no textarea' };
    const samples = [];
    const t0 = performance.now();
    return new Promise((resolve) => {
      const iv = setInterval(() => {
        const r = ta.getBoundingClientRect();
        // 找所有可能的滚动容器
        let sp = ta.parentElement, scrollInfo = null;
        while (sp && sp !== document.body) {
          const s = getComputedStyle(sp);
          if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && sp.scrollHeight > sp.clientHeight + 1) {
            scrollInfo = { cls: sp.className.slice(0, 50), scrollTop: +sp.scrollTop.toFixed(1),
              scrollHeight: sp.scrollHeight, clientHeight: sp.clientHeight };
            break;
          }
          sp = sp.parentElement;
        }
        // 统计消息内容长度(所有 markdown 渲染区域的文本)
        let msgLen = 0;
        document.querySelectorAll('[class*="message"], [class*="content"], [class*="markdown"]').forEach(el => {
          msgLen += (el.textContent || '').length;
        });
        samples.push({
          t: +(performance.now() - t0).toFixed(1),
          y: +r.y.toFixed(2), x: +r.x.toFixed(2), h: +r.height.toFixed(2), bottom: +r.bottom.toFixed(2),
          bodyScrollH: document.body.scrollHeight, bodyClientH: document.body.clientHeight,
          scroll: scrollInfo, msgLen,
        });
        if (performance.now() - t0 > d) { clearInterval(iv); resolve(samples); }
      }, interval);
    });
  }, { d: durationMs, interval: SAMPLE_INTERVAL_MS });
  console.log(`[s41v2] ${label}: ${samples.length} samples`);
  return samples;
}

function analyzeEnhanced(samples, label) {
  const ys = samples.map(s => s.y);
  const hs = samples.map(s => s.h);
  const min = Math.min(...ys), max = Math.max(...ys);
  const mean = ys.reduce((a, b) => a + b, 0) / ys.length;
  const std = Math.sqrt(ys.reduce((a, b) => a + (b - mean) ** 2, 0) / ys.length);
  let jitterCount = 0, dirChanges = 0;
  const changes = [];
  for (let i = 1; i < ys.length; i++) {
    const delta = Math.abs(ys[i] - ys[i - 1]);
    if (delta > JITTER_THRESHOLD_PX) { jitterCount++; if (changes.length < 8) changes.push({ i, t: samples[i].t, delta: +delta.toFixed(2), from: +ys[i-1].toFixed(2), to: +ys[i].toFixed(2) }); }
  }
  for (let i = 2; i < ys.length; i++) {
    const d1 = ys[i] - ys[i - 1], d2 = ys[i - 1] - ys[i - 2];
    if (Math.abs(d1) > 0.5 && Math.abs(d2) > 0.5 && d1 * d2 < 0) dirChanges++;
  }
  // height 变化
  const hMin = Math.min(...hs), hMax = Math.max(...hs);
  // 内容增长
  const msgLens = samples.map(s => s.msgLen);
  const msgStart = msgLens[0] || 0, msgEnd = msgLens[msgLens.length - 1] || 0;
  // scrollHeight 变化
  const bsh = samples.map(s => s.bodyScrollH);
  const bshStart = bsh[0] || 0, bshEnd = bsh[bsh.length - 1] || 0;
  const result = { label, count: samples.length, yMin: +min.toFixed(2), yMax: +max.toFixed(2),
    yRange: +(max - min).toFixed(2), yStd: +std.toFixed(3), jitterCount, dirChanges,
    hMin: +hMin.toFixed(2), hMax: +hMax.toFixed(2), hRange: +(hMax - hMin).toFixed(2),
    msgLenStart: msgStart, msgLenEnd: msgEnd, msgGrowth: msgEnd - msgStart,
    bodySHstart: bshStart, bodySHend: bshEnd, bodySHgrowth: bshEnd - bshStart };
  console.log(`[s41v2] ${label}: ${JSON.stringify(result)}`);
  if (changes.length) console.log(`[s41v2]   changes: ${JSON.stringify(changes)}`);
  // scroll parent info
  const scrolls = samples.filter(s => s.scroll);
  if (scrolls.length) {
    const stMin = Math.min(...scrolls.map(s => s.scroll.scrollTop));
    const stMax = Math.max(...scrolls.map(s => s.scroll.scrollTop));
    console.log(`[s41v2]   scrollParent "${scrolls[0].scroll.cls}": scrollTop ${stMin.toFixed(1)}→${stMax.toFixed(1)} (range ${(stMax-stMin).toFixed(1)}); scrollHeight ${scrolls[0].scroll.scrollHeight}→${scrolls[scrolls.length-1].scroll.scrollHeight}`);
  } else {
    console.log(`[s41v2]   no scrollParent found`);
  }
  return result;
}

// baseline
console.log('\n[s41v2] === BASELINE ===');
const baseline = await sampleEnhanced(2000, 'baseline');
const baselineM = analyzeEnhanced(baseline, 'baseline');

// streaming
console.log('\n[s41v2] === STREAMING ===');
const textarea = await frame.$('textarea[placeholder*="message"]');
await textarea.fill(QUESTION);
await page.waitForTimeout(300);
const sendBtn = await frame.$('button:has-text("Send message")');
console.log('[s41v2] clicking send...');
const streamP = sampleEnhanced(STREAM_SAMPLE_MS, 'streaming');
await sendBtn.click();
const stream = await streamP;
const streamM = analyzeEnhanced(stream, 'streaming');

// verdict
console.log('\n[s41v2] === VERDICT ===');
console.log(`[s41v2] msgGrowth: ${streamM.msgGrowth} chars (baseline ${baselineM.msgGrowth})`);
console.log(`[s41v2] bodySHgrowth: ${streamM.bodySHgrowth}px (baseline ${baselineM.bodySHgrowth}px)`);
console.log(`[s41v2] yRange: stream=${streamM.yRange}px baseline=${baselineM.yRange}px`);
console.log(`[s41v2] jitter(>${JITTER_THRESHOLD_PX}px): stream=${streamM.jitterCount} baseline=${baselineM.jitterCount}`);
console.log(`[s41v2] dirChanges: stream=${streamM.dirChanges} baseline=${baselineM.dirChanges}`);

const isRed = streamM.jitterCount > 5 || streamM.yRange > 4;
console.log(`\n[s41v2] RED: ${isRed ? 'YES' : 'NO'}`);

// dump y timeline for visualization
const ys = stream.map(s => s.y);
const uniqueYs = [...new Set(ys.map(y => Math.round(y * 10) / 10))];
console.log(`[s41v2] unique y values (rounded to 0.1): ${JSON.stringify(uniqueYs)}`);
// dump y every 50 samples
const timeline = stream.filter((_, i) => i % 50 === 0).map(s => ({ t: s.t, y: s.y, h: s.h, msgLen: s.msgLen, bsh: s.bodyScrollH }));
console.log(`[s41v2] y timeline (every 50th): ${JSON.stringify(timeline)}`);

await browser.close();
process.exit(isRed ? 0 : 1);
