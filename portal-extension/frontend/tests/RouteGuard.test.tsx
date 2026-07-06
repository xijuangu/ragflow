/**
 * 路由守卫测试 — 验证未登录用户访问受保护页面跳转 /login。
 *
 * 对应 Slice 9 验收点 5:未登录用户直接访问分享页详情页 → 跳转登录页。
 * 对应 Slice 9 验收点 6:登出后回登录页,再访问分享页 → 跳转登录页。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import App from '../src/App';
import { mockFetch } from './setup';

function renderApp(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/*" element={<App />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('Route guard (ProtectedRoute)', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('未登录用户访问 /share-pages → 跳转 /login', async () => {
    // /me 返回 403(未登录)
    globalThis.fetch = mockFetch([{ url: '/me', status: 403, body: { detail: '未登录' } }]);

    renderApp('/share-pages');

    expect(await screen.findByText('RAGFlow 权限门户')).toBeInTheDocument();
    expect(screen.getByLabelText('用户名')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toBeInTheDocument();
  });

  it('未登录用户访问 /share-pages/:id → 跳转 /login', async () => {
    globalThis.fetch = mockFetch([{ url: '/me', status: 403, body: { detail: '未登录' } }]);

    renderApp('/share-pages/sp_default');

    expect(await screen.findByLabelText('用户名')).toBeInTheDocument();
  });

  it('已登录用户访问 /login → 跳转 /share-pages', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages', status: 200, body: { share_pages: [] } },
    ]);

    renderApp('/login');

    // 已登录 → 直接跳列表页(显示空状态)
    expect(await screen.findByText('暂无被授权的分享页')).toBeInTheDocument();
  });

  it('登出后回登录页(验证登出按钮触发跳转)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages', status: 200, body: { share_pages: [] } },
      { url: '/logout', method: 'POST', status: 200, body: { logged_out: true } },
    ]);

    renderApp('/share-pages');

    // 等列表加载
    expect(await screen.findByText('admin')).toBeInTheDocument();

    // 点登出
    await user.click(screen.getByRole('button', { name: '登出' }));

    // 回到登录页
    expect(await screen.findByLabelText('用户名')).toBeInTheDocument();
  });
});
