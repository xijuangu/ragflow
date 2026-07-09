/**
 * 会话搜索页(Slice 11,验收点 6)— 按用户/分享页/关键词搜索 + elevated 二次确认查正文。
 *
 * 对应后端:
 *   - GET /admin/sessions?user_id=&share_page_id=&since=&until=&keyword=&limit=
 *     返回 { sessions: [元数据,不含正文] }
 *   - GET /admin/sessions/:sid?elevated=true(写 session_view_elevated 审计 + 调 RAGFlow 取正文)
 *     返回 { ...元数据, messages: [...], reference: {...} }
 *
 * UI 流程:
 *   1. 默认加载全部会话元数据(分页/过滤在搜索表单)
 *   2. 每行带「查看正文」按钮 → window.confirm("以管理员身份查看 — 此操作将记录")
 *      → 确认 → 调 elevated 端点 → 弹窗显示 messages
 *
 * 对应 PRD D7a:管理员默认只看元数据,查正文需二次确认 + 写审计(后端在 elevated=true 时写)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import {
  ApiError,
  api,
  type AdminSessionElevated,
  type AdminSessionMetadata,
  type AdminSharePage,
  type AdminUser,
} from '../../api/client';
import { formatTime } from '../../utils/formatTime';
import { useAdminList } from '../../hooks/useAdminList';

interface SessionMessage {
  role: string;
  content: string;
}

export default function SessionsAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [sharePages, setSharePages] = useState<AdminSharePage[]>([]);
  const [sessions, setSessions] = useState<AdminSessionMetadata[] | null>(null);
  const { error, setError } = useAdminList(
    async () => {
      const [usersRes, pagesRes] = await Promise.all([
        api.listAdminUsers(),
        api.listAdminSharePages(),
      ]);
      return { users: usersRes.users, sharePages: pagesRes.share_pages };
    },
    {
      errorMessage: '加载用户/分享页失败',
      onSuccess: (d) => {
        setUsers(d.users);
        setSharePages(d.sharePages);
      },
    },
  );

  // 筛选表单
  const [filterUserId, setFilterUserId] = useState('');
  const [filterSharePageId, setFilterSharePageId] = useState('');
  const [filterKeyword, setFilterKeyword] = useState('');

  // elevated 弹窗
  const [elevatedLoading, setElevatedLoading] = useState(false);
  const [elevatedData, setElevatedData] = useState<AdminSessionElevated | null>(null);
  const [elevatedError, setElevatedError] = useState<string | null>(null);

  const userMap = useMemo(() => {
    const m = new Map<string, AdminUser>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const sharePageMap = useMemo(() => {
    const m = new Map<string, AdminSharePage>();
    for (const p of sharePages) m.set(p.id, p);
    return m;
  }, [sharePages]);

  // 初始加载用户/分享页下拉数据由 useAdminList 完成(上方);此处仅保留会话加载
  const loadSessions = useCallback(
    async (opts: { userId?: string; sharePageId?: string; keyword?: string } = {}) => {
      setSessions(null);
      setError(null);
      try {
        const res = await api.listAdminSessions({
          user_id: opts.userId || undefined,
          share_page_id: opts.sharePageId || undefined,
          keyword: opts.keyword || undefined,
        });
        setSessions(res.sessions);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '加载会话列表失败');
      }
    },
    [setError],
  );

  // 首次加载会话
  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const handleSearch = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      await loadSessions({
        userId: filterUserId,
        sharePageId: filterSharePageId,
        keyword: filterKeyword.trim(),
      });
    },
    [filterUserId, filterSharePageId, filterKeyword, loadSessions],
  );

  const handleViewElevated = useCallback(
    async (session: AdminSessionMetadata) => {
      if (elevatedLoading) return;
      const confirmed = window.confirm(
        '以管理员身份查看 — 此操作将记录',
      );
      if (!confirmed) return;
      setElevatedLoading(true);
      setElevatedError(null);
      try {
        const res = await api.getAdminSession(session.session_id, true);
        setElevatedData(res as AdminSessionElevated);
      } catch (e) {
        setElevatedError(e instanceof ApiError ? e.message : '取回会话失败');
      } finally {
        setElevatedLoading(false);
      }
    },
    [elevatedLoading],
  );

  const closeElevated = useCallback(() => {
    setElevatedData(null);
    setElevatedError(null);
  }, []);

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 会话搜索</div>
          <h1>会话搜索</h1>
          <div className="sub">按用户、分享页或关键词检索会话元数据。查看正文需二次确认并写入审计日志。</div>
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {/* 会话列表(含筛选) */}
      <div className="card card-table">
        <form className="filters" onSubmit={handleSearch} aria-label="会话搜索表单">
          <div className="form-field">
            <label htmlFor="filter-user">用户</label>
            <select
              id="filter-user"
              value={filterUserId}
              onChange={(e) => setFilterUserId(e.target.value)}
            >
              <option value="">全部用户</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="filter-share-page">分享页</label>
            <select
              id="filter-share-page"
              value={filterSharePageId}
              onChange={(e) => setFilterSharePageId(e.target.value)}
            >
              <option value="">全部分享页</option>
              {sharePages.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="filter-keyword">关键词</label>
            <input
              id="filter-keyword"
              type="text"
              value={filterKeyword}
              onChange={(e) => setFilterKeyword(e.target.value)}
              placeholder="按会话标题模糊匹配"
            />
          </div>
          <button type="submit" className="btn btn-primary">
            搜索
          </button>
        </form>
        <div className="card-body">
          <div className="table-wrap">
            {sessions === null && !error && <div className="loading">加载中…</div>}
            {sessions !== null && sessions.length === 0 && (
              <div className="empty-state">暂无会话</div>
            )}
            {sessions !== null && sessions.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>标题</th>
                    <th>用户</th>
                    <th>分享页</th>
                    <th>创建时间</th>
                    <th>最近活跃</th>
                    <th>消息数</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.session_id} data-testid={`session-row-${s.session_id}`}>
                      <td>{s.title || '(无标题)'}</td>
                      <td>{userMap.get(s.portal_user_id)?.username ?? s.portal_user_id}</td>
                      <td>{sharePageMap.get(s.share_page_id)?.name ?? s.share_page_id}</td>
                      <td className="mono">{formatTime(s.created_at, 'datetime')}</td>
                      <td className="mono">{formatTime(s.last_active_at, 'datetime')}</td>
                      <td className="mono">{s.message_count}</td>
                      <td className="admin-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => handleViewElevated(s)}
                          disabled={elevatedLoading}
                        >
                          {elevatedLoading ? '加载中…' : '查看正文'}
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

      {/* elevated 弹窗 — 显示消息正文(modal,非 drawer) */}
      {(elevatedData || elevatedError) && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={closeElevated}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>以管理员身份查看</h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={closeElevated}
                aria-label="关闭"
              >
                关闭
              </button>
            </div>
            {elevatedError && <div className="alert-error">{elevatedError}</div>}
            {elevatedData && (
              <div className="modal-body">
                <p className="modal-meta">
                  会话: {elevatedData.title || '(无标题)'}
                </p>
                <ul className="message-list">
                  {(elevatedData.messages as SessionMessage[]).map((m, i) => (
                    <li key={i} className={`message-item message-${m.role}`}>
                      <span className="message-role">{m.role}</span>
                      <span className="message-content">{m.content}</span>
                    </li>
                  ))}
                  {(elevatedData.messages as SessionMessage[]).length === 0 && (
                    <li className="empty-state-sm">无消息</li>
                  )}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
