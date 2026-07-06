/**
 * 分享页列表页 — 调 GET /share-pages 列出当前用户被授权的分享页,点击进入详情。
 *
 * 对应 Slice 9 验收点 2:列表页显示用户被授权的分享页(至少 sp_default)。
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, api, type SharePage } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import AppHeader from '../components/AppHeader';

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
    <div className="app-layout">
      <AppHeader username={user?.username} onLogout={logout} isAdmin={user?.is_admin ?? false} />
      <main className="app-main">
        <h2 className="page-title">我的分享页</h2>

        {error && <div className="alert-error">{error}</div>}

        {pages === null && !error && <div className="loading">加载中…</div>}

        {pages !== null && pages.length === 0 && (
          <div className="empty-state card">暂无被授权的分享页</div>
        )}

        {pages !== null && pages.length > 0 && (
          <ul className="share-page-list" role="list">
            {pages.map((p) => (
              <li key={p.id} className="share-page-item">
                <div>
                  <span className="name">{p.name}</span>
                  <span className="meta" style={{ marginLeft: 12 }}>
                    {p.ragflow_type} · {p.embed_type}
                  </span>
                </div>
                <Link className="btn btn-ghost" to={`/share-pages/${p.id}`}>
                  打开
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
