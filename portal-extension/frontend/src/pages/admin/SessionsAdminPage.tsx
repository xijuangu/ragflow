/**
 * 会话搜索页(Slice 11,验收点 6;Issue 87 改为前端内存检索)— 全量加载 + 搜索/筛选/排序 + elevated 二次确认查正文。
 *
 * 对应后端:
 *   - GET /admin/sessions(全量元数据,不含正文;Issue 87 起筛选/排序全在浏览器内存完成,§12.1)
 *   - GET /admin/sessions/:sid?elevated=true(写 session_view_elevated 审计 + 调 RAGFlow 取正文)
 *     返回 { ...元数据, messages: [...], reference: {...} }
 *
 * UI 流程:
 *   1. 一次性加载全部会话元数据 + 用户/分享页(用于名称解析与筛选选项)
 *   2. 工具栏:搜索框(标题 + 用户名)、用户/分享页筛选下拉、表头三态排序
 *   3. 每行带「查看正文」按钮 → 统一 warning 弹窗展示审计后果
 *      → 确认 → 调 elevated 端点 → 弹窗显示 messages
 *
 * 对应 PRD D7a:管理员默认只看元数据,查正文需二次确认 + 写审计(后端在 elevated=true 时写)。
 */
import { useCallback, useMemo, useState } from 'react';
import {
  ApiError,
  api,
  type AdminSessionElevated,
  type AdminSessionMetadata,
} from '../../api/client';
import { formatTime } from '../../utils/formatTime';
import { useAdminList, useAdminListView } from '../../hooks/useAdminList';
import type { AdminListViewConfig } from '../../hooks/useAdminList';
import ConfirmDialog from '../../components/ConfirmDialog';
import { MobileCard, MobileCardList } from '../../components/MobileCards';
import { AdminListToolbar, SortableTh } from '../../components/AdminListControls';

interface SessionMessage {
  role: string;
  content: string;
}

export default function SessionsAdminPage() {
  // Issue 87:全量加载(用户/分享页/会话一次性并行),搜索/筛选/排序在内存完成
  const { data, error } = useAdminList(
    async () => {
      const [usersRes, pagesRes, sessionsRes] = await Promise.all([
        api.listAdminUsers(),
        api.listAdminSharePages(),
        api.listAdminSessions({ limit: 10000 }),
      ]);
      return {
        users: usersRes.users,
        sharePages: pagesRes.share_pages,
        sessions: sessionsRes.sessions,
      };
    },
    { errorMessage: '加载会话列表失败' },
  );
  const users = useMemo(() => data?.users ?? [], [data]);
  const sharePages = useMemo(() => data?.sharePages ?? [], [data]);
  const sessions = data?.sessions ?? null;

  // elevated 弹窗
  const [elevatedLoading, setElevatedLoading] = useState(false);
  const [elevatedData, setElevatedData] = useState<AdminSessionElevated | null>(null);
  const [elevatedError, setElevatedError] = useState<string | null>(null);
  const [viewTarget, setViewTarget] = useState<AdminSessionMetadata | null>(null);

  const userMap = useMemo(() => {
    const m = new Map<string, (typeof users)[number]>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const sharePageMap = useMemo(() => {
    const m = new Map<string, (typeof sharePages)[number]>();
    for (const p of sharePages) m.set(p.id, p);
    return m;
  }, [sharePages]);

  // Issue 87:搜索列 = 会话标题 + 用户名;筛选 = 用户/分享页;排序 = 标题/创建时间/最近活跃/消息数
  const sessionsViewConfig: AdminListViewConfig<AdminSessionMetadata> = {
    searchColumns: [
      (s) => s.title,
      (s) => userMap.get(s.portal_user_id)?.username ?? s.portal_user_id,
    ],
    filters: [
      {
        key: 'user',
        label: '用户筛选',
        control: 'combobox',
        options: users.map((u) => ({ value: u.id, label: u.username, sublabel: u.email })),
        match: (s, v) => s.portal_user_id === v,
      },
      {
        key: 'share_page',
        label: '分享页筛选',
        control: 'combobox',
        options: sharePages.map((p) => ({ value: p.id, label: p.name })),
        match: (s, v) => s.share_page_id === v,
      },
    ],
    sortColumns: [
      { key: 'title', label: '标题', compare: (a, b) => (a.title || '').localeCompare(b.title || '') },
      { key: 'created_at', label: '创建时间', compare: (a, b) => a.created_at - b.created_at },
      { key: 'last_active_at', label: '最近活跃', compare: (a, b) => a.last_active_at - b.last_active_at },
      { key: 'message_count', label: '消息数', compare: (a, b) => a.message_count - b.message_count },
    ],
  };
  const {
    view: visibleSessions,
    search,
    setSearch,
    filterValues,
    setFilterValue,
    sort,
    toggleSort,
  } = useAdminListView(sessions, sessionsViewConfig);

  const confirmViewElevated = useCallback(
    async () => {
      if (elevatedLoading || !viewTarget) return;
      const sessionId = viewTarget.session_id;
      setElevatedLoading(true);
      setElevatedError(null);
      setViewTarget(null);
      try {
        const res = await api.getAdminSession(sessionId, true);
        setElevatedData(res as AdminSessionElevated);
      } catch (e) {
        setElevatedError(e instanceof ApiError ? e.message : '取回会话失败');
      } finally {
        setElevatedLoading(false);
      }
    },
    [elevatedLoading, viewTarget],
  );

  const closeElevated = useCallback(() => {
    setElevatedData(null);
    setElevatedError(null);
  }, []);

  const renderViewAction = (session: AdminSessionMetadata) => (
    <button
      type="button"
      className="btn btn-outline btn-sm"
      onClick={() => setViewTarget(session)}
      disabled={elevatedLoading}
    >
      {elevatedLoading ? '加载中…' : '查看正文'}
    </button>
  );

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

      {/* 会话列表(搜索/筛选/排序全在内存完成,Issue 87) */}
      <div className="card card-table">
        <AdminListToolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel="搜索会话"
          searchPlaceholder="按会话标题或用户名搜索"
          filters={sessionsViewConfig.filters}
          filterValues={filterValues}
          onFilterChange={setFilterValue}
        />
        <div className="card-body">
          {sessions === null && !error && <div className="loading">加载中…</div>}
          {sessions !== null && sessions.length === 0 && (
            <div className="empty-state">暂无会话</div>
          )}
          {sessions !== null && sessions.length > 0 && visibleSessions !== null && visibleSessions.length === 0 && (
            <div className="empty-state">无匹配结果</div>
          )}
          <div className="table-wrap">
            {visibleSessions !== null && visibleSessions.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <SortableTh columnKey="title" label="标题" sort={sort} onToggle={toggleSort} />
                    <th>用户</th>
                    <th>分享页</th>
                    <SortableTh columnKey="created_at" label="创建时间" sort={sort} onToggle={toggleSort} />
                    <SortableTh columnKey="last_active_at" label="最近活跃" sort={sort} onToggle={toggleSort} />
                    <SortableTh columnKey="message_count" label="消息数" sort={sort} onToggle={toggleSort} />
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleSessions.map((s) => (
                    <tr key={s.session_id} data-testid={`session-row-${s.session_id}`}>
                      <td>{s.title || '(无标题)'}</td>
                      <td>{userMap.get(s.portal_user_id)?.username ?? s.portal_user_id}</td>
                      <td>{sharePageMap.get(s.share_page_id)?.name ?? s.share_page_id}</td>
                      <td className="mono">{formatTime(s.created_at, 'datetime')}</td>
                      <td className="mono">{formatTime(s.last_active_at, 'datetime')}</td>
                      <td className="mono">{s.message_count}</td>
                      <td>
                        <div className="admin-actions">{renderViewAction(s)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {visibleSessions !== null && visibleSessions.length > 0 && (
            <MobileCardList label="会话列表">
              {visibleSessions.map((s) => (
                <MobileCard
                  key={s.session_id}
                  title={s.title || '(无标题)'}
                  testId={`mobile-session-${s.session_id}`}
                  fields={[
                    { label: '用户', value: userMap.get(s.portal_user_id)?.username ?? s.portal_user_id },
                    { label: '分享页', value: sharePageMap.get(s.share_page_id)?.name ?? s.share_page_id },
                    { label: '最近活跃', value: <span className="mono">{formatTime(s.last_active_at, 'datetime')}</span> },
                    { label: '消息数', value: <span className="mono">{s.message_count}</span> },
                  ]}
                  actions={renderViewAction(s)}
                />
              ))}
            </MobileCardList>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={viewTarget !== null}
        title="查看会话正文"
        message={`将以管理员身份查看「${viewTarget?.title || '(无标题)'}」的完整正文。`}
        confirmText="确认查看"
        variant="warning"
        details={['仅在业务需要时查看', '此操作将记录到审计日志']}
        busy={elevatedLoading}
        onConfirm={confirmViewElevated}
        onCancel={() => setViewTarget(null)}
      />

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
