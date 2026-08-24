/**
 * 会话搜索页测试(Slice 11,验收点 6)— 验证按多维度搜索 + elevated 二次确认 + 弹窗显示正文。
 *
 * 对应 Issue 11 验收点 6:
 *   - 管理员能按多维度搜索会话(用户/分享页/关键词)
 *   - 默认只看元数据
 *   - 查正文需统一 warning 弹窗二次确认,确认后调 elevated 端点(写审计)
 *   - 弹窗显示消息正文
 */
import { render, screen, within } from '@testing-library/react';
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

  it('搜索框按会话标题 + 用户名模糊匹配,纯客户端不再带参请求(Issue 87)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('session-row-sess_a');

    // 按标题关键词过滤
    await user.type(screen.getByLabelText('搜索会话'), 'RAGFlow');
    expect(screen.queryByTestId('session-row-sess_b')).not.toBeInTheDocument();
    expect(screen.getByTestId('session-row-sess_a')).toBeInTheDocument();

    // 按用户名过滤(sess_a 属于 user1)
    await user.clear(screen.getByLabelText('搜索会话'));
    await user.type(screen.getByLabelText('搜索会话'), 'USER1');
    expect(screen.queryByTestId('session-row-sess_b')).not.toBeInTheDocument();
    expect(screen.getByTestId('session-row-sess_a')).toBeInTheDocument();

    // 全量加载后不再发起带查询参数的 sessions 请求
    const calls = getFetchCalls(globalThis.fetch);
    const sessionCalls = calls.filter((c) => c.url.startsWith('/admin/sessions?'));
    expect(sessionCalls).toHaveLength(0);
  });

  it('用户/分享页筛选 combobox(Issue 87 + Issue 88 长列表筛选换 combobox)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('session-row-sess_a');

    // 用户筛选:combobox 选择 user1
    const userFilter = screen.getByRole('combobox', { name: '用户筛选' });
    await user.click(userFilter);
    await user.click(screen.getByRole('option', { name: /user1/ }));
    expect(screen.queryByTestId('session-row-sess_b')).not.toBeInTheDocument();
    expect(screen.getByTestId('session-row-sess_a')).toBeInTheDocument();

    // 选"全部"清空筛选
    await user.click(userFilter);
    await user.click(screen.getByRole('option', { name: '全部' }));

    // 分享页筛选:combobox 选择默认分享页
    const pageFilter = screen.getByRole('combobox', { name: '分享页筛选' });
    await user.click(pageFilter);
    await user.click(screen.getByRole('option', { name: '默认分享页' }));
    expect(screen.getByTestId('session-row-sess_a')).toBeInTheDocument();
    expect(screen.getByTestId('session-row-sess_b')).toBeInTheDocument();
  });

  it('消息数列三态排序(Issue 87)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('session-row-sess_a');

    const order = () =>
      screen
        .getAllByTestId(/^session-row-/)
        .map((el) => el.getAttribute('data-testid')!.replace('session-row-', ''));

    expect(order()).toEqual(['sess_a', 'sess_b']);
    await user.click(screen.getByRole('button', { name: '按消息数排序' }));
    expect(order()).toEqual(['sess_b', 'sess_a']); // 1 < 3 升序
    await user.click(screen.getByRole('button', { name: '按消息数排序' }));
    expect(order()).toEqual(['sess_a', 'sess_b']);
    await user.click(screen.getByRole('button', { name: '按消息数排序' }));
    expect(order()).toEqual(['sess_a', 'sess_b']); // 不排:恢复原始顺序
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
