/**
 * 登录页测试 — 验证登录交互、错误提示、成功跳转。
 *
 * 对应 Slice 9 验收点 1:用户输入 admin 凭据登录成功,跳转分享页列表。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import LoginPage from '../src/pages/LoginPage';
import { mockFetch } from './setup';

function renderLogin(initialPath = '/login') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share-pages" element={<div data-testid="share-pages">列表页</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('LoginPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('显示用户名和密码输入框与登录按钮', async () => {
    // 初始探测 /me 返回 403(未登录)
    globalThis.fetch = mockFetch([{ url: '/me', status: 403, body: { detail: '未登录' } }]);

    renderLogin();

    // 用 findBy 等待 AuthProvider 的异步 /me 探测完成,避免 act 警告
    expect(await screen.findByLabelText('用户名')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
  });

  it('登录成功后跳转到分享页列表', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch([
      { url: '/me', status: 403, body: { detail: '未登录' } },
      {
        url: '/login',
        method: 'POST',
        status: 200,
        body: { username: 'admin', is_admin: true },
      },
    ]);
    globalThis.fetch = fetchMock;

    renderLogin();

    await user.type(screen.getByLabelText('用户名'), 'admin');
    await user.type(screen.getByLabelText('密码'), 'password');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByTestId('share-pages')).toBeInTheDocument();
  });

  it('登录失败显示后端返回的错误提示', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 403, body: { detail: '未登录' } },
      {
        url: '/login',
        method: 'POST',
        status: 401,
        body: { detail: '密码错误' },
      },
    ]);

    renderLogin();

    await user.type(screen.getByLabelText('用户名'), 'admin');
    await user.type(screen.getByLabelText('密码'), 'wrong');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByText('密码错误')).toBeInTheDocument();
    expect(screen.queryByTestId('share-pages')).not.toBeInTheDocument();
  });

  it('登录失败显示账号已禁用提示', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 403, body: { detail: '未登录' } },
      {
        url: '/login',
        method: 'POST',
        status: 403,
        body: { detail: '账号已禁用' },
      },
    ]);

    renderLogin();

    await user.type(screen.getByLabelText('用户名'), 'admin');
    await user.type(screen.getByLabelText('密码'), 'pass');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByText('账号已禁用')).toBeInTheDocument();
  });
});
