/**
 * 分享页管理页(Slice 11,验收点 4 + Slice 16 扩展)— 列表 / 创建 / 启用禁用。
 *
 * 对应后端:
 *   - GET /admin/share-pages(列表,含未授权与禁用的)
 *   - POST /admin/share-pages(创建,关联 RAGFlow dialog_id/agent_id,201)
 *   - PATCH /admin/share-pages/:id(启用/禁用)
 *
 * Slice 16:embed_type/ragflow_type 开放选择器(D9 一期固定值已扩展)。
 *   - embed_type:fullscreen(全屏 iframe)/ widget(悬浮组件 snippet)
 *   - ragflow_type:chat(/chat/share)/ agent(/agent/share)
 */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminSharePage } from '../../api/client';

interface SharePageFormState {
  name: string;
  ragflow_resource_id: string;
  embed_type: 'fullscreen' | 'widget';
  ragflow_type: 'chat' | 'agent';
}

const DEFAULT_FORM: SharePageFormState = {
  name: '',
  ragflow_resource_id: '',
  embed_type: 'fullscreen',
  ragflow_type: 'chat',
};

export default function SharePagesAdminPage() {
  const [pages, setPages] = useState<AdminSharePage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [form, setForm] = useState<SharePageFormState>(DEFAULT_FORM);
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
        setFormError('分享页名称与资源 ID 均不能为空');
        return;
      }
      setCreating(true);
      try {
        const created = await api.createAdminSharePage({
          name,
          ragflow_resource_id,
          embed_type: form.embed_type,
          ragflow_type: form.ragflow_type,
        });
        setPages((prev) => (prev ? [...prev, created] : [created]));
        setForm(DEFAULT_FORM);
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
            <label htmlFor="new-sp-dialog">RAGFlow 资源 ID</label>
            <input
              id="new-sp-dialog"
              type="text"
              value={form.ragflow_resource_id}
              onChange={(e) => setForm((f) => ({ ...f, ragflow_resource_id: e.target.value }))}
              placeholder="chat 类型填 dialog_id;agent 类型填 agent_id"
              required
            />
          </div>
          <div className="form-field">
            <label htmlFor="new-sp-embed-type">嵌入类型</label>
            <select
              id="new-sp-embed-type"
              value={form.embed_type}
              onChange={(e) =>
                setForm((f) => ({ ...f, embed_type: e.target.value as 'fullscreen' | 'widget' }))
              }
            >
              <option value="fullscreen">fullscreen(全屏 iframe)</option>
              <option value="widget">widget(悬浮组件 snippet)</option>
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="new-sp-ragflow-type">RAGFlow 类型</label>
            <select
              id="new-sp-ragflow-type"
              value={form.ragflow_type}
              onChange={(e) =>
                setForm((f) => ({ ...f, ragflow_type: e.target.value as 'chat' | 'agent' }))
              }
            >
              <option value="chat">chat(对话助手)</option>
              <option value="agent">agent(Agent 工作流)</option>
            </select>
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
                <th>资源 ID</th>
                <th>嵌入类型</th>
                <th>RAGFlow 类型</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {pages.map((p) => (
                <tr key={p.id} data-testid={`share-page-row-${p.id}`}>
                  <td>{p.name}</td>
                  <td className="mono">{p.ragflow_resource_id}</td>
                  <td>{p.embed_type}</td>
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
