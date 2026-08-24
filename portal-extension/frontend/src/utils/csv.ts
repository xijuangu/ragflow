/**
 * CSV 导出工具(Issue 92 用量统计;可复用到其他 admin 列表)。
 *
 * - buildCsv:纯函数,负责转义(逗号/引号/换行)与拼接,可直接单测。
 * - downloadCsv:浏览器下载(Blob + a[download]),带 BOM 让 Excel 正确识别中文。
 */

/** 单元格转义:含逗号/双引号/换行时加引号包裹,内部双引号 doubling(RFC 4180)。 */
function escapeCell(value: string | number | boolean): string {
  const s = String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 构造 CSV 文本(CRLF 行尾;首行为表头)。 */
export function buildCsv(
  headers: readonly string[],
  rows: readonly (readonly (string | number | boolean)[])[],
): string {
  return [headers, ...rows].map((row) => row.map(escapeCell).join(',')).join('\r\n');
}

/** 触发浏览器下载(filename 建议带 .csv 后缀)。 */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
