/**
 * 审计日志页测试(Slice 11,验收点 7)— 验证按 action/actor 筛选 + 显示日志条目。
 *
 * 对应 Issue 11 验收点 7:
 *   - 管理员能查看审计日志
 *   - 按 action/时间筛选
 *   - 8 类敏感操作都在日志中可见
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import AuditLogsAdminPage from '../src/pages/admin/AuditLogsAdminPage';
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

const AUDIT_LOGS_RESPONSE = {
  audit_logs: [
    {
      id: 1,
      actor_user_id: 'u_admin',
      action: 'login_success',
      target_type: 'user',
      target_id: 'u_admin',
      at: 1700001000,
      meta: null,
    },
    {
      id: 2,
      actor_user_id: 'u_admin',
      action: 'grant_create',
      target_type: 'share_page',
      target_id: 'sp_default',
      at: 1700100000,
      meta: { subject_type: 'user', subject_id: 'u_user1' },
    },
    {
      id: 3,
      actor_user_id: 'u_user1',
      action: 'session_view_elevated',
      target_type: 'session',
      target_id: 'sess_a',
      at: 1700200000,
      meta: { owner_user_id: 'u_user1', share_page_id: 'sp_default' },
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/audit']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/audit" element={<AuditLogsAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('AuditLogsAdminPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示审计日志列表(actor/action/目标/时间)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
    ]);

    renderPage();

    // 三行日志都出现(按 testid 定位,避免与下拉选项文本冲突)
    const row1 = await screen.findByTestId('audit-row-1');
    const row2 = await screen.findByTestId('audit-row-2');
    const row3 = await screen.findByTestId('audit-row-3');
    // 行内 action 文本(在 badge 内)
    expect(within(row1).getByText('login_success')).toBeInTheDocument();
    expect(within(row2).getByText('grant_create')).toBeInTheDocument();
    expect(within(row3).getByText('session_view_elevated')).toBeInTheDocument();
    // actor 用户名 admin(而非裸 ID)出现在每行
    expect(within(row1).getByText('admin')).toBeInTheDocument();
    const detailsText = '{"subject_type":"user","subject_id":"u_user1"}';
    const details = within(row2).getByText(detailsText);
    expect(details).toHaveAttribute('title', detailsText);
  });

  it('按 action 筛选 — 客户端过滤,不再带参请求(Issue 87)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('audit-row-1');

    await user.selectOptions(screen.getByLabelText('操作类型'), 'grant_create');

    // row 1 与 row 3 消失,row 2 保留
    expect(screen.queryByTestId('audit-row-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-row-3')).not.toBeInTheDocument();
    expect(screen.getByTestId('audit-row-2')).toBeInTheDocument();

    // 全量加载后不再发起带查询参数的 audit-logs 请求
    const calls = getFetchCalls(globalThis.fetch);
    expect(calls.filter((c) => c.url.startsWith('/admin/audit-logs?'))).toHaveLength(0);
  });

  it('搜索框按操作者用户名不区分大小写模糊匹配(Issue 87)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('audit-row-1');

    await user.type(screen.getByLabelText('搜索操作者'), 'USER1');
    expect(screen.queryByTestId('audit-row-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-row-2')).not.toBeInTheDocument();
    expect(screen.getByTestId('audit-row-3')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('搜索操作者'));
    await user.type(screen.getByLabelText('搜索操作者'), 'admin');
    expect(screen.getByTestId('audit-row-1')).toBeInTheDocument();
    expect(screen.getByTestId('audit-row-2')).toBeInTheDocument();
    expect(screen.queryByTestId('audit-row-3')).not.toBeInTheDocument();
  });

  it('日志条目包含 8 类 action 筛选选项', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: { audit_logs: [] } },
    ]);

    renderPage();

    await screen.findByText('暂无日志');

    const select = screen.getByLabelText('操作类型') as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    // 8 类敏感操作都在下拉中
    expect(options).toContain('login_success');
    expect(options).toContain('login_failure');
    expect(options).toContain('grant_create');
    expect(options).toContain('grant_revoke');
    expect(options).toContain('session_delete');
    expect(options).toContain('session_view_elevated');
    expect(options).toContain('user_enable');
    expect(options).toContain('user_disable');
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 500, body: { detail: '服务器错误' } },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });

  it('空日志显示空状态', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: { audit_logs: [] } },
    ]);

    renderPage();

    expect(await screen.findByText('暂无日志')).toBeInTheDocument();
  });

  it('按时间范围筛选 — 客户端按起始/结束日期过滤(Issue 87)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('audit-row-1');

    // at 分别为 2023-11-14 / 2023-11-16 / 2023-11-17(UTC);圈出 11-16 只留 row 2
    await user.type(screen.getByLabelText('起始日期'), '2023-11-16');
    await user.type(screen.getByLabelText('结束日期'), '2023-11-16');

    expect(screen.queryByTestId('audit-row-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-row-3')).not.toBeInTheDocument();
    expect(screen.getByTestId('audit-row-2')).toBeInTheDocument();
  });

  it('时间列三态排序(Issue 87)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('audit-row-1');

    const order = () =>
      screen
        .getAllByTestId(/^audit-row-/)
        .map((el) => el.getAttribute('data-testid')!.replace('audit-row-', ''));

    expect(order()).toEqual(['1', '2', '3']);
    await user.click(screen.getByRole('button', { name: '按时间排序' }));
    expect(order()).toEqual(['1', '2', '3']);
    await user.click(screen.getByRole('button', { name: '按时间排序' }));
    expect(order()).toEqual(['3', '2', '1']);
    await user.click(screen.getByRole('button', { name: '按时间排序' }));
    expect(order()).toEqual(['1', '2', '3']);
  });
});
