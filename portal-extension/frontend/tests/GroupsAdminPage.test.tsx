/**
 * 用户组管理页测试(Slice 11)— 验证列表/创建/添加成员/移除成员。
 *
 * 对应 Issue 11 验收点 3:管理员能创建用户组、添加/移除成员。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import GroupsAdminPage from '../src/pages/admin/GroupsAdminPage';
import { mockFetch } from './setup';

const GROUPS_RESPONSE = {
  groups: [
    {
      id: 'g_dev',
      name: '开发组',
      created_at: 1700000000,
      member_count: 2,
      members: ['u_admin', 'u_user1'],
    },
    {
      id: 'g_ops',
      name: '运维组',
      created_at: 1700000100,
      member_count: 0,
      members: [],
    },
  ],
};

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
      id: 'u_user2',
      username: 'user2',
      email: 'user2@example.com',
      is_admin: false,
      enabled: true,
      created_at: 1700000500,
      sso_provider: null,
      sso_external_id: null,
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/groups']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/groups" element={<GroupsAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('GroupsAdminPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示用户组列表(名称/成员数)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByText('开发组')).toBeInTheDocument();
    expect(screen.getByText('运维组')).toBeInTheDocument();
    // 成员数可见
    expect(screen.getByText(/2\s*成员/)).toBeInTheDocument();
    expect(screen.getByText(/0\s*成员/)).toBeInTheDocument();
  });

  it('创建用户组 — 填名称提交后调 POST /admin/groups', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/groups',
        method: 'POST',
        status: 201,
        body: { id: 'g_new', name: '新组', created_at: 1700000600, member_count: 0, members: [] },
      },
    ]);

    renderPage();

    await screen.findByText('开发组');

    await user.type(screen.getByLabelText('用户组名称'), '新组');
    await user.click(screen.getByRole('button', { name: '创建用户组' }));

    expect(await screen.findByText('新组')).toBeInTheDocument();
  });

  it('添加成员 — 选择用户并提交后调 POST /admin/groups/:id/members', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/groups/g_ops/members',
        method: 'POST',
        status: 200,
        body: { group_id: 'g_ops', user_id: 'u_user2', added: true },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('group-row-g_ops');
    // 在该组行内选择 user2 并添加
    await user.selectOptions(within(row).getByLabelText('选择用户'), 'u_user2');
    await user.click(within(row).getByRole('button', { name: '添加' }));

    // 乐观更新:成员数从 0 变 1,且显示 user2(用户名)
    expect(await within(row).findByText('user2')).toBeInTheDocument();
    expect(await within(row).findByText(/1\s*成员/)).toBeInTheDocument();
  });

  it('移除成员 — 点击"移除"调 DELETE /admin/groups/:id/members/:user_id', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      {
        url: '/admin/groups/g_dev/members/u_user1',
        method: 'DELETE',
        status: 200,
        body: { group_id: 'g_dev', user_id: 'u_user1', removed: true },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('group-row-g_dev');
    // 初始成员列表含 u_user1(u_user1 不在 USERS_RESPONSE 中,resolveName 回退显示 ID)
    const memberItem = within(row).getByText('u_user1').closest('.member-item') as HTMLElement;
    expect(memberItem).toBeInTheDocument();
    // 点击该成员旁的"移除"
    await user.click(within(memberItem).getByRole('button', { name: '移除' }));

    // 乐观更新:u_user1 从成员列表移除
    expect(within(row).queryByText('u_user1')).not.toBeInTheDocument();
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/groups', status: 500, body: { detail: '服务器错误' } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });
});
