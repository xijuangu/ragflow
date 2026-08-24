/**
 * 审计日志页(Slice 11,验收点 7;Issue 87 改为前端内存检索)— 全量加载 + 搜索/筛选/排序。
 *
 * 对应后端:
 *   - GET /admin/audit-logs(全量;Issue 87 起筛选/排序全在浏览器内存完成,§12.1。
 *     日志量增长到万级再单独做服务端分页或默认时间窗)
 *     返回 { audit_logs: [{ id, actor_user_id, action, target_type, target_id, at, meta }] }
 *
 * 敏感操作(action):login_success / login_failure / grant_create / grant_revoke /
 *   session_delete / session_view_elevated / user_password_change / user_enable / user_disable。
 *
 * 对应 PRD D8:管理员分级查看 + 敏感操作审计。
 */
import { useMemo } from 'react';
import {
  api,
  AUDIT_ACTIONS,
  type AdminAuditLog,
} from '../../api/client';
import { formatTime } from '../../utils/formatTime';
import { useAdminList, useAdminListView } from '../../hooks/useAdminList';
import type { AdminListViewConfig } from '../../hooks/useAdminList';
import { MobileCard, MobileCardList } from '../../components/MobileCards';
import { AdminListToolbar, SortableTh } from '../../components/AdminListControls';

function formatMeta(meta: Record<string, unknown> | null): string {
  if (!meta) return '';
  try {
    return JSON.stringify(meta);
  } catch {
    return '';
  }
}

/** 日期值(YYYY-MM-DD)→ 当日 UTC 起/止 epoch 秒;非法日期不过滤。 */
function parseDayStart(v: string): number | null {
  const t = Date.parse(`${v}T00:00:00Z`);
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}
function parseDayEnd(v: string): number | null {
  const t = Date.parse(`${v}T23:59:59Z`);
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}

export default function AuditLogsAdminPage() {
  // Issue 87:全量加载(用户用于操作者名解析),筛选/排序在内存完成
  const { data, error } = useAdminList(
    async () => {
      const [usersRes, logsRes] = await Promise.all([
        api.listAdminUsers(),
        api.listAdminAuditLogs({ limit: 10000 }),
      ]);
      return { users: usersRes.users, logs: logsRes.audit_logs };
    },
    { errorMessage: '加载审计日志失败' },
  );
  const users = useMemo(() => data?.users ?? [], [data]);
  const logs = data?.logs ?? null;

  const userMap = useMemo(() => {
    const m = new Map<string, (typeof users)[number]>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const resolveActor = (log: AdminAuditLog) =>
    userMap.get(log.actor_user_id)?.username ?? log.actor_user_id;

  // Issue 87:搜索列 = 操作者;筛选 = 操作类型 + 时间范围(日期);排序 = 时间/操作者/操作类型
  const logsViewConfig: AdminListViewConfig<AdminAuditLog> = {
    searchColumns: [(log) => resolveActor(log)],
    filters: [
      {
        key: 'action',
        label: '操作类型',
        options: AUDIT_ACTIONS.map((a) => ({ value: a, label: a })),
        match: (log, v) => log.action === v,
      },
      {
        key: 'since',
        label: '起始日期',
        control: 'date',
        match: (log, v) => {
          const since = parseDayStart(v);
          return since === null || log.at >= since;
        },
      },
      {
        key: 'until',
        label: '结束日期',
        control: 'date',
        match: (log, v) => {
          const until = parseDayEnd(v);
          return until === null || log.at <= until;
        },
      },
    ],
    sortColumns: [
      { key: 'at', label: '时间', compare: (a, b) => a.at - b.at },
      { key: 'actor', label: '操作者', compare: (a, b) => resolveActor(a).localeCompare(resolveActor(b)) },
      { key: 'action', label: '操作类型', compare: (a, b) => a.action.localeCompare(b.action) },
    ],
  };
  const {
    view: visibleLogs,
    search,
    setSearch,
    filterValues,
    setFilterValue,
    sort,
    toggleSort,
  } = useAdminListView(logs, logsViewConfig);

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 审计日志</div>
          <h1>审计日志</h1>
          <div className="sub">查看敏感操作记录:登录、授权、会话查看、改密、用户启停。按操作者、类型与时间筛选。</div>
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {/* 日志列表(搜索/筛选/排序全在内存完成,Issue 87) */}
      <div className="card card-table">
        <AdminListToolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel="搜索操作者"
          searchPlaceholder="按操作者用户名搜索"
          filters={logsViewConfig.filters}
          filterValues={filterValues}
          onFilterChange={setFilterValue}
        />
        <div className="card-body">
          {logs === null && !error && <div className="loading">加载中…</div>}
          {logs !== null && logs.length === 0 && (
            <div className="empty-state">暂无日志</div>
          )}
          {logs !== null && logs.length > 0 && visibleLogs !== null && visibleLogs.length === 0 && (
            <div className="empty-state">无匹配结果</div>
          )}
          <div className="table-wrap">
            {visibleLogs !== null && visibleLogs.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <SortableTh columnKey="at" label="时间" sort={sort} onToggle={toggleSort} />
                    <SortableTh columnKey="actor" label="操作者" sort={sort} onToggle={toggleSort} />
                    <SortableTh columnKey="action" label="操作类型" sort={sort} onToggle={toggleSort} />
                    <th>目标类型</th>
                    <th>目标 ID</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleLogs.map((log) => {
                    const metaText = formatMeta(log.meta);
                    return (
                    <tr key={log.id} data-testid={`audit-row-${log.id}`}>
                      <td className="mono">{formatTime(log.at, 'seconds')}</td>
                      <td>{resolveActor(log)}</td>
                      <td>
                        <span className="badge badge-info">{log.action}</span>
                      </td>
                      <td>{log.target_type}</td>
                      <td className="mono">{log.target_id}</td>
                      <td className="audit-meta" title={metaText}>{metaText}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
          {visibleLogs !== null && visibleLogs.length > 0 && (
            <MobileCardList label="审计日志列表">
              {visibleLogs.map((log) => {
                const metaText = formatMeta(log.meta);
                return (
                  <MobileCard
                    key={log.id}
                    title={log.action}
                    testId={`mobile-audit-${log.id}`}
                    fields={[
                      { label: '时间', value: <span className="mono">{formatTime(log.at, 'seconds')}</span> },
                      { label: '操作者', value: resolveActor(log) },
                      { label: '目标', value: `${log.target_type} / ${log.target_id}` },
                      { label: '详情', value: <span className="audit-meta" title={metaText}>{metaText || '—'}</span> },
                    ]}
                  />
                );
              })}
            </MobileCardList>
          )}
        </div>
      </div>
    </section>
  );
}
