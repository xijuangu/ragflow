/**
 * 分享页详情页 — iframe 对话 + 「我的会话」侧栏(Slice 10 + Slice 16 扩展)。
 *
 * Slice 9:调 GET /share-pages/:id/embed-url 获取 iframe URL,渲染 iframe。
 *   - iframe URL 含 T_short(短期嵌入令牌),不含真实 beta Token。
 *   - 同源加载,cookie 自动携带,X-Frame-Options: SAMEORIGIN 由后端设置。
 *
 * Slice 10:左侧栏「我的会话」列表 + 重新打开 + 重命名/删除/新建。
 *   - 列表:GET /share-pages/:id/sessions(标题、时间、消息数)。
 *   - 重新打开:点击会话 → 调 embed-url → 在 iframe URL 追加 &session_id=<sid>
 *     (RAGFlow 前端原生读 URL 参数恢复历史消息 + 引用,无需改 RAGFlow 源码)。
 *   - 新建会话:POST /share-pages/:id/sessions 预创建 → 用返回的 iframe_url(已含 session_id)。
 *   - 重命名:PATCH(同步:RAGFlow 成功才更新门户 title)→ 乐观更新本地列表。
 *   - 删除:DELETE(双删)→ confirm → 乐观移除本地列表。
 *   - 用户只看到自己的会话(后端 list_sessions 按 portal_user_id 隔离)。
 *
 * Slice 16:widget 类型展示 snippet(可复制 iframe HTML)而非 iframe;
 *   agent 类型 iframe URL 走 /agent/share 路径(由后端构造,前端透明)。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, api, type EmbedUrlResponse, type SessionSummary } from '../api/client';
import { formatTime } from '../utils/formatTime';
import { useAuth } from '../auth/AuthContext';
import AppHeader from '../components/AppHeader';

/** 在 iframe URL 后追加 session_id 参数(RAGFlow 前端原生读 URL ?session_id= 恢复历史)。 */
function appendSessionId(url: string, sessionId: string): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}session_id=${encodeURIComponent(sessionId)}`;
}

/** Slice 25:会话列表轮询间隔(ms)。真实首问后需在 2s 内刷新左侧列表。 */
const SESSION_POLL_INTERVAL_MS = 2000;

export default function SharePageDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user, logout } = useAuth();

  // iframe / widget 状态
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [iframeNonce, setIframeNonce] = useState(0); // 强制 iframe 重载(同 URL 也重载)
  const [embedError, setEmbedError] = useState<string | null>(null);
  // Slice 16:widget 类型展示 snippet 而非 iframe
  const [snippet, setSnippet] = useState<string | null>(null);
  const [widgetUrl, setWidgetUrl] = useState<string | null>(null);
  const [isWidget, setIsWidget] = useState(false);
  const [copied, setCopied] = useState(false);

  // 会话列表状态
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);

  // 初始加载:embed-url(判断 embed_type + 取 iframe URL)+ 会话列表
  // Slice 22:回退 Slice 21 — fullscreen 类型改回只调 embed-url(不 precreate)。
  //   根因:RAGFlow 前端 use-send-shared-message.ts:77 的 session_id 来自 SSE 响应
  //   (derivedMessages[0].session_id),不从 URL ?session_id= 读。precreate 往 iframe
  //   URL 塞 session_id 无效,且 precreate 创建的 session 不会被 iframe 使用(孤儿)。
  //   改由网关在 SSE 代理时绑定 RAGFlow 实际创建的 session(见 gateway.py Slice 22)。
  //   widget 类型仍用 embed-url(返回 snippet,不需 session 归属)。
  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    (async () => {
      try {
        const embedRes = await api.getEmbedUrl(id);
        if (cancelled) return;
        // Slice 16:widget 类型返回 widget_url + snippet(无 iframe_url)
        if (embedRes.embed_type === 'widget' || embedRes.snippet) {
          setIsWidget(true);
          setSnippet(embedRes.snippet ?? null);
          setWidgetUrl(embedRes.widget_url ?? null);
          setIframeUrl(null);
          return;
        }
        // fullscreen 类型:用 embed-url 返回的 iframe_url(无 session_id — RAGFlow 前端
        // 不读 URL session_id,session 由网关在 SSE 代理时绑定)
        setIsWidget(false);
        setIframeUrl(embedRes.iframe_url ?? null);
        setSnippet(null);
        setWidgetUrl(null);
        setIframeNonce((n) => n + 1);
      } catch (e) {
        if (cancelled) return;
        setEmbedError(e instanceof ApiError ? e.message : '加载分享页失败');
      }
    })();

    (async () => {
      try {
        const res = await api.listSessions(id);
        if (!cancelled) setSessions(res.sessions);
      } catch (e) {
        if (cancelled) return;
        setSessionsError(e instanceof ApiError ? e.message : '会话列表加载失败');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [id]);

  // Slice 23:会话列表定时轮询(每 SESSION_POLL_INTERVAL_MS)— 检测 iframe 内发消息后
  // 网关 bind 的新 session。根因:原列表只在挂载时调一次 listSessions,iframe 发消息后
  // 不刷新 → 看不到新会话。轮询而非 postMessage:iframe 跨域(RAGFlow 前端)postMessage
  // 需改 RAGFlow 源码,轮询是零侵入方案(2s 内满足 Issue 25 的及时刷新验收)。
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const res = await api.listSessions(id);
        if (!cancelled) setSessions(res.sessions);
      } catch {
        // 轮询失败静默(不阻塞 iframe,不打扰用户)
      }
    }, SESSION_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [id]);

  /** 重新打开历史会话:调 embed-url 拿新 T_short,追加 session_id,重载 iframe。
   *  Slice 16:widget 类型无 iframe_url,不重载(widget snippet 是静态嵌入代码)。 */
  const handleReopen = useCallback(
    async (sessionId: string) => {
      if (!id || sessionBusy || isWidget) return;
      setSessionBusy(true);
      setActiveSessionId(sessionId);
      try {
        const res: EmbedUrlResponse = await api.getEmbedUrl(id);
        if (res.iframe_url) {
          setIframeUrl(appendSessionId(res.iframe_url, sessionId));
          setIframeNonce((n) => n + 1);
        }
      } catch (e) {
        setEmbedError(e instanceof ApiError ? e.message : '重新打开会话失败');
      } finally {
        setSessionBusy(false);
      }
    },
    [id, sessionBusy, isWidget],
  );

  /** 新建会话:POST 预创建,用返回的 iframe_url(已含 session_id)重载 iframe。
   *  Slice 16:widget 类型仍可新建会话(会话进入「我的会话」列表),但 iframe 不重载。
   *  Slice 23:用 useRef 做同步守卫(ref 赋值同步,非 state 异步),防止快速点击
   *           绕过 sessionBusy 守卫导致多次 precreateSession(弹多个会话)。 */
  const newSessionLockRef = useRef(false);
  const handleNewSession = useCallback(async () => {
    if (!id || sessionBusy || newSessionLockRef.current) return;
    newSessionLockRef.current = true;
    setSessionBusy(true);
    try {
      const res = await api.precreateSession(id);
      setActiveSessionId(res.session_id);
      if (!isWidget && res.iframe_url) {
        setIframeUrl(res.iframe_url);
        setIframeNonce((n) => n + 1);
      }
      // 预创建后会话已绑定当前用户,刷新列表使其出现
      try {
        const list = await api.listSessions(id);
        setSessions(list.sessions);
      } catch {
        // 列表刷新失败不阻断已加载的 iframe
      }
    } catch (e) {
      setSessionsError(e instanceof ApiError ? e.message : '新建会话失败');
    } finally {
      newSessionLockRef.current = false;
      setSessionBusy(false);
    }
  }, [id, sessionBusy, isWidget]);

  /** 重命名会话:prompt 输入新标题 → PATCH → 乐观更新本地列表标题。 */
  const handleRename = useCallback(
    async (sessionId: string, currentTitle: string) => {
      if (!id) return;
      const title = window.prompt('重命名会话', currentTitle);
      if (title === null) return; // 用户取消
      const trimmed = title.trim();
      if (!trimmed || trimmed === currentTitle) return;
      try {
        await api.renameSession(id, sessionId, trimmed);
        setSessions((prev) =>
          prev ? prev.map((s) => (s.session_id === sessionId ? { ...s, title: trimmed } : s)) : prev,
        );
      } catch (e) {
        setSessionsError(e instanceof ApiError ? e.message : '重命名失败');
      }
    },
    [id],
  );

  /** 删除会话:confirm → DELETE → 乐观移除本地列表项。 */
  const handleDelete = useCallback(
    async (sessionId: string) => {
      if (!id) return;
      if (!window.confirm('确定删除该会话?删除后不可恢复。')) return;
      try {
        await api.deleteSession(id, sessionId);
        setSessions((prev) => (prev ? prev.filter((s) => s.session_id !== sessionId) : prev));
        if (activeSessionId === sessionId) {
          setActiveSessionId(null);
        }
      } catch (e) {
        setSessionsError(e instanceof ApiError ? e.message : '删除失败');
      }
    },
    [id, activeSessionId],
  );

  /** Slice 16:复制 snippet 到剪贴板。 */
  const handleCopySnippet = useCallback(async () => {
    if (!snippet) return;
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 剪贴板 API 不可用(如非 HTTPS),降级提示用户手动复制
      setSessionsError('剪贴板不可用,请手动选择文本复制');
    }
  }, [snippet]);

  return (
    <div className="app-layout">
      <AppHeader username={user?.username} onLogout={logout} isAdmin={user?.is_admin ?? false} />
      <main className="app-main">
        <div className="back-link">
          <Link to="/share-pages">← 返回列表</Link>
        </div>
        <h2 className="page-title">{isWidget ? '悬浮组件嵌入' : '分享页对话'}</h2>

        {embedError && <div className="alert-error">{embedError}</div>}

        <div className="detail-grid">
          <aside className="sessions-sidebar" aria-label="我的会话">
            <div className="sidebar-header">
              <h3>我的会话</h3>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleNewSession}
                disabled={sessionBusy}
              >
                新建会话
              </button>
            </div>

            {sessionsError && <div className="alert-error alert-sm">{sessionsError}</div>}

            {sessions === null && !sessionsError && <div className="sidebar-loading">加载中…</div>}

            {sessions !== null && sessions.length === 0 && (
              <div className="empty-state empty-state-sm">暂无会话</div>
            )}

            {sessions !== null && sessions.length > 0 && (
              <ul className="session-list" role="list">
                {sessions.map((s) => {
                  const isActive = s.session_id === activeSessionId;
                  return (
                    <li
                      key={s.session_id}
                      data-session-item
                      className={`session-item${isActive ? ' session-item-active' : ''}`}
                    >
                      <button
                        type="button"
                        className="session-main"
                        onClick={() => handleReopen(s.session_id)}
                        disabled={sessionBusy || isWidget}
                        title={s.title || '(未命名)'}
                      >
                        <span className="session-title">{s.title || '(未命名)'}</span>
                        <span className="session-meta">
                          {formatTime(s.last_active_at, 'datetime')} · {s.message_count} 条消息
                        </span>
                      </button>
                      <div className="session-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs"
                          onClick={() => handleRename(s.session_id, s.title || '')}
                          disabled={sessionBusy}
                        >
                          重命名
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger btn-xs"
                          onClick={() => handleDelete(s.session_id)}
                          disabled={sessionBusy}
                        >
                          删除
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </aside>

          <div className="iframe-container">
            {/* Slice 16:widget 类型展示 snippet 与复制按钮(替代 iframe) */}
            {isWidget && snippet && (
              <div className="widget-snippet-panel" data-testid="widget-snippet-panel">
                <h3>悬浮组件嵌入代码</h3>
                <p className="snippet-hint">
                  将以下 HTML 代码复制粘贴到任意页面即可加载悬浮组件。widget URL:
                  <code className="mono">{widgetUrl}</code>
                </p>
                <div className="snippet-actions">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={handleCopySnippet}
                  >
                    {copied ? '已复制 ✓' : '复制 snippet'}
                  </button>
                </div>
                <pre className="snippet-code" data-testid="widget-snippet-code">
                  <code>{snippet}</code>
                </pre>
              </div>
            )}
            {/* fullscreen 类型:渲染 iframe(chat 走 /chats/share,agent 走 /agent/share) */}
            {!isWidget && iframeUrl && (
              <iframe
                key={iframeNonce}
                src={iframeUrl}
                title="RAGFlow 对话"
                allow="clipboard-read; clipboard-write"
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
