/**
 * 分享页列表页测试 — 验证列表加载与点击进入详情。
 *
 * 对应 Slice 9 验收点 2:列表页显示用户被授权的分享页(至少 sp_default)。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePagesPage from '../src/pages/SharePagesPage';
import { mockFetch } from './setup';

const MOCK_PAGES = {
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
      id: 'sp_extra',
      name: '额外分享页',
      ragflow_type: 'chat',
      ragflow_resource_id: 'dialog-456',
      embed_type: 'fullscreen',
      enabled: true,
      created_at: 1700000001,
    },
  ],
};

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/share-pages']}>
      <AuthProvider>
        <Routes>
          <Route path="/share-pages" element={<SharePagesPage />} />
          <Route path="/share-pages/:id" element={<div data-testid="detail">详情页</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('SharePagesPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示用户被授权的分享页列表', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages', status: 200, body: MOCK_PAGES },
    ]);

    renderList();

    expect(await screen.findByText('默认分享页')).toBeInTheDocument();
    expect(screen.getByText('额外分享页')).toBeInTheDocument();
  });

  it('点击"打开"跳转到分享页详情', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages', status: 200, body: MOCK_PAGES },
    ]);

    renderList();

    const links = await screen.findAllByRole('link', { name: '打开' });
    await user.click(links[0]);

    expect(await screen.findByTestId('detail')).toBeInTheDocument();
  });

  it('列表为空时显示空状态提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages', status: 200, body: { share_pages: [] } },
    ]);

    renderList();

    expect(await screen.findByText('暂无被授权的分享页')).toBeInTheDocument();
  });

  it('加载失败显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages', status: 500, body: { detail: '服务器错误' } },
    ]);

    renderList();

    expect(await screen.findByText('服务器错误')).toBeInTheDocument();
  });
});
