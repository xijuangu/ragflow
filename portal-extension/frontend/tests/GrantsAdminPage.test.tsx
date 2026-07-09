/**
 * 授权管理页测试(Slice 11)— 验证选择分享页/列出授权/授权给用户或组/撤销授权。
 *
 * 对应 Issue 11 验收点 5:管理员能把分享页授权给用户或组,能撤销授权。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import GrantsAdminPage from '../src/pages/admin/GrantsAdminPage';
import { mockFetch } from './setup';

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
    {
      id: 'sp_other',
      name: '其他分享页',
      ragflow_type: 'chat',
      ragflow_resource_id: 'dialog-456',
      embed_type: 'fullscreen',
      enabled: true,
      created_at: 1700000100,
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

const GROUPS_RESPONSE = {
  groups: [
    {
      id: 'g_dev',
      name: '开发组',
      created_at: 1700000000,
      member_count: 1,
      members: ['u_admin'],
    },
  ],
};

const GRANTS_RESPONSE = {
  grants: [
    {
      share_page_id: 'sp_default',
      subject_type: 'user',
      subject_id: 'u_admin',
      permission: 'use',
    },
    {
      share_page_id: 'sp_default',
      subject_type: 'group',
      subject_id: 'g_dev',
      permission: 'use',
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/grants']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/grants" element={<GrantsAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('GrantsAdminPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并默认选中第一个分享页,显示其授权列表', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/share-pages/sp_default/grants', status: 200, body: GRANTS_RESPONSE },
    ]);

    renderPage();

    // 授权列表显示(用户名 admin 与组名 开发组 可见,而非裸 ID)
    expect(await screen.findByText('admin')).toBeInTheDocument();
    expect(screen.getByText('开发组')).toBeInTheDocument();
  });

  it('切换分享页 — 调对应 grants 端点', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/share-pages/sp_default/grants', status: 200, body: GRANTS_RESPONSE },
      { url: '/admin/share-pages/sp_other/grants', status: 200, body: { grants: [] } },
    ]);

    renderPage();

    await screen.findByText('admin');

    // 切换到"其他分享页"
    await user.selectOptions(screen.getByLabelText('分享页'), 'sp_other');

    // 空状态出现
    expect(await screen.findByText('暂无授权')).toBeInTheDocument();
  });

  it('授权给用户 — 选择 subject_type=user + user1,提交后调 POST grants', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/share-pages/sp_default/grants', status: 200, body: { grants: [] } },
      {
        url: '/admin/share-pages/sp_default/grants',
        method: 'POST',
        status: 201,
        body: {
          share_page_id: 'sp_default',
          subject_type: 'user',
          subject_id: 'u_user1',
          permission: 'use',
        },
      },
    ]);

    renderPage();

    await screen.findByText('暂无授权');

    // 选择授权主体类型 = 用户
    await user.selectOptions(screen.getByLabelText('主体类型'), 'user');
    // 选择 user1
    await user.selectOptions(screen.getByLabelText('授权对象'), 'u_user1');
    await user.click(screen.getByRole('button', { name: '授权' }));

    // 乐观更新:授权行出现,行内含用户名 user1
    const row = await screen.findByTestId('grant-row-user-u_user1');
    expect(within(row).getByText('user1')).toBeInTheDocument();
  });

  it('授权给组 — 选择 subject_type=group + 开发组,提交后调 POST grants', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/share-pages/sp_default/grants', status: 200, body: { grants: [] } },
      {
        url: '/admin/share-pages/sp_default/grants',
        method: 'POST',
        status: 201,
        body: {
          share_page_id: 'sp_default',
          subject_type: 'group',
          subject_id: 'g_dev',
          permission: 'use',
        },
      },
    ]);

    renderPage();

    await screen.findByText('暂无授权');

    await user.selectOptions(screen.getByLabelText('主体类型'), 'group');
    await user.selectOptions(screen.getByLabelText('授权对象'), 'g_dev');
    await user.click(screen.getByRole('button', { name: '授权' }));

    // 乐观更新:授权行出现,行内含组名 开发组
    const row = await screen.findByTestId('grant-row-group-g_dev');
    expect(within(row).getByText('开发组')).toBeInTheDocument();
  });

  it('撤销授权 — 点击"撤销"调 DELETE /share-pages/:id/grants/:type/:id', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
      { url: '/admin/share-pages/sp_default/grants', status: 200, body: GRANTS_RESPONSE },
      {
        url: '/share-pages/sp_default/grants/user/u_admin',
        method: 'DELETE',
        status: 200,
        body: {
          revoked: true,
          share_page_id: 'sp_default',
          subject_type: 'user',
          subject_id: 'u_admin',
          tokens_revoked: 1,
        },
      },
    ]);

    renderPage();

    // 等待授权列表加载
    const adminRow = await screen.findByTestId('grant-row-user-u_admin');
    await user.click(within(adminRow).getByRole('button', { name: '撤销' }));

    // 乐观更新:该授权行消失
    expect(screen.queryByTestId('grant-row-user-u_admin')).not.toBeInTheDocument();
  });

  it('加载分享页失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 500, body: { detail: '服务器错误' } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });
});
