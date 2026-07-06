/**
 * 审计日志页(Slice 11,验收点 7)— 列出 8 类敏感操作日志,按 action/actor/时间筛选。
 *
 * 对应后端:
 *   - GET /admin/audit-logs?actor_user_id=&action=&since=&until=&limit=
 *     返回 { audit_logs: [{ id, actor_user_id, action, target_type, target_id, at, meta }] }
 *
 * 8 类敏感操作(action):login_success / login_failure / grant_create / grant_revoke /
 *   session_delete / session_view_elevated / user_enable / user_disable。
 *
 * 对应 PRD D8:管理员分级查看 + 敏感操作审计(8 类)。
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

function formatTime(epoch: number): string {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

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
  const [error, setError] = useState<string | null>(null);

  // 筛选表单
  const [filterActor, setFilterActor] = useState('');
  const [filterAction, setFilterAction] = useState('');

  const userMap = useMemo(() => {
    const m = new Map<string, AdminUser>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const loadLogs = useCallback(
    async (opts: { actorUserId?: string; action?: string } = {}) => {
      setLogs(null);
      setError(null);
      try {
        const res = await api.listAdminAuditLogs({
          actor_user_id: opts.actorUserId || undefined,
          action: opts.action || undefined,
        });
        setLogs(res.audit_logs);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '加载审计日志失败');
      }
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listAdminUsers();
        if (cancelled) return;
        setUsers(res.users);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : '加载用户列表失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  const handleSearch = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      await loadLogs({
        actorUserId: filterActor,
        action: filterAction,
      });
    },
    [filterActor, filterAction, loadLogs],
  );

  return (
    <section>
      <h2 className="page-title">审计日志</h2>

      {error && <div className="alert-error">{error}</div>}

      {/* 筛选表单 */}
      <form className="admin-form card" onSubmit={handleSearch} aria-label="审计日志筛选表单">
        <div className="admin-form-row">
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
          <button type="submit" className="btn btn-primary">
            筛选
          </button>
        </div>
      </form>

      {/* 日志列表 */}
      <div className="admin-table-wrap">
        {logs === null && !error && <div className="loading">加载中…</div>}
        {logs !== null && logs.length === 0 && (
          <div className="empty-state card">暂无日志</div>
        )}
        {logs !== null && logs.length > 0 && (
          <table className="admin-table">
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
                  <td>{formatTime(log.at)}</td>
                  <td>{userMap.get(log.actor_user_id)?.username ?? log.actor_user_id}</td>
                  <td>
                    <span className="badge badge-info">{log.action}</span>
                  </td>
                  <td>{log.target_type}</td>
                  <td>{log.target_id}</td>
                  <td className="audit-meta">{formatMeta(log.meta)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
