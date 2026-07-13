/**
 * 用户管理页(Slice 11,验收点 2)— 列表 / 创建 / 启用禁用 / 硬删除(确认)。
 *
 * 对应后端:
 *   - GET /admin/users(列表)
 *   - POST /admin/users(创建,201)
 *   - PATCH /admin/users/:id(启用/禁用,后端写 user_enable/user_disable 审计)
 *   - PATCH /admin/users/:id/password(修改密码,后端写 user_password_change 审计)
 *   - DELETE /admin/users/:id(硬删除,级联删会话,无孤儿)
 *
 * 硬删除前用统一危险操作弹窗提示级联清会话、不可恢复和审计后果。
 */
import { useCallback, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminUser } from '../../api/client';
import { formatTime } from '../../utils/formatTime';
import { useAdminList } from '../../hooks/useAdminList';
import { useOptimisticToggle } from '../../hooks/useOptimisticToggle';
import ConfirmDialog from '../../components/ConfirmDialog';
import { MobileCard, MobileCardList } from '../../components/MobileCards';

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
  const [passwordTarget, setPasswordTarget] = useState<AdminUser | null>(null);
  const [passwordValue, setPasswordValue] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);

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

  const confirmDelete = useCallback(
    async () => {
      if (!deleteTarget) return;
      const userId = deleteTarget.id;
      // 悲观删除:成功后才 filter 列表(API 调用 + 改列表一起放进 action)
      await run({
        id: userId,
        action: async () => {
          await api.deleteAdminUser(userId);
          setUsers((prev) => (prev ? prev.filter((x) => x.id !== userId) : prev));
        },
        errorMessage: '删除用户失败',
      });
      setDeleteTarget(null);
    },
    [deleteTarget, run, setUsers],
  );

  const openPasswordModal = useCallback((u: AdminUser) => {
    setPasswordTarget(u);
    setPasswordValue('');
    setPasswordError(null);
    setPasswordSuccess(null);
  }, []);

  const closePasswordModal = useCallback(() => {
    if (passwordSaving) return;
    setPasswordTarget(null);
    setPasswordValue('');
    setPasswordError(null);
  }, [passwordSaving]);

  const handlePasswordSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!passwordTarget || passwordSaving) return;
      setPasswordError(null);
      setPasswordSuccess(null);
      const password = passwordValue;
      if (!password) {
        setPasswordError('新密码不能为空');
        return;
      }
      setPasswordSaving(true);
      try {
        await api.updateAdminUserPassword(passwordTarget.id, password);
        setPasswordSuccess('密码已更新');
        setPasswordTarget(null);
        setPasswordValue('');
      } catch (e) {
        setPasswordError(e instanceof ApiError ? e.message : '修改密码失败');
      } finally {
        setPasswordSaving(false);
      }
    },
    [passwordTarget, passwordValue, passwordSaving],
  );

  const renderUserActions = (u: AdminUser) => (
    <div className="admin-actions">
      <button
        type="button"
        className="btn btn-outline btn-sm"
        onClick={() => openPasswordModal(u)}
        disabled={busyId === u.id}
      >
        改密码
      </button>
      <button
        type="button"
        className="btn btn-outline btn-sm"
        onClick={() => handleToggleEnabled(u)}
        disabled={busyId === u.id || u.is_admin}
        title={u.is_admin ? '管理员不可禁用' : ''}
      >
        {u.enabled ? '禁用' : '启用'}
      </button>
      <button
        type="button"
        className="btn btn-danger btn-sm"
        onClick={() => setDeleteTarget(u)}
        disabled={busyId === u.id || u.is_admin}
        title={u.is_admin ? '管理员不可删除' : ''}
      >
        硬删除
      </button>
    </div>
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
      {passwordSuccess && <div className="alert-success">{passwordSuccess}</div>}

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
          {users === null && !error && <div className="loading">加载中…</div>}
          {users !== null && users.length === 0 && <div className="empty-state">暂无用户</div>}
          {users !== null && users.length > 0 && (
            <>
              <div className="table-wrap">
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
                      <td>
                        {renderUserActions(u)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              <MobileCardList label="用户列表">
                {users.map((u) => (
                  <MobileCard
                    key={u.id}
                    testId={`mobile-user-${u.id}`}
                    title={u.username}
                    badge={<span className={`badge ${u.enabled ? 'b-active' : 'b-archived'}`}>{u.enabled ? '启用' : '禁用'}</span>}
                    fields={[
                      { label: '邮箱', value: u.email },
                      { label: '角色', value: u.is_admin ? '管理员' : '普通用户' },
                      { label: '创建时间', value: <span className="mono">{formatTime(u.created_at, 'date')}</span> },
                    ]}
                    actions={renderUserActions(u)}
                  />
                ))}
              </MobileCardList>
            </>
          )}
        </div>
      </div>

      {passwordTarget && (
        <div className="modal-backdrop" role="presentation">
          <form className="modal-card" onSubmit={handlePasswordSubmit} aria-label="修改用户密码">
            <div className="modal-header">
              <h3>修改密码</h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={closePasswordModal}
                disabled={passwordSaving}
                aria-label="关闭"
              >
                关闭
              </button>
            </div>
            <div className="modal-body">
              <p className="modal-meta">用户: {passwordTarget.username}</p>
              {passwordError && <div className="alert-error">{passwordError}</div>}
              <div className="form-field">
                <label htmlFor="admin-user-new-password">新密码</label>
                <input
                  id="admin-user-new-password"
                  type="password"
                  value={passwordValue}
                  onChange={(e) => setPasswordValue(e.target.value)}
                  autoComplete="new-password"
                  required
                />
              </div>
              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={closePasswordModal}
                  disabled={passwordSaving}
                >
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={passwordSaving}>
                  {passwordSaving ? '保存中…' : '保存密码'}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="硬删除用户"
        message={`确定硬删除用户「${deleteTarget?.username ?? ''}」吗?`}
        confirmText="确认删除"
        variant="danger"
        details={['会级联删除该用户的所有会话', '此操作不可恢复', '会写入审计日志']}
        busy={deleteTarget !== null && busyId === deleteTarget.id}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </section>
  );
}
