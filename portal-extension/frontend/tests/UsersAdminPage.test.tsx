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

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示用户列表(用户名/邮箱/管理员标记/启用状态)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByTestId('user-row-u_admin')).toBeInTheDocument();
    expect(screen.getAllByText('user1@example.com')).toHaveLength(2);
    expect(screen.getAllByText('disabled_user')).toHaveLength(2);
    // 管理员标记可见
    expect(screen.getAllByText('管理员')).toHaveLength(2);
    const mobileCard = screen.getByTestId('mobile-user-u_user1');
    expect(within(mobileCard).getByText('user1@example.com')).toBeInTheDocument();
    expect(within(mobileCard).getByRole('button', { name: '禁用' })).toBeInTheDocument();
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

    await screen.findByTestId('user-row-u_admin');

    await user.type(screen.getByLabelText('用户名'), 'newuser');
    await user.type(screen.getByLabelText('邮箱'), 'new@example.com');
    await user.type(screen.getByLabelText('初始密码'), 'pass123');
    await user.click(screen.getByRole('button', { name: '创建用户' }));

    expect(await screen.findByTestId('mobile-user-u_new')).toBeInTheDocument();
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

  it('硬删除用户 — 统一弹窗确认后调 DELETE', async () => {
    const user = userEvent.setup();
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

    const dialog = screen.getByRole('dialog', { name: '硬删除用户' });
    expect(within(dialog).getByText('会级联删除该用户的所有会话')).toBeInTheDocument();
    expect(within(dialog).getByText('此操作不可恢复')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '确认删除' }));
    // 删除后行消失(乐观移除)
    expect(await screen.findByText('用户管理')).toBeInTheDocument();
    expect(screen.queryByTestId('user-row-u_user1')).not.toBeInTheDocument();
  });

  it('硬删除用户 — 弹窗取消不调 DELETE', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();

    const row = await screen.findByTestId('user-row-u_user1');
    await user.click(within(row).getByRole('button', { name: '硬删除' }));

    const dialog = screen.getByRole('dialog', { name: '硬删除用户' });
    await user.click(within(dialog).getByRole('button', { name: '取消' }));
    // 行仍存在
    expect(screen.getByTestId('user-row-u_user1')).toBeInTheDocument();
    expect(getFetchCalls(globalThis.fetch).filter((call) => call.method === 'DELETE')).toHaveLength(0);
  });

  it('修改密码 — 点击"改密码"后提交新密码并调 PATCH /admin/users/:id/password', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/users/u_user1/password',
        method: 'PATCH',
        status: 200,
        body: {
          id: 'u_user1',
          username: 'user1',
          email: 'user1@example.com',
          is_admin: false,
          enabled: true,
          created_at: 1700000100,
          sso_provider: null,
          sso_external_id: null,
        },
      },
    ]);
    globalThis.fetch = fetchMock;

    renderPage();

    const row = await screen.findByTestId('user-row-u_user1');
    await user.click(within(row).getByRole('button', { name: '改密码' }));
    await user.type(screen.getByLabelText('新密码'), 'newpass123');
    await user.click(screen.getByRole('button', { name: '保存密码' }));

    expect(await screen.findByText('密码已更新')).toBeInTheDocument();
    expect(getFetchCalls(fetchMock)).toContainEqual({
      url: '/admin/users/u_user1/password',
      method: 'PATCH',
      body: { password: 'newpass123' },
    });
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 500, body: { detail: '服务器错误' } },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });

  // ---- Issue 86:搜索 / 筛选 / 三态排序(tracer 接入) ----

  function userRowOrder(): string[] {
    return screen
      .getAllByTestId(/^user-row-/)
      .map((el) => el.getAttribute('data-testid')!.replace('user-row-', ''));
  }

  it('搜索框按用户名+邮箱不区分大小写模糊匹配', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('user-row-u_admin');

    // 大写搜索用户名
    await user.type(screen.getByLabelText('搜索用户'), 'USER1');
    expect(userRowOrder()).toEqual(['u_user1']);

    // 清空后改搜邮箱
    await user.clear(screen.getByLabelText('搜索用户'));
    await user.type(screen.getByLabelText('搜索用户'), 'd@example');
    expect(userRowOrder()).toEqual(['u_disabled']);

    // 无匹配 → 空态提示
    await user.clear(screen.getByLabelText('搜索用户'));
    await user.type(screen.getByLabelText('搜索用户'), 'nobody');
    expect(screen.queryByTestId(/^user-row-/)).not.toBeInTheDocument();
    expect(screen.getByText('无匹配结果')).toBeInTheDocument();
  });

  it('启用状态筛选:只看禁用用户', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('user-row-u_admin');

    await user.selectOptions(screen.getByLabelText('状态筛选'), 'disabled');
    expect(userRowOrder()).toEqual(['u_disabled']);

    await user.selectOptions(screen.getByLabelText('状态筛选'), 'enabled');
    expect(userRowOrder()).toEqual(['u_admin', 'u_user1']);

    await user.selectOptions(screen.getByLabelText('状态筛选'), '');
    expect(userRowOrder()).toEqual(['u_admin', 'u_user1', 'u_disabled']);
  });

  it('用户名列表头三态排序:升 → 降 → 不排', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('user-row-u_admin');
    expect(userRowOrder()).toEqual(['u_admin', 'u_user1', 'u_disabled']);

    await user.click(screen.getByRole('button', { name: '按用户名排序' }));
    expect(userRowOrder()).toEqual(['u_admin', 'u_disabled', 'u_user1']);

    await user.click(screen.getByRole('button', { name: '按用户名排序' }));
    expect(userRowOrder()).toEqual(['u_user1', 'u_disabled', 'u_admin']);

    await user.click(screen.getByRole('button', { name: '按用户名排序' }));
    expect(userRowOrder()).toEqual(['u_admin', 'u_user1', 'u_disabled']);
  });

  it('创建时间列可排序;操作列不参与排序', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();
    await screen.findByTestId('user-row-u_admin');

    await user.click(screen.getByRole('button', { name: '按创建时间排序' }));
    expect(userRowOrder()).toEqual(['u_admin', 'u_user1', 'u_disabled']);
    await user.click(screen.getByRole('button', { name: '按创建时间排序' }));
    expect(userRowOrder()).toEqual(['u_disabled', 'u_user1', 'u_admin']);

    // 操作列没有排序按钮
    expect(screen.queryByRole('button', { name: '按操作排序' })).not.toBeInTheDocument();
  });
});
