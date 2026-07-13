/**
 * 分享页管理页测试(Slice 11)— 验证列表/创建(填 dialog_id)/启用禁用。
 *
 * 对应 Issue 11 验收点 4:管理员能创建分享页(填 dialog_id)、启用/禁用。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePagesAdminPage from '../src/pages/admin/SharePagesAdminPage';
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
      id: 'sp_disabled',
      name: '已禁用分享页',
      ragflow_type: 'chat',
      ragflow_resource_id: 'dialog-456',
      embed_type: 'fullscreen',
      enabled: false,
      created_at: 1700000100,
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/share-pages']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/share-pages" element={<SharePagesAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('SharePagesAdminPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示分享页列表(名称/dialog_id/状态)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
    ]);

    renderPage();

    const defaultRow = await screen.findByTestId('share-page-row-sp_default');
    expect(within(defaultRow).getByText('默认分享页')).toBeInTheDocument();
    expect(within(defaultRow).getByText('dialog-123')).toBeInTheDocument();
    expect(screen.getByTestId('share-page-row-sp_disabled')).toBeInTheDocument();
  });

  it('创建分享页 — 填名称与 dialog_id 提交后调 POST /admin/share-pages', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      {
        url: '/admin/share-pages',
        method: 'POST',
        status: 201,
        body: {
          id: 'sp_new',
          name: '新分享页',
          ragflow_type: 'chat',
          ragflow_resource_id: 'dialog-new',
          embed_type: 'fullscreen',
          enabled: true,
          created_at: 1700000200,
        },
      },
    ]);

    renderPage();

    await screen.findByTestId('share-page-row-sp_default');

    await user.type(screen.getByLabelText('分享页名称'), '新分享页');
    await user.type(screen.getByLabelText('RAGFlow 资源 ID'), 'dialog-new');
    await user.click(screen.getByRole('button', { name: '创建分享页' }));

    const createdRow = await screen.findByTestId('share-page-row-sp_new');
    expect(within(createdRow).getByText('新分享页')).toBeInTheDocument();
    expect(within(createdRow).getByText('dialog-new')).toBeInTheDocument();
  });

  it('禁用分享页 — 点击"禁用"调 PATCH enabled=false', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      {
        url: '/admin/share-pages/sp_default',
        method: 'PATCH',
        status: 200,
        body: {
          id: 'sp_default',
          name: '默认分享页',
          ragflow_type: 'chat',
          ragflow_resource_id: 'dialog-123',
          embed_type: 'fullscreen',
          enabled: false,
          created_at: 1700000000,
        },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('share-page-row-sp_default');
    await user.click(within(row).getByRole('button', { name: '禁用' }));

    // 乐观更新:按钮切换为"启用"
    expect(await within(row).findByRole('button', { name: '启用' })).toBeInTheDocument();
  });

  it('启用分享页 — 点击"启用"调 PATCH enabled=true', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
      {
        url: '/admin/share-pages/sp_disabled',
        method: 'PATCH',
        status: 200,
        body: {
          id: 'sp_disabled',
          name: '已禁用分享页',
          ragflow_type: 'chat',
          ragflow_resource_id: 'dialog-456',
          embed_type: 'fullscreen',
          enabled: true,
          created_at: 1700000100,
        },
      },
    ]);

    renderPage();

    const row = await screen.findByTestId('share-page-row-sp_disabled');
    await user.click(within(row).getByRole('button', { name: '启用' }));

    expect(await within(row).findByRole('button', { name: '禁用' })).toBeInTheDocument();
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 500, body: { detail: '服务器错误' } },
    ]);

    renderPage();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });
});
