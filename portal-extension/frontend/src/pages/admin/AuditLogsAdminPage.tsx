/**
 * 审计日志页(Slice 11,验收点 7)— 列出敏感操作日志,按 action/actor/时间筛选。
 *
 * 对应后端:
 *   - GET /admin/audit-logs?actor_user_id=&action=&since=&until=&limit=
 *     返回 { audit_logs: [{ id, actor_user_id, action, target_type, target_id, at, meta }] }
 *
 * 敏感操作(action):login_success / login_failure / grant_create / grant_revoke /
 *   session_delete / session_view_elevated / user_password_change / user_enable / user_disable。
 *
 * 对应 PRD D8:管理员分级查看 + 敏感操作审计。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import {
  ApiError,
  api,
  AUDIT_ACTIONS,
  type AdminAuditLog,
  type AdminUser,
  type AuditAction,
} from '../../api/client';
import { formatTime } from '../../utils/formatTime';
import { useAdminList } from '../../hooks/useAdminList';

function formatMeta(meta: Record<string, unknown> | null): string {
  if (!meta) return '';
  try {
    return JSON.stringify(meta);
  } catch {
    return '';
  }
}

export default function AuditLogsAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [logs, setLogs] = useState<AdminAuditLog[] | null>(null);
  const { error, setError } = useAdminList(
    async () => {
      const res = await api.listAdminUsers();
      return res.users;
    },
    {
      errorMessage: '加载用户列表失败',
      onSuccess: (u) => setUsers(u),
    },
  );

  // 筛选表单
  const [filterActor, setFilterActor] = useState('');
  const [filterAction, setFilterAction] = useState('');
  const [filterSince, setFilterSince] = useState(''); // YYYY-MM-DD
  const [filterUntil, setFilterUntil] = useState(''); // YYYY-MM-DD

  const userMap = useMemo(() => {
    const m = new Map<string, AdminUser>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const loadLogs = useCallback(
    async (opts: {
      actorUserId?: string;
      action?: string;
      since?: number;
      until?: number;
    } = {}) => {
      setLogs(null);
      setError(null);
      try {
        const res = await api.listAdminAuditLogs({
          actor_user_id: opts.actorUserId || undefined,
          action: opts.action || undefined,
          since: opts.since,
          until: opts.until,
        });
        setLogs(res.audit_logs);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '加载审计日志失败');
      }
    },
    [setError],
  );

  // 首次加载日志(用户列表由 useAdminList 在上方加载)
  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  const handleSearch = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      // date input(YYYY-MM-DD)→ epoch 秒;since 取当日 00:00 UTC,until 取当日 23:59:59 UTC
      const sinceEpoch = filterSince
        ? Math.floor(Date.parse(`${filterSince}T00:00:00Z`) / 1000)
        : undefined;
      const untilEpoch = filterUntil
        ? Math.floor(Date.parse(`${filterUntil}T23:59:59Z`) / 1000)
        : undefined;
      await loadLogs({
        actorUserId: filterActor,
        action: filterAction,
        since: Number.isNaN(sinceEpoch) ? undefined : sinceEpoch,
        until: Number.isNaN(untilEpoch) ? undefined : untilEpoch,
      });
    },
    [filterActor, filterAction, filterSince, filterUntil, loadLogs],
  );

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

      {/* 日志列表(含筛选) */}
      <div className="card card-table">
        <form className="filters" onSubmit={handleSearch} aria-label="审计日志筛选表单">
          <div className="form-field">
            <label htmlFor="filter-actor">操作者</label>
            <select
              id="filter-actor"
              value={filterActor}
              onChange={(e) => setFilterActor(e.target.value)}
            >
              <option value="">全部操作者</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="filter-action">操作类型</label>
            <select
              id="filter-action"
              value={filterAction}
              onChange={(e) => setFilterAction(e.target.value as AuditAction | '')}
            >
              <option value="">全部操作</option>
              {AUDIT_ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="filter-since">起始日期</label>
            <input
              id="filter-since"
              type="date"
              value={filterSince}
              onChange={(e) => setFilterSince(e.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="filter-until">结束日期</label>
            <input
              id="filter-until"
              type="date"
              value={filterUntil}
              onChange={(e) => setFilterUntil(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-primary">
            筛选
          </button>
        </form>
        <div className="card-body">
          <div className="table-wrap">
            {logs === null && !error && <div className="loading">加载中…</div>}
            {logs !== null && logs.length === 0 && (
              <div className="empty-state">暂无日志</div>
            )}
            {logs !== null && logs.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作者</th>
                    <th>操作类型</th>
                    <th>目标类型</th>
                    <th>目标 ID</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id} data-testid={`audit-row-${log.id}`}>
                      <td className="mono">{formatTime(log.at, 'seconds')}</td>
                      <td>{userMap.get(log.actor_user_id)?.username ?? log.actor_user_id}</td>
                      <td>
                        <span className="badge badge-info">{log.action}</span>
                      </td>
                      <td>{log.target_type}</td>
                      <td className="mono">{log.target_id}</td>
                      <td className="audit-meta">{formatMeta(log.meta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
