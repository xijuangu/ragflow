/**
 * 用户管理页测试(Slice 11)— 验证列表/创建/启用禁用/硬删除(确认)。
 *
 * 对应 Issue 11 验收点 2:管理员能创建/禁用/启用/硬删除用户,硬删除时有确认提示。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import UsersAdminPage from '../src/pages/admin/UsersAdminPage';
import { mockFetch } from './setup';

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
    {
      id: 'u_disabled',
      username: 'disabled_user',
      email: 'd@example.com',
      is_admin: false,
      enabled: false,
      created_at: 1700000200,
      sso_provider: null,
      sso_external_id: null,
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/users']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/users" element={<UsersAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('UsersAdminPage', () => {
  let originalFetch: typeof fetch;
  let originalConfirm: typeof window.confirm;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    originalConfirm = window.confirm;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    window.confirm = originalConfirm;
    vi.restoreAllMocks();
  });

  it('加载并显示用户列表(用户名/邮箱/管理员标记/启用状态)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByText('admin')).toBeInTheDocument();
    expect(screen.getByText('user1@example.com')).toBeInTheDocument();
    expect(screen.getByText('disabled_user')).toBeInTheDocument();
    // 管理员标记可见
    expect(screen.getByText(/管理员/)).toBeInTheDocument();
  });

  it('创建用户 — 填表单提交后调 POST /admin/users 并刷新列表', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/users',
        method: 'POST',
        status: 201,
        body: {
          id: 'u_new',
          username: 'newuser',
          email: 'new@example.com',
          is_admin: false,
          enabled: true,
          created_at: 1700000300,
          sso_provider: null,
          sso_external_id: null,
        },
      },
      // 列表刷新(创建后重新拉取)
      {
        url: '/admin/users',
        status: 200,
        body: {
          users: [
            ...USERS_RESPONSE.users,
            {
              id: 'u_new',
              username: 'newuser',
              email: 'new@example.com',
              is_admin: false,
              enabled: true,
              created_at: 1700000300,
              sso_provider: null,
              sso_external_id: null,
            },
          ],
        },
      },
    ]);

    renderPage();

    await screen.findByText('admin');

    await user.type(screen.getByLabelText('用户名'), 'newuser');
    await user.type(screen.getByLabelText('邮箱'), 'new@example.com');
    await user.type(screen.getByLabelText('初始密码'), 'pass123');
    await user.click(screen.getByRole('button', { name: '创建用户' }));

    expect(await screen.findByText('newuser')).toBeInTheDocument();
  });

  it('禁用用户 — 点击"禁用"调 PATCH enabled=false(写 user_disable 审计)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/users/u_user1',
        method: 'PATCH',
        status: 200,
        body: {
          id: 'u_user1',
          username: 'user1',
          email: 'user1@example.com',
          is_admin: false,
          enabled: false,
          created_at: 1700000100,
          sso_provider: null,
          sso_external_id: null,
        },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('user-row-u_user1');
    const disableBtn = within(row).getByRole('button', { name: '禁用' });
    await user.click(disableBtn);

    // 按钮切换为"启用"(乐观更新:enabled=false)
    expect(await within(row).findByRole('button', { name: '启用' })).toBeInTheDocument();
  });

  it('启用用户 — 点击"启用"调 PATCH enabled=true(写 user_enable 审计)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/users/u_disabled',
        method: 'PATCH',
        status: 200,
        body: {
          id: 'u_disabled',
          username: 'disabled_user',
          email: 'd@example.com',
          is_admin: false,
          enabled: true,
          created_at: 1700000200,
          sso_provider: null,
          sso_external_id: null,
        },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('user-row-u_disabled');
    const enableBtn = within(row).getByRole('button', { name: '启用' });
    await user.click(enableBtn);

    expect(await within(row).findByRole('button', { name: '禁用' })).toBeInTheDocument();
  });

  it('硬删除用户 — confirm 确认后调 DELETE(验收点 2:硬删除时有确认提示)', async () => {
    const user = userEvent.setup();
    window.confirm = vi.fn().mockReturnValue(true);
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/users/u_user1',
        method: 'DELETE',
        status: 200,
        body: { user_id: 'u_user1', deleted: true },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('user-row-u_user1');
    await user.click(within(row).getByRole('button', { name: '硬删除' }));

    expect(window.confirm).toHaveBeenCalled();
    // 删除后行消失(乐观移除)
    expect(screen.queryByTestId('user-row-u_user1')).not.toBeInTheDocument();
  });

  it('硬删除用户 — confirm 取消不调 DELETE', async () => {
    const user = userEvent.setup();
    window.confirm = vi.fn().mockReturnValue(false);
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();

    const row = await screen.findByTestId('user-row-u_user1');
    await user.click(within(row).getByRole('button', { name: '硬删除' }));

    expect(window.confirm).toHaveBeenCalled();
    // 行仍存在
    expect(screen.getByTestId('user-row-u_user1')).toBeInTheDocument();
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 500, body: { detail: '服务器错误' } },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });
});
