/**
 * 用量统计聚合(Issue 92)— 从 UsageStatsAdminPage 抽出的纯函数模块
 * (react-refresh 约束:组件文件不导出非组件符号;纯函数独立成模块便于单测)。
 */
import type { AdminUsageRecord } from '../../api/client';

/** 汇总视图行(按用户聚合)。 */
export interface UsageSummaryRow {
  portal_user_id: string;
  username: string;
  request_count: number;
  input_tokens: number;
  output_tokens: number;
  incomplete_count: number;
}

/** 按用户聚合用量记录(纯函数,页面与测试共用)。 */
export function aggregateUsageByUser(
  records: readonly AdminUsageRecord[],
  resolveUser: (portalUserId: string) => string,
): UsageSummaryRow[] {
  const byUser = new Map<string, UsageSummaryRow>();
  for (const r of records) {
    let row = byUser.get(r.portal_user_id);
    if (!row) {
      row = {
        portal_user_id: r.portal_user_id,
        username: resolveUser(r.portal_user_id),
        request_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        incomplete_count: 0,
      };
      byUser.set(r.portal_user_id, row);
    }
    row.request_count += 1;
    row.input_tokens += r.input_tokens;
    row.output_tokens += r.output_tokens;
    if (r.incomplete) row.incomplete_count += 1;
  }
  return [...byUser.values()];
}
