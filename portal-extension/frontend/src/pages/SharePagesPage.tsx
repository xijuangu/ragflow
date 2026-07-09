/**
 * 分享页列表页 — 调 GET /share-pages 列出当前用户被授权的分享页,点击进入详情。
 *
 * Slice 56:从 `share-page-list` 列表迁移到 `grid-cards` + `share-card` 卡片网格
 *   (设计稿 `portal-ui-redesign/share-list.html`)。page-title 升级为 page-head
 *   (kicker + h1 + sub)。逻辑不动:GET /share-pages + Link 到详情。
 *
 * 对应 Slice 9 验收点 2:列表页显示用户被授权的分享页(至少 sp_default)。
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, api, type SharePage } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import AppHeader from '../components/AppHeader';
import { formatTime } from '../utils/formatTime';

export default function SharePagesPage() {
  const { user, logout } = useAuth();
  const [pages, setPages] = useState<SharePage[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listSharePages();
        if (!cancelled) setPages(res.share_pages);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 403) {
          // 会话失效,触发刷新让 AuthContext 重新探测
          setError('会话已失效,请重新登录');
        } else {
          setError(e instanceof Error ? e.message : '加载分享页失败');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <AppHeader username={user?.username} onLogout={logout} isAdmin={user?.is_admin ?? false} />
      <main className="main">
        <div className="page-head">
          <div>
            <div className="kicker">工作区 / 分享页</div>
            <h1>我的分享页</h1>
            <div className="sub">以下是管理员分享给你的 RAGFlow 知识库,点击「打开」即可开始对话。</div>
          </div>
        </div>

        {error && <div className="alert-error">{error}</div>}

        {pages === null && !error && <div className="loading">加载中…</div>}

        {pages !== null && pages.length === 0 && (
          <div className="empty-state">暂无被授权的分享页</div>
        )}

        {pages !== null && pages.length > 0 && (
          <div className="grid-cards">
            {pages.map((p) => (
              <div key={p.id} className="share-card">
                <div className="sc-head">
                  <div className="sc-icon">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
                      <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-7l-2-2H5a2 2 0 0 0-2 2z" />
                    </svg>
                  </div>
                  <span className={`badge ${p.enabled ? 'b-active' : 'b-archived'}`}>
                    {p.enabled ? '活跃' : '已停用'}
                  </span>
                </div>
                <div>
                  <h3>{p.name}</h3>
                  <div className="sc-desc">
                    类型 {p.ragflow_type} · 嵌入 {p.embed_type}
                  </div>
                </div>
                <div className="sc-meta">
                  <div className="meta-row">
                    <span>RAGFlow 类型</span>
                    <b>{p.ragflow_type}</b>
                  </div>
                  <div className="meta-row">
                    <span>嵌入方式</span>
                    <b>{p.embed_type}</b>
                  </div>
                  <div className="meta-row">
                    <span>创建时间</span>
                    <b className="mono">{formatTime(p.created_at, 'date')}</b>
                  </div>
                </div>
                <div className="sc-foot">
                  <span className="sc-hint">
                    点击「打开」进入对话
                  </span>
                  <Link className="btn-link" to={`/share-pages/${p.id}`}>
                    打开
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </>
  );
}
