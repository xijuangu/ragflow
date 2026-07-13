import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import AdminLayout from '../src/components/AdminLayout';
import { mockFetch } from './setup';

describe('AdminLayout', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('移动端菜单可打开、点击遮罩关闭，并同步 aria 状态', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
    ]);

    render(
      <MemoryRouter initialEntries={['/admin/users']}>
        <AuthProvider>
          <Routes>
            <Route path="/admin" element={<AdminLayout />}>
              <Route path="users" element={<div>用户页</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    const openButton = await screen.findByRole('button', { name: '打开管理菜单' });
    expect(openButton).toHaveAttribute('aria-expanded', 'false');

    await user.click(openButton);
    expect(screen.getByRole('button', { name: '关闭管理菜单', expanded: true })).toBeInTheDocument();
    expect(screen.getByLabelText('管理后台导航')).toHaveClass('open');

    const closeButtons = screen.getAllByRole('button', { name: '关闭管理菜单' });
    await user.click(closeButtons[1]);
    expect(screen.getByRole('button', { name: '打开管理菜单' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByLabelText('管理后台导航')).not.toHaveClass('open');
  });
});
