/**
 * API 客户端 — 封装对门户后端的 fetch 调用。
 *
 * 所有请求携带同源 cookie(credentials: 'include'),开发时通过 Vite proxy
 * 保持同源,生产时由 FastAPI StaticFiles 同源托管。
 *
 * iframe URL 通过 GET /share-pages/:id/embed-url 获取,前端不直接拼接 beta Token
 * (对应 Slice 9 验收点 4:iframe URL 不含真实 beta Token)。
 */

export interface SharePage {
  id: string;
  name: string;
  ragflow_type: string;
  ragflow_resource_id: string;
  embed_type: string;
  enabled: boolean;
  created_at: number;
}

export interface UserInfo {
  username: string;
  is_admin: boolean;
}

export interface EmbedUrlResponse {
  iframe_url: string;
  share_page_id: string;
  expires_in: number;
}

/** 「我的会话」列表项 — 对应 GET /share-pages/:id/sessions 响应。 */
export interface SessionSummary {
  session_id: string;
  title: string;
  created_at: number;
  last_active_at: number;
  message_count: number;
}

/** POST /share-pages/:id/sessions 预创建会话响应。 */
export interface PrecreateSessionResponse {
  session_id: string;
  iframe_url: string;
  share_page_id: string;
}

/** PATCH /share-pages/:id/sessions/:sid 重命名响应。 */
export interface RenameSessionResponse {
  session_id: string;
  title: string;
}

/** DELETE /share-pages/:id/sessions/:sid 删除响应。 */
export interface DeleteSessionResponse {
  session_id: string;
  deleted: boolean;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // 响应体非 JSON,沿用 statusText
    }
    throw new ApiError(res.status, String(detail));
  }

  // 204 No Content 或空响应体
  const text = await res.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

export const api = {
  login(username: string, password: string): Promise<UserInfo> {
    return request<UserInfo>('/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
  },

  logout(): Promise<{ logged_out: boolean }> {
    return request<{ logged_out: boolean }>('/logout', { method: 'POST' });
  },

  me(): Promise<UserInfo> {
    return request<UserInfo>('/me');
  },

  listSharePages(): Promise<{ share_pages: SharePage[] }> {
    return request<{ share_pages: SharePage[] }>('/share-pages');
  },

  getEmbedUrl(id: string): Promise<EmbedUrlResponse> {
    return request<EmbedUrlResponse>(`/share-pages/${encodeURIComponent(id)}/embed-url`);
  },

  /** 列出当前用户在该分享页下的会话(标题、时间、消息数)。 */
  listSessions(sharePageId: string): Promise<{ sessions: SessionSummary[] }> {
    return request<{ sessions: SessionSummary[] }>(
      `/share-pages/${encodeURIComponent(sharePageId)}/sessions`,
    );
  },

  /** 预创建空会话,返回 session_id 与带 session_id 的 iframe_url(对应「新建会话」)。 */
  precreateSession(sharePageId: string): Promise<PrecreateSessionResponse> {
    return request<PrecreateSessionResponse>(
      `/share-pages/${encodeURIComponent(sharePageId)}/sessions`,
      { method: 'POST' },
    );
  },

  /** 重命名会话(同步:RAGFlow 成功才更新门户 title)。 */
  renameSession(
    sharePageId: string,
    sessionId: string,
    title: string,
  ): Promise<RenameSessionResponse> {
    return request<RenameSessionResponse>(
      `/share-pages/${encodeURIComponent(sharePageId)}/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'PATCH', body: JSON.stringify({ title }) },
    );
  },

  /** 删除会话(双删:RAGFlow 成功 → 门户硬删除;失败 → 标记 deleted_at)。 */
  deleteSession(sharePageId: string, sessionId: string): Promise<DeleteSessionResponse> {
    return request<DeleteSessionResponse>(
      `/share-pages/${encodeURIComponent(sharePageId)}/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' },
    );
  },
};
