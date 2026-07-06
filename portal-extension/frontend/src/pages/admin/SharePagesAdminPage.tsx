/**
 * 分享页管理页(Slice 11,验收点 4)— 列表 / 创建(填 dialog_id)/ 启用禁用。
 *
 * 对应后端:
 *   - GET /admin/share-pages(列表,含未授权与禁用的)
 *   - POST /admin/share-pages(创建,关联 RAGFlow dialog_id,201)
 *   - PATCH /admin/share-pages/:id(启用/禁用)
 *
 * embed_type/ragflow_type 一期固定值(D9),不开放选择器,后端 create_share_page 默认填。
 */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminSharePage } from '../../api/client';

export default function SharePagesAdminPage() {
  const [pages, setPages] = useState<AdminSharePage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [form, setForm] = useState({ name: '', ragflow_resource_id: '' });
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listAdminSharePages();
        if (!cancelled) setPages(res.share_pages);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : '加载分享页列表失败');
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
      const name = form.name.trim();
      const ragflow_resource_id = form.ragflow_resource_id.trim();
      if (!name || !ragflow_resource_id) {
        setFormError('分享页名称与 dialog_id 均不能为空');
        return;
      }
      setCreating(true);
      try {
        const created = await api.createAdminSharePage({ name, ragflow_resource_id });
        setPages((prev) => (prev ? [...prev, created] : [created]));
        setForm({ name: '', ragflow_resource_id: '' });
      } catch (e) {
        setFormError(e instanceof ApiError ? e.message : '创建分享页失败');
      } finally {
        setCreating(false);
      }
    },
    [creating, form],
  );

  const handleToggleEnabled = useCallback(
    async (p: AdminSharePage) => {
      if (busyId) return;
      setBusyId(p.id);
      const next = !p.enabled;
      setPages((prev) =>
        prev ? prev.map((x) => (x.id === p.id ? { ...x, enabled: next } : x)) : prev,
      );
      try {
        await api.updateAdminSharePage(p.id, next);
      } catch (e) {
        setPages((prev) =>
          prev ? prev.map((x) => (x.id === p.id ? { ...x, enabled: p.enabled } : x)) : prev,
        );
        setError(e instanceof ApiError ? e.message : '更新分享页失败');
      } finally {
        setBusyId(null);
      }
    },
    [busyId],
  );

  return (
    <section>
      <h2 className="page-title">分享页管理</h2>

      {error && <div className="alert-error">{error}</div>}

      {/* 创建分享页表单 */}
      <form className="admin-form card" onSubmit={handleCreate} aria-label="创建分享页表单">
        <h3 className="form-title">创建分享页</h3>
        {formError && <div className="alert-error">{formError}</div>}
        <div className="admin-form-row">
          <div className="form-field">
            <label htmlFor="new-sp-name">分享页名称</label>
            <input
              id="new-sp-name"
              type="text"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              required
            />
          </div>
          <div className="form-field">
            <label htmlFor="new-sp-dialog">RAGFlow dialog_id</label>
            <input
              id="new-sp-dialog"
              type="text"
              value={form.ragflow_resource_id}
              onChange={(e) => setForm((f) => ({ ...f, ragflow_resource_id: e.target.value }))}
              placeholder="如 dialog-abc123"
              required
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={creating}>
            {creating ? '创建中…' : '创建分享页'}
          </button>
        </div>
      </form>

      {/* 分享页列表 */}
      <div className="admin-table-wrap">
        {pages === null && !error && <div className="loading">加载中…</div>}
        {pages !== null && pages.length === 0 && (
          <div className="empty-state card">暂无分享页</div>
        )}
        {pages !== null && pages.length > 0 && (
          <table className="admin-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>dialog_id</th>
                <th>类型</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {pages.map((p) => (
                <tr key={p.id} data-testid={`share-page-row-${p.id}`}>
                  <td>{p.name}</td>
                  <td className="mono">{p.ragflow_resource_id}</td>
                  <td>{p.ragflow_type}</td>
                  <td>
                    {p.enabled ? (
                      <span className="badge badge-success">启用</span>
                    ) : (
                      <span className="badge badge-danger">禁用</span>
                    )}
                  </td>
                  <td className="admin-actions">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleToggleEnabled(p)}
                      disabled={busyId === p.id}
                    >
                      {p.enabled ? '禁用' : '启用'}
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
