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
};
