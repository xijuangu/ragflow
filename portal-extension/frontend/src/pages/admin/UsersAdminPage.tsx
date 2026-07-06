/**
 * 用户管理页(Slice 11,验收点 2)— 列表 / 创建 / 启用禁用 / 硬删除(确认)。
 *
 * 对应后端:
 *   - GET /admin/users(列表)
 *   - POST /admin/users(创建,201)
 *   - PATCH /admin/users/:id(启用/禁用,后端写 user_enable/user_disable 审计)
 *   - DELETE /admin/users/:id(硬删除,级联删会话,无孤儿)
 *
 * 硬删除前用 window.confirm 提示级联清会话(对应验收点 2:硬删除时有确认提示)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminUser } from '../../api/client';

function formatTime(epoch: number): string {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export default function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // 创建表单状态
  const [form, setForm] = useState({ username: '', email: '', password: '' });
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listAdminUsers();
        if (!cancelled) setUsers(res.users);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : '加载用户列表失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCreate = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (creating) return;
      setFormError(null);
      const username = form.username.trim();
      const email = form.email.trim();
      const password = form.password;
      if (!username || !email || !password) {
        setFormError('用户名/邮箱/密码均不能为空');
        return;
      }
      setCreating(true);
      try {
        const created = await api.createAdminUser({ username, email, password });
        // 把新建用户追加到本地列表(POST 返回完整对象,无需重新拉取列表)
        setUsers((prev) => (prev ? [...prev, created] : [created]));
        setForm({ username: '', email: '', password: '' });
      } catch (e) {
        setFormError(e instanceof ApiError ? e.message : '创建用户失败');
      } finally {
        setCreating(false);
      }
    },
    [creating, form],
  );

  const handleToggleEnabled = useCallback(
    async (u: AdminUser) => {
      if (busyId) return;
      setBusyId(u.id);
      const next = !u.enabled;
      // 乐观更新
      setUsers((prev) =>
        prev ? prev.map((x) => (x.id === u.id ? { ...x, enabled: next } : x)) : prev,
      );
      try {
        await api.updateAdminUser(u.id, next);
      } catch (e) {
        // 回滚
        setUsers((prev) =>
          prev ? prev.map((x) => (x.id === u.id ? { ...x, enabled: u.enabled } : x)) : prev,
        );
        setError(e instanceof ApiError ? e.message : '更新用户失败');
      } finally {
        setBusyId(null);
      }
    },
    [busyId],
  );

  const handleDelete = useCallback(
    async (u: AdminUser) => {
      if (busyId) return;
      const confirmed = window.confirm(
        `确定硬删除用户「${u.username}」?\n该操作会级联删除其所有会话(不可恢复)。`,
      );
      if (!confirmed) return;
      setBusyId(u.id);
      try {
        await api.deleteAdminUser(u.id);
        setUsers((prev) => (prev ? prev.filter((x) => x.id !== u.id) : prev));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '删除用户失败');
      } finally {
        setBusyId(null);
      }
    },
    [busyId],
  );

  return (
    <section>
      <h2 className="page-title">用户管理</h2>

      {error && <div className="alert-error">{error}</div>}

      {/* 创建表单 */}
      <form className="admin-form card" onSubmit={handleCreate} aria-label="创建用户表单">
        <h3 className="form-title">创建用户</h3>
        {formError && <div className="alert-error">{formError}</div>}
        <div className="admin-form-row">
          <div className="form-field">
            <label htmlFor="new-username">用户名</label>
            <input
              id="new-username"
              type="text"
              value={form.username}
              onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
              required
            />
          </div>
          <div className="form-field">
            <label htmlFor="new-email">邮箱</label>
            <input
              id="new-email"
              type="email"
              value={form.email}
              onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              required
            />
          </div>
          <div className="form-field">
            <label htmlFor="new-password">初始密码</label>
            <input
              id="new-password"
              type="password"
              value={form.password}
              onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
              required
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={creating}>
            {creating ? '创建中…' : '创建用户'}
          </button>
        </div>
      </form>

      {/* 用户列表 */}
      <div className="admin-table-wrap">
        {users === null && !error && <div className="loading">加载中…</div>}
        {users !== null && users.length === 0 && (
          <div className="empty-state card">暂无用户</div>
        )}
        {users !== null && users.length > 0 && (
          <table className="admin-table">
            <thead>
              <tr>
                <th>用户名</th>
                <th>邮箱</th>
                <th>角色</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} data-testid={`user-row-${u.id}`}>
                  <td>{u.username}</td>
                  <td>{u.email}</td>
                  <td>{u.is_admin ? <span className="badge badge-info">管理员</span> : '普通用户'}</td>
                  <td>
                    {u.enabled ? (
                      <span className="badge badge-success">启用</span>
                    ) : (
                      <span className="badge badge-danger">禁用</span>
                    )}
                  </td>
                  <td>{formatTime(u.created_at)}</td>
                  <td className="admin-actions">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleToggleEnabled(u)}
                      disabled={busyId === u.id || u.is_admin}
                      title={u.is_admin ? '管理员不可禁用' : ''}
                    >
                      {u.enabled ? '禁用' : '启用'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => handleDelete(u)}
                      disabled={busyId === u.id || u.is_admin}
                      title={u.is_admin ? '管理员不可删除' : ''}
                    >
                      硬删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
