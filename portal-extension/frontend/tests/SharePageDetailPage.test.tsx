/**
 * 分享页详情页测试 — 验证 embed-url 调用与 iframe 渲染。
 *
 * 对应 Slice 9 验收点 3:iframe 加载 RAGFlow 对话界面。
 * 对应 Slice 9 验收点 4:iframe URL 来自 embed-url 端点,不含真实 beta Token。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePageDetailPage from '../src/pages/SharePageDetailPage';
import { mockFetch } from './setup';

const EMBED_RESPONSE = {
  iframe_url: 'http://ragflow.local/chat/share?shared_id=dialog-123&auth=T_short_abc&from=chat',
  share_page_id: 'sp_default',
  expires_in: 300,
};

function renderDetail(id = 'sp_default') {
  return render(
    <MemoryRouter initialEntries={[`/share-pages/${id}`]}>
      <AuthProvider>
        <Routes>
          <Route path="/share-pages/:id" element={<SharePageDetailPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('SharePageDetailPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('调 embed-url 端点并渲染 iframe(src 来自响应)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
    ]);

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    expect(iframe).toBeInTheDocument();
    expect(iframe).toHaveAttribute('src', EMBED_RESPONSE.iframe_url);
  });

  it('iframe URL 不含真实 beta Token(仅含 T_short 的 auth 参数)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
    ]);

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    const src = iframe.getAttribute('src') ?? '';

    // auth 参数存在(T_short)
    expect(src).toContain('auth=T_short_abc');
    // 不含真实 beta Token(断言 URL 中无 "beta" / "RAGFlow-" 前缀的真实 token)
    expect(src).not.toMatch(/[?&]beta=/);
    expect(src).not.toContain('ragflow-');
  });

  it('embed-url 返回 403 时显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      {
        url: '/share-pages/sp_default/embed-url',
        status: 403,
        body: { detail: '无权访问该分享页' },
      },
    ]);

    renderDetail();

    expect(await screen.findByText('无权访问该分享页')).toBeInTheDocument();
    expect(screen.queryByTitle('RAGFlow 对话')).not.toBeInTheDocument();
  });
});
