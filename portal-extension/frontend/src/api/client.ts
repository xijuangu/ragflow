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

// ===========================================================================
// Slice 11 管理员后台类型(对应后端 /admin/* 路由,见 portal/routes.py)
// ===========================================================================

/** 管理员视角的用户对象(对应 GET /admin/users 响应项)。 */
export interface AdminUser {
  id: string;
  username: string;
  email: string;
  is_admin: boolean;
  enabled: boolean;
  created_at: number;
  sso_provider: string | null;
  sso_external_id: string | null;
}

/** 管理员视角的用户组对象(对应 GET /admin/groups 响应项,含成员列表)。 */
export interface AdminGroup {
  id: string;
  name: string;
  created_at: number;
  member_count: number;
  members: string[];
}

/** 管理员视角的分享页对象(对应 GET /admin/share-pages 响应项)。 */
export interface AdminSharePage {
  id: string;
  name: string;
  ragflow_type: string;
  ragflow_resource_id: string;
  embed_type: string;
  enabled: boolean;
  created_at: number;
}

/** 授权主体类型 — 后端 Literal('user','group')。 */
export type SubjectType = 'user' | 'group';

/** 管理员视角的授权对象(对应 GET /admin/share-pages/:id/grants 响应项)。 */
export interface AdminGrant {
  share_page_id: string;
  subject_type: SubjectType;
  subject_id: string;
  permission: string;
}

/** 管理员视角的会话元数据(对应 GET /admin/sessions,默认不含正文)。 */
export interface AdminSessionMetadata {
  session_id: string;
  title: string;
  portal_user_id: string;
  share_page_id: string;
  ragflow_resource_id: string;
  created_at: number;
  last_active_at: number;
  deleted_at: number | null;
  message_count: number;
}

/** 管理员 elevated 查会话正文响应(元数据 + messages + reference)。 */
export interface AdminSessionElevated extends AdminSessionMetadata {
  messages: unknown[];
  reference: Record<string, unknown>;
}

/** 管理员视角的审计日志条目(对应 GET /admin/audit-logs)。 */
export interface AdminAuditLog {
  id: number;
  actor_user_id: string;
  action: string;
  target_type: string;
  target_id: string;
  at: number;
  meta: Record<string, unknown> | null;
}

/** 8 类敏感操作(供审计日志筛选下拉选项与 UI 触发入口对照)。 */
export const AUDIT_ACTIONS = [
  'login_success',
  'login_failure',
  'grant_create',
  'grant_revoke',
  'session_delete',
  'session_view_elevated',
  'user_enable',
  'user_disable',
] as const;
export type AuditAction = (typeof AUDIT_ACTIONS)[number];

/** GET /admin/sessions 查询参数。 */
export interface AdminSessionsQuery {
  user_id?: string;
  share_page_id?: string;
  since?: number;
  until?: number;
  keyword?: string;
  limit?: number;
}

/** GET /admin/audit-logs 查询参数。 */
export interface AdminAuditLogsQuery {
  actor_user_id?: string;
  action?: string;
  since?: number;
  until?: number;
  limit?: number;
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

  // -------------------------------------------------------------------------
  // Slice 11 管理员后台 API(均要求 is_admin,后端 require_admin 守卫)
  // -------------------------------------------------------------------------

  // 用户管理 ---------------------------------------------------------------

  /** 列出所有用户(GET /admin/users)。 */
  listAdminUsers(): Promise<{ users: AdminUser[] }> {
    return request<{ users: AdminUser[] }>('/admin/users');
  },

  /** 创建用户(POST /admin/users,201 成功)。 */
  createAdminUser(body: {
    username: string;
    email: string;
    password: string;
  }): Promise<AdminUser> {
    return request<AdminUser>('/admin/users', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** 启用/禁用用户(PATCH /admin/users/:id,写 user_enable/user_disable 审计)。 */
  updateAdminUser(userId: string, enabled: boolean): Promise<AdminUser> {
    return request<AdminUser>(`/admin/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
  },

  /** 硬删除用户(DELETE /admin/users/:id,级联删会话,无孤儿)。 */
  deleteAdminUser(userId: string): Promise<{ user_id: string; deleted: boolean }> {
    return request<{ user_id: string; deleted: boolean }>(
      `/admin/users/${encodeURIComponent(userId)}`,
      { method: 'DELETE' },
    );
  },

  // 用户组管理 -------------------------------------------------------------

  /** 列出所有用户组(含成员列表,GET /admin/groups)。 */
  listAdminGroups(): Promise<{ groups: AdminGroup[] }> {
    return request<{ groups: AdminGroup[] }>('/admin/groups');
  },

  /** 创建用户组(POST /admin/groups,201 成功)。 */
  createAdminGroup(name: string): Promise<AdminGroup> {
    return request<AdminGroup>('/admin/groups', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  },

  /** 添加用户到用户组(POST /admin/groups/:id/members)。 */
  addAdminGroupMember(
    groupId: string,
    userId: string,
  ): Promise<{ group_id: string; user_id: string; added: boolean }> {
    return request<{ group_id: string; user_id: string; added: boolean }>(
      `/admin/groups/${encodeURIComponent(groupId)}/members`,
      { method: 'POST', body: JSON.stringify({ user_id: userId }) },
    );
  },

  /** 从用户组移除用户(DELETE /admin/groups/:id/members/:user_id)。 */
  removeAdminGroupMember(
    groupId: string,
    userId: string,
  ): Promise<{ group_id: string; user_id: string; removed: boolean }> {
    return request<{ group_id: string; user_id: string; removed: boolean }>(
      `/admin/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(userId)}`,
      { method: 'DELETE' },
    );
  },

  // 分享页管理 -------------------------------------------------------------

  /** 列出所有分享页(GET /admin/share-pages,管理员视角,含未授权与禁用的)。 */
  listAdminSharePages(): Promise<{ share_pages: AdminSharePage[] }> {
    return request<{ share_pages: AdminSharePage[] }>('/admin/share-pages');
  },

  /** 创建分享页(POST /admin/share-pages,关联 RAGFlow dialog_id,201 成功)。 */
  createAdminSharePage(body: {
    name: string;
    ragflow_resource_id: string;
  }): Promise<AdminSharePage> {
    return request<AdminSharePage>('/admin/share-pages', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** 启用/禁用分享页(PATCH /admin/share-pages/:id)。 */
  updateAdminSharePage(
    sharePageId: string,
    enabled: boolean,
  ): Promise<AdminSharePage> {
    return request<AdminSharePage>(
      `/admin/share-pages/${encodeURIComponent(sharePageId)}`,
      { method: 'PATCH', body: JSON.stringify({ enabled }) },
    );
  },

  // 授权管理 ---------------------------------------------------------------

  /** 列出某分享页的所有授权(GET /admin/share-pages/:id/grants)。 */
  listAdminGrants(sharePageId: string): Promise<{ grants: AdminGrant[] }> {
    return request<{ grants: AdminGrant[] }>(
      `/admin/share-pages/${encodeURIComponent(sharePageId)}/grants`,
    );
  },

  /** 把分享页授权给用户或组(POST /admin/share-pages/:id/grants,201 成功,写 grant_create 审计)。 */
  createAdminGrant(
    sharePageId: string,
    body: { subject_type: SubjectType; subject_id: string; permission?: string },
  ): Promise<AdminGrant> {
    return request<AdminGrant>(
      `/admin/share-pages/${encodeURIComponent(sharePageId)}/grants`,
      { method: 'POST', body: JSON.stringify({ permission: 'use', ...body }) },
    );
  },

  /** 撤销授权(DELETE /share-pages/:id/grants/:subject_type/:subject_id,写 grant_revoke 审计)。
   *  注意:此端点路径前缀为 /share-pages(非 /admin),与后端 routes.py 保持一致。 */
  revokeGrant(
    sharePageId: string,
    subjectType: SubjectType,
    subjectId: string,
  ): Promise<{
    revoked: boolean;
    share_page_id: string;
    subject_type: SubjectType;
    subject_id: string;
    tokens_revoked: number;
  }> {
    return request<{
      revoked: boolean;
      share_page_id: string;
      subject_type: SubjectType;
      subject_id: string;
      tokens_revoked: number;
    }>(
      `/share-pages/${encodeURIComponent(sharePageId)}/grants/${encodeURIComponent(subjectType)}/${encodeURIComponent(subjectId)}`,
      { method: 'DELETE' },
    );
  },

  // 会话搜索与分级查看 -----------------------------------------------------

  /** 管理员搜索会话(按用户/分享页/时间/关键词,返回元数据,GET /admin/sessions)。 */
  listAdminSessions(query: AdminSessionsQuery = {}): Promise<{ sessions: AdminSessionMetadata[] }> {
    const qs = new URLSearchParams();
    if (query.user_id) qs.set('user_id', query.user_id);
    if (query.share_page_id) qs.set('share_page_id', query.share_page_id);
    if (query.since !== undefined) qs.set('since', String(query.since));
    if (query.until !== undefined) qs.set('until', String(query.until));
    if (query.keyword) qs.set('keyword', query.keyword);
    if (query.limit !== undefined) qs.set('limit', String(query.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request<{ sessions: AdminSessionMetadata[] }>(`/admin/sessions${suffix}`);
  },

  /** 管理员查会话详情(GET /admin/sessions/:sid)。
   *  - elevated=false(默认):只返回元数据,不写审计,不调 RAGFlow。
   *  - elevated=true:写 session_view_elevated 审计 + 调 RAGFlow 取正文,返回 {metadata, messages, reference}。
   *  UI 层 elevated=true 前需二次确认(window.confirm)。 */
  getAdminSession(
    sessionId: string,
    elevated = false,
  ): Promise<AdminSessionMetadata | AdminSessionElevated> {
    const suffix = elevated ? '?elevated=true' : '';
    return request<AdminSessionMetadata | AdminSessionElevated>(
      `/admin/sessions/${encodeURIComponent(sessionId)}${suffix}`,
    );
  },

  // 审计日志 ---------------------------------------------------------------

  /** 管理员查看审计日志(按 action/actor/时间过滤,GET /admin/audit-logs)。 */
  listAdminAuditLogs(query: AdminAuditLogsQuery = {}): Promise<{ audit_logs: AdminAuditLog[] }> {
    const qs = new URLSearchParams();
    if (query.actor_user_id) qs.set('actor_user_id', query.actor_user_id);
    if (query.action) qs.set('action', query.action);
    if (query.since !== undefined) qs.set('since', String(query.since));
    if (query.until !== undefined) qs.set('until', String(query.until));
    if (query.limit !== undefined) qs.set('limit', String(query.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request<{ audit_logs: AdminAuditLog[] }>(`/admin/audit-logs${suffix}`);
  },
};
