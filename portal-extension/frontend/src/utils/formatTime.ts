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
