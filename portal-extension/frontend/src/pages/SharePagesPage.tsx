/**
 * 分享页列表页 — 调 GET /share-pages 列出当前用户被授权的分享页,点击进入详情。
 *
 * Slice 56:从 `share-page-list` 列表迁移到 `grid-cards` + `share-card` 卡片网格
 *   (设计稿 `project-materials/portal-ui-redesign/share-list.html`)。逻辑不动:GET /share-pages + Link 到详情。
 *
 * Issue 67:卡片精简 — 删文件夹图标/状态 badge/「点击打开」提示文案,「打开」改实心按钮,
 *   页头仅留标题(去 kicker 面包屑与 sub 描述)。
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
            <h1>我的分享页</h1>
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
                  <Link
                    className="btn btn-primary btn-sm"
                    to={`/share-pages/${p.id}`}
                    state={{ sharePageName: p.name }}
                  >
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
