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
import { useCallback, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminUser } from '../../api/client';
import { formatTime } from '../../utils/formatTime';
import { useAdminList } from '../../hooks/useAdminList';
import { useOptimisticToggle } from '../../hooks/useOptimisticToggle';

export default function UsersAdminPage() {
  const { data: users, setData: setUsers, error, setError } = useAdminList(
    () => api.listAdminUsers().then((r) => r.users),
    { errorMessage: '加载用户列表失败' },
  );
  const { busyId, run } = useOptimisticToggle({ onError: setError });

  // 创建表单状态
  const [form, setForm] = useState({ username: '', email: '', password: '' });
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

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
    [creating, form, setUsers],
  );

  const handleToggleEnabled = useCallback(
    async (u: AdminUser) => {
      const next = !u.enabled;
      await run({
        id: u.id,
        optimistic: () =>
          setUsers((prev) =>
            prev ? prev.map((x) => (x.id === u.id ? { ...x, enabled: next } : x)) : prev,
          ),
        rollback: () =>
          setUsers((prev) =>
            prev ? prev.map((x) => (x.id === u.id ? { ...x, enabled: u.enabled } : x)) : prev,
          ),
        action: () => api.updateAdminUser(u.id, next),
        errorMessage: '更新用户失败',
      });
    },
    [run, setUsers],
  );

  const handleDelete = useCallback(
    async (u: AdminUser) => {
      const confirmed = window.confirm(
        `确定硬删除用户「${u.username}」?\n该操作会级联删除其所有会话(不可恢复)。`,
      );
      if (!confirmed) return;
      // 悲观删除:成功后才 filter 列表(API 调用 + 改列表一起放进 action)
      await run({
        id: u.id,
        action: async () => {
          await api.deleteAdminUser(u.id);
          setUsers((prev) => (prev ? prev.filter((x) => x.id !== u.id) : prev));
        },
        errorMessage: '删除用户失败',
      });
    },
    [run, setUsers],
  );

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 用户管理</div>
          <h1>用户管理</h1>
          <div className="sub">管理门户账号、角色与启用状态。硬删除会级联清空该用户所有会话。</div>
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {/* 创建表单(内联,无 drawer) */}
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
      <div className="card card-table">
        <div className="card-body">
          <div className="table-wrap">
            {users === null && !error && <div className="loading">加载中…</div>}
            {users !== null && users.length === 0 && (
              <div className="empty-state">暂无用户</div>
            )}
            {users !== null && users.length > 0 && (
              <table>
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
                          <span className="badge b-active">启用</span>
                        ) : (
                          <span className="badge b-archived">禁用</span>
                        )}
                      </td>
                      <td className="mono">{formatTime(u.created_at, 'date')}</td>
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
        </div>
      </div>
    </section>
  );
}
