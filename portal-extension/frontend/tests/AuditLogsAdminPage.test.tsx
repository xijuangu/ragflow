/**
 * 审计日志页测试(Slice 11,验收点 7)— 验证按 action/actor 筛选 + 显示日志条目。
 *
 * 对应 Issue 11 验收点 7:
 *   - 管理员能查看审计日志
 *   - 按 action/时间筛选
 *   - 8 类敏感操作都在日志中可见
 */
import { render, screen, waitFor, within } from '@testing-library/react';
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
      at: 1700002000,
      meta: { subject_type: 'user', subject_id: 'u_user1' },
    },
    {
      id: 3,
      actor_user_id: 'u_admin',
      action: 'session_view_elevated',
      target_type: 'session',
      target_id: 'sess_a',
      at: 1700003000,
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
  });

  it('按 action 筛选 — 触发带 action=xxx 的 GET /admin/audit-logs', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
      {
        url: '/admin/audit-logs?action=grant_create',
        status: 200,
        body: { audit_logs: [AUDIT_LOGS_RESPONSE.audit_logs[1]] },
      },
    ]);

    renderPage();

    // 等待初始加载完成
    await screen.findByTestId('audit-row-1');

    await user.selectOptions(screen.getByLabelText('操作类型'), 'grant_create');
    await user.click(screen.getByRole('button', { name: '筛选' }));

    // 等待筛选结果 — row 1 与 row 3 消失,row 2 保留
    await waitFor(() => {
      expect(screen.queryByTestId('audit-row-1')).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId('audit-row-3')).not.toBeInTheDocument();
    expect(screen.getByTestId('audit-row-2')).toBeInTheDocument();

    const calls = getFetchCalls(globalThis.fetch);
    const lastCall = calls[calls.length - 1];
    expect(lastCall.url).toContain('action=grant_create');
  });

  it('按 actor 筛选 — 触发带 actor_user_id=xxx 的 GET /admin/audit-logs', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/audit-logs', status: 200, body: AUDIT_LOGS_RESPONSE },
      {
        url: '/admin/audit-logs?actor_user_id=u_admin',
        status: 200,
        body: { audit_logs: [AUDIT_LOGS_RESPONSE.audit_logs[0]] },
      },
    ]);

    renderPage();

    await screen.findByTestId('audit-row-1');

    await user.selectOptions(screen.getByLabelText('操作者'), 'u_admin');
    await user.click(screen.getByRole('button', { name: '筛选' }));

    // 等待筛选结果 — 只剩 row 1
    await waitFor(() => {
      expect(screen.queryByTestId('audit-row-2')).not.toBeInTheDocument();
    });
    expect(screen.getByTestId('audit-row-1')).toBeInTheDocument();

    const calls = getFetchCalls(globalThis.fetch);
    const lastCall = calls[calls.length - 1];
    expect(lastCall.url).toContain('actor_user_id=u_admin');
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
});
