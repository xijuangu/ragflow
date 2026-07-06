/**
 * AdminRoute 守卫测试(Slice 11)— 验证非管理员访问 /admin 跳转。
 *
 * 对应 Issue 11 验收点 1:管理员能看到管理后台入口,普通用户看不到且直接访问 URL → 403/跳转。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import AdminRoute from '../src/components/AdminRoute';
import { mockFetch } from './setup';

function renderWithRoute(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route
            path="/admin/*"
            element={
              <AdminRoute>
                <div data-testid="admin-content">管理后台内容</div>
              </AdminRoute>
            }
          />
          <Route path="/login" element={<div data-testid="login">登录页</div>} />
          <Route path="/share-pages" element={<div data-testid="share-pages">分享页列表</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('AdminRoute 守卫', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('管理员访问 /admin → 渲染管理后台内容', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
    ]);

    renderWithRoute('/admin/users');

    expect(await screen.findByTestId('admin-content')).toBeInTheDocument();
  });

  it('普通用户访问 /admin → 跳转 /share-pages', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'user1', is_admin: false } },
    ]);

    renderWithRoute('/admin/users');

    expect(await screen.findByTestId('share-pages')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-content')).not.toBeInTheDocument();
  });

  it('未登录用户访问 /admin → 跳转 /login', async () => {
    globalThis.fetch = mockFetch([{ url: '/me', status: 403, body: { detail: '未登录' } }]);

    renderWithRoute('/admin/users');

    expect(await screen.findByTestId('login')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-content')).not.toBeInTheDocument();
  });
});
