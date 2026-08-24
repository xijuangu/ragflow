/**
 * 把 epoch 秒格式化为本地时间字符串(TD6:从各 admin 页 + SharePageDetailPage 抽取的共享实现)。
 *
 * 各模式输出(与原内联实现逐字符一致,均使用本地时区):
 *   - 'date'     → YYYY-MM-DD
 *   - 'datetime' → YYYY-MM-DD HH:MM
 *   - 'seconds'  → YYYY-MM-DD HH:MM:SS
 *
 * epoch 为 falsy(0/null/undefined)时返回空串,与原各页内联实现一致。
 */

/** formatTime 的精度模式。 */
export type FormatTimeMode = 'date' | 'datetime' | 'seconds';

/** 把 epoch 秒格式化为本地时间字符串。 */
export function formatTime(epoch: number, mode: FormatTimeMode): string {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  const datePart = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  if (mode === 'date') return datePart;
  const timePart = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (mode === 'datetime') return `${datePart} ${timePart}`;
  return `${datePart} ${timePart}:${pad(d.getSeconds())}`;
}

/** 日期值(YYYY-MM-DD)→ 当日 UTC 起 epoch 秒;非法日期返回 null(调用方不过滤)。 */
export function parseDayStart(v: string): number | null {
  const t = Date.parse(`${v}T00:00:00Z`);
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}

/** 日期值(YYYY-MM-DD)→ 当日 UTC 止 epoch 秒;非法日期返回 null(调用方不过滤)。 */
export function parseDayEnd(v: string): number | null {
  const t = Date.parse(`${v}T23:59:59Z`);
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}

/** 当月第一天的日期值(YYYY-MM-DD,UTC),供「默认本月」筛选初始值用。 */
export function firstDayOfCurrentMonth(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-01`;
}
