/**
 * 用户组管理页测试(Slice 11;Issue 89 改为 master-detail 主从视图)。
 *
 * Issue 89 验收点:
 *   - 左侧可搜索组列表,右侧详情面板:成员列表(带移除)+ 该组的分享页授权 + 顶部添加成员入口
 *   - 添加成员走 combobox 多选 + chips,一次可添加多人;移除成员有确认
 *   - 创建/移除能力不回归
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import GroupsAdminPage from '../src/pages/admin/GroupsAdminPage';
import { getFetchCalls, mockFetch } from './setup';

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

/** 开发组在默认分享页上有授权;运维组没有。 */
const GRANTS_SP_DEFAULT = {
  grants: [
    { share_page_id: 'sp_default', subject_type: 'group', subject_id: 'g_dev', permission: 'use' },
    { share_page_id: 'sp_default', subject_type: 'user', subject_id: 'u_admin', permission: 'use' },
  ],
};

function baseMocks() {
  return [
    { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
    { url: '/admin/groups', status: 200, body: GROUPS_RESPONSE },
    { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
    { url: '/admin/share-pages/sp_default/grants', status: 200, body: GRANTS_SP_DEFAULT },
  ];
}

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

async function renderLoadedPage() {
  renderPage();
  // 等左侧列表与右侧详情(含授权)都加载完
  await screen.findByTestId('group-row-g_dev');
  await screen.findByTestId('group-detail');
}

describe('GroupsAdminPage (Issue 89 master-detail)', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('master-detail:左侧组列表 + 默认选中首组,右侧显示成员与分享页授权', async () => {
    globalThis.fetch = mockFetch(baseMocks());

    await renderLoadedPage();

    // 左侧列表两组可见
    expect(screen.getByTestId('group-row-g_dev')).toBeInTheDocument();
    expect(screen.getByTestId('group-row-g_ops')).toBeInTheDocument();

    // 右侧默认显示首组(开发组)详情:成员 + 授权,无需跳页或开下拉
    const detail = screen.getByTestId('group-detail');
    expect(within(detail).getByRole('heading', { name: '开发组' })).toBeInTheDocument();
    expect(within(detail).getByText('admin')).toBeInTheDocument();
    // u_user1 不在用户列表中,回退显示 ID
    expect(within(detail).getByText('u_user1')).toBeInTheDocument();
    // 该组的分享页授权直接可见(只含 group 授权,不含 user 授权)
    expect(within(detail).getByText('默认分享页')).toBeInTheDocument();
    // 授权区不应混入其他主体的授权名称歧义 — 权限标识可见
    expect(within(detail).getByText('use')).toBeInTheDocument();
  });

  it('点击左侧另一组,右侧详情切换(成员/授权随之变化)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch(baseMocks());

    await renderLoadedPage();

    await user.click(screen.getByTestId('group-row-g_ops'));

    const detail = screen.getByTestId('group-detail');
    expect(within(detail).getByRole('heading', { name: '运维组' })).toBeInTheDocument();
    expect(within(detail).getByText('暂无成员')).toBeInTheDocument();
    // 运维组无授权
    expect(await within(detail).findByText('暂无授权')).toBeInTheDocument();
    // 左侧选中态
    expect(screen.getByTestId('group-row-g_ops')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('group-row-g_dev')).not.toHaveAttribute('aria-current');
  });

  it('创建用户组 — 填名称提交后调 POST /admin/groups,新组出现在左侧列表并选中', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      ...baseMocks(),
      {
        url: '/admin/groups',
        method: 'POST',
        status: 201,
        body: { id: 'g_new', name: '新组', created_at: 1700000600, member_count: 0, members: [] },
      },
    ]);

    await renderLoadedPage();

    await user.type(screen.getByLabelText('用户组名称'), '新组');
    await user.click(screen.getByRole('button', { name: '创建用户组' }));

    const newRow = await screen.findByTestId('group-row-g_new');
    expect(newRow).toHaveTextContent('新组');
    // 新组自动选中,右侧显示其详情
    const detail = screen.getByTestId('group-detail');
    expect(within(detail).getByRole('heading', { name: '新组' })).toBeInTheDocument();
  });

  it('Slice 43 — 后端 POST 响应漏 member_count/members 时兜底不崩溃', async () => {
    // 回归测试:原 bug admin_create_group 只返回 id/name/created_at/org_id,
    // 导致 g.members.length 抛 TypeError → React render 崩溃 → 整个 admin SPA 加载失败
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      ...baseMocks(),
      {
        url: '/admin/groups',
        method: 'POST',
        status: 201,
        body: { id: 'g_buggy', name: '漏字段组', created_at: 1700000700 },
      },
    ]);

    await renderLoadedPage();

    await user.type(screen.getByLabelText('用户组名称'), '漏字段组');
    await user.click(screen.getByRole('button', { name: '创建用户组' }));

    const newRow = await screen.findByTestId('group-row-g_buggy');
    expect(newRow).toHaveTextContent('漏字段组');
    expect(newRow).toHaveTextContent(/0\s*成员/);
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  });

  it('添加成员 — combobox 多选 + chips,一次可添加多人(Issue 88/89)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      ...baseMocks(),
      {
        url: '/admin/groups/g_ops/members',
        method: 'POST',
        status: 200,
        body: { group_id: 'g_ops', user_id: 'u_user2', added: true },
      },
    ]);

    await renderLoadedPage();
    await user.click(screen.getByTestId('group-row-g_ops'));

    const detail = screen.getByTestId('group-detail');
    const picker = within(detail).getByRole('combobox', { name: '选择用户' });
    await user.click(picker);
    await user.type(picker, 'user2@');
    await user.click(screen.getByRole('option', { name: /user2/ }));
    expect(within(detail).getByRole('list', { name: '已选项' })).toHaveTextContent('user2');

    await user.click(within(detail).getByRole('button', { name: '添加' }));

    // 乐观更新:成员数从 0 变 1,且显示 user2(用户名)
    expect(await within(detail).findByText('user2')).toBeInTheDocument();
    expect(await within(detail).findByText(/1\s*成员/)).toBeInTheDocument();
    // 提交后 chips 清空
    expect(within(detail).queryByRole('list', { name: '已选项' })).not.toBeInTheDocument();
  });

  it('添加成员 — chips 移除后不再提交该用户', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch(baseMocks());

    await renderLoadedPage();
    await user.click(screen.getByTestId('group-row-g_ops'));

    const detail = screen.getByTestId('group-detail');
    const picker = within(detail).getByRole('combobox', { name: '选择用户' });
    await user.click(picker);
    await user.click(screen.getByRole('option', { name: /user2/ }));
    await user.click(within(detail).getByRole('button', { name: '移除 user2' }));
    expect(within(detail).getByRole('button', { name: '添加' })).toBeDisabled();

    const calls = getFetchCalls(globalThis.fetch);
    expect(calls.filter((c) => c.url.includes('/members'))).toHaveLength(0);
  });

  it('移除成员 — 需确认弹窗,确认后调 DELETE 并乐观移除', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      ...baseMocks(),
      {
        url: '/admin/groups/g_dev/members/u_user1',
        method: 'DELETE',
        status: 200,
        body: { group_id: 'g_dev', user_id: 'u_user1', removed: true },
      },
    ]);

    await renderLoadedPage();

    const detail = screen.getByTestId('group-detail');
    const memberItem = within(detail).getByText('u_user1').closest('.member-item') as HTMLElement;
    await user.click(within(memberItem).getByRole('button', { name: '移除' }));

    // 确认弹窗出现
    const dialog = screen.getByRole('dialog', { name: '移除成员' });
    await user.click(within(dialog).getByRole('button', { name: '确认移除' }));

    // 乐观更新:u_user1 从成员列表移除
    expect(within(detail).queryByText('u_user1')).not.toBeInTheDocument();
    const calls = getFetchCalls(globalThis.fetch);
    expect(
      calls.filter((c) => c.url === '/admin/groups/g_dev/members/u_user1' && c.method === 'DELETE'),
    ).toHaveLength(1);
  });

  it('移除成员 — 取消确认弹窗则不调 DELETE、成员保留', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch(baseMocks());

    await renderLoadedPage();

    const detail = screen.getByTestId('group-detail');
    const memberItem = within(detail).getByText('u_user1').closest('.member-item') as HTMLElement;
    await user.click(within(memberItem).getByRole('button', { name: '移除' }));

    const dialog = screen.getByRole('dialog', { name: '移除成员' });
    await user.click(within(dialog).getByRole('button', { name: '取消' }));

    expect(within(detail).getByText('u_user1')).toBeInTheDocument();
    const calls = getFetchCalls(globalThis.fetch);
    expect(calls.filter((c) => c.url.includes('/members') && c.method === 'DELETE')).toHaveLength(0);
  });

  it('搜索框按组名不区分大小写模糊匹配左侧列表(Issue 87/89)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch(baseMocks());

    await renderLoadedPage();

    await user.type(screen.getByLabelText('搜索用户组'), '运维');
    expect(screen.queryByTestId('group-row-g_dev')).not.toBeInTheDocument();
    expect(screen.getByTestId('group-row-g_ops')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('搜索用户组'));
    await user.type(screen.getByLabelText('搜索用户组'), '不存在的组');
    expect(screen.queryByTestId(/^group-row-/)).not.toBeInTheDocument();
    expect(screen.getByText('无匹配结果')).toBeInTheDocument();
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/groups', status: 500, body: { detail: '服务器错误' } },
      { url: '/admin/users', status: 200, body: USERS_RESPONSE },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });
});
