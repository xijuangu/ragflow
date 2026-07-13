/**
 * 会话搜索页测试(Slice 11,验收点 6)— 验证按多维度搜索 + elevated 二次确认 + 弹窗显示正文。
 *
 * 对应 Issue 11 验收点 6:
 *   - 管理员能按多维度搜索会话(用户/分享页/关键词)
 *   - 默认只看元数据
 *   - 查正文需统一 warning 弹窗二次确认,确认后调 elevated 端点(写审计)
 *   - 弹窗显示消息正文
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SessionsAdminPage from '../src/pages/admin/SessionsAdminPage';
import { getFetchCalls, mockFetch } from './setup';

const USERS_RESPONSE = {
  users: [
    {
      id: 'u_admin',
      username: 'admin',
      email: 'admin@example.com',
      is_admin: true,
      enabled: true,
      created_at: 1700000000,
      sso_provider: null,
      sso_external_id: null,
    },
    {
      id: 'u_user1',
      username: 'user1',
      email: 'user1@example.com',
      is_admin: false,
      enabled: true,
      created_at: 1700000100,
      sso_provider: null,
      sso_external_id: null,
    },
  ],
};

const SHARE_PAGES_RESPONSE = {
  share_pages: [
    {
      id: 'sp_default',
      name: '默认分享页',
      ragflow_type: 'chat',
      ragflow_resource_id: 'dialog-123',
      embed_type: 'fullscreen',
      enabled: true,
      created_at: 1700000000,
    },
  ],
};

const SESSIONS_RESPONSE = {
  sessions: [
    {
      session_id: 'sess_a',
      title: '如何使用 RAGFlow?',
      portal_user_id: 'u_user1',
      share_page_id: 'sp_default',
      ragflow_resource_id: 'dialog-123',
      created_at: 1700000000,
      last_active_at: 1700001000,
      deleted_at: null,
      message_count: 3,
    },
    {
      session_id: 'sess_b',
      title: '退款流程咨询',
      portal_user_id: 'u_admin',
      share_page_id: 'sp_default',
      ragflow_resource_id: 'dialog-123',
      created_at: 1700002000,
      last_active_at: 1700003000,
      deleted_at: null,
      message_count: 1,
    },
  ],
};

const ELEVATED_RESPONSE = {
  session_id: 'sess_a',
  title: '如何使用 RAGFlow?',
  portal_user_id: 'u_user1',
  share_page_id: 'sp_default',
  ragflow_resource_id: 'dialog-123',
  created_at: 1700000000,
  last_active_at: 1700001000,
  deleted_at: null,
  message_count: 2,
  messages: [
    { role: 'user', content: '如何使用 RAGFlow?' },
    { role: 'assistant', content: '请参考官方文档。' },
  ],
  reference: {},
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/sessions']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/sessions" element={<SessionsAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('SessionsAdminPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示会话列表(标题/用户/分享页/消息数)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByTestId('session-row-sess_a')).toBeInTheDocument();
    expect(screen.getByTestId('session-row-sess_b')).toBeInTheDocument();
  });

  it('按关键词搜索 — 触发带 keyword 的 GET /admin/sessions', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      // 初始加载(无 keyword)
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
      // 搜索后(带 keyword=RAGFlow)
      { url: '/admin/sessions?keyword=RAGFlow', status: 200, body: { sessions: [SESSIONS_RESPONSE.sessions[0]] } },
    ]);

    renderPage();

    await screen.findByTestId('session-row-sess_a');

    await user.type(screen.getByLabelText('关键词'), 'RAGFlow');
    await user.click(screen.getByRole('button', { name: '搜索' }));

    // 等待搜索完成 — sess_b 消失
    await waitFor(() => {
      expect(screen.queryByTestId('session-row-sess_b')).not.toBeInTheDocument();
    });
    // 搜索结果仍含 sess_a
    expect(screen.getByTestId('session-row-sess_a')).toBeInTheDocument();

    // 校验最后一次请求带上了 keyword
    const calls = getFetchCalls(globalThis.fetch);
    const lastCall = calls[calls.length - 1];
    expect(lastCall.url).toContain('keyword=RAGFlow');
  });

  it('按用户筛选 — 触发带 user_id 的 GET /admin/sessions', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
      {
        url: '/admin/sessions?user_id=u_user1',
        status: 200,
        body: { sessions: [SESSIONS_RESPONSE.sessions[0]] },
      },
    ]);

    renderPage();

    await screen.findByTestId('session-row-sess_a');

    await user.selectOptions(screen.getByLabelText('用户'), 'u_user1');
    await user.click(screen.getByRole('button', { name: '搜索' }));

    // 只剩 sess_a,user1 的会话(管理员自己的"退款"会话消失)
    await waitFor(() => {
      expect(screen.queryByTestId('session-row-sess_b')).not.toBeInTheDocument();
    });
    expect(screen.getByTestId('session-row-sess_a')).toBeInTheDocument();

    const calls = getFetchCalls(globalThis.fetch);
    const lastCall = calls[calls.length - 1];
    expect(lastCall.url).toContain('user_id=u_user1');
  });

  it('点击"查看正文" → 统一确认弹窗取消 → 不调 elevated 端点', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);
    renderPage();

    const row = await screen.findByTestId('session-row-sess_a');
    await user.click(
      // 表头按钮也可能匹配,定位到行内按钮
      row.querySelector('button') as HTMLButtonElement,
    );

    const dialog = screen.getByRole('dialog', { name: '查看会话正文' });
    expect(within(dialog).getByText('此操作将记录到审计日志')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '取消' }));
    // 取消 → 不应有 elevated 请求
    const calls = getFetchCalls(globalThis.fetch);
    const elevatedCalls = calls.filter((c) => c.url.includes('elevated=true'));
    expect(elevatedCalls).toHaveLength(0);
    // 弹窗未出现
    expect(screen.queryByText('以管理员身份查看')).not.toBeInTheDocument();
  });

  it('点击"查看正文" → 统一确认弹窗确认 → 调 elevated 端点 + 弹窗显示消息', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
      { url: '/admin/sessions/sess_a?elevated=true', status: 200, body: ELEVATED_RESPONSE },
    ]);
    renderPage();

    const row = await screen.findByTestId('session-row-sess_a');
    await user.click(
      row.querySelector('button') as HTMLButtonElement,
    );

    const dialog = screen.getByRole('dialog', { name: '查看会话正文' });
    await user.click(within(dialog).getByRole('button', { name: '确认查看' }));
    // 弹窗显示消息正文 — assistant 消息"请参考官方文档。"只出现在弹窗,可唯一定位
    expect(await screen.findByText('请参考官方文档。')).toBeInTheDocument();
    // 弹窗标题"以管理员身份查看"出现
    expect(screen.getByText('以管理员身份查看')).toBeInTheDocument();

    // 校验 elevated 请求被调用
    const calls = getFetchCalls(globalThis.fetch);
    const elevatedCalls = calls.filter((c) => c.url.includes('elevated=true'));
    expect(elevatedCalls).toHaveLength(1);
  });

  it('加载会话列表失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 500, body: { detail: '服务器错误' } },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });

  it('elevated 查询失败显示错误提示', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
      { url: '/admin/sessions/sess_a?elevated=true', status: 500, body: { detail: 'RAGFlow 不可用' } },
    ]);
    renderPage();

    const row = await screen.findByTestId('session-row-sess_a');
    await user.click(
      row.querySelector('button') as HTMLButtonElement,
    );

    const dialog = screen.getByRole('dialog', { name: '查看会话正文' });
    await user.click(within(dialog).getByRole('button', { name: '确认查看' }));

    expect(await screen.findByText('RAGFlow 不可用')).toBeInTheDocument();
  });
});
