/**
 * 分享页详情页 — 调 GET /share-pages/:id/embed-url 获取 iframe URL,渲染 iframe。
 *
 * 对应 Slice 9 验收点 3:点击分享页进入详情页,iframe 加载 RAGFlow 对话界面。
 * 对应 Slice 9 验收点 4:iframe URL 不含真实 beta Token(前端只通过 embed-url 端点拿 URL,
 *   不直接拼接 beta Token)。
 *
 * iframe 同源加载,门户 cookie 自动携带,X-Frame-Options: SAMEORIGIN 由后端中间件设置。
 */
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, api, type EmbedUrlResponse } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import AppHeader from '../components/AppHeader';

export default function SharePageDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user, logout } = useAuth();
  const [embed, setEmbed] = useState<EmbedUrlResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getEmbedUrl(id);
        if (!cancelled) setEmbed(res);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError) {
          setError(e.message);
        } else {
          setError('加载分享页失败');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="app-layout">
      <AppHeader username={user?.username} onLogout={logout} />
      <main className="app-main">
        <div className="back-link">
          <Link to="/share-pages">← 返回列表</Link>
        </div>
        <h2 className="page-title">分享页对话</h2>

        {error && <div className="alert-error">{error}</div>}

        {!embed && !error && <div className="loading">加载中…</div>}

        {embed && (
          <div className="iframe-container">
            {/*
              iframe URL 由后端 /share-pages/:id/embed-url 端点返回,含 T_short(短期嵌入令牌),
              不含真实 beta Token。同源加载,cookie 自动携带。
            */}
            <iframe
              src={embed.iframe_url}
              title="RAGFlow 对话"
              allow="clipboard-read; clipboard-write"
            />
          </div>
        )}
      </main>
    </div>
  );
}
