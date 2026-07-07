/**
 * 分享页详情页测试 — 验证 iframe 渲染与 session 预创建。
 *
 * 对应 Slice 9 验收点 3:iframe 加载 RAGFlow 对话界面。
 * 对应 Slice 9 验收点 4:iframe URL 来自预创建端点,不含真实 beta Token。
 * 对应 Slice 21:fullscreen 类型挂载时调 POST /sessions(预创建 session,绑定归属),
 *               而非 GET /embed-url(不预创建,导致后续 /completions 403)。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePageDetailPage from '../src/pages/SharePageDetailPage';
import { mockFetch } from './setup';

// Slice 21:fullscreen 类型挂载调预创建端点,响应含 session_id + iframe_url(已带 session_id 参数)
const PRECREATE_RESPONSE = {
  session_id: 'sess-abc-123',
  iframe_url:
    '/chats/share?shared_id=dialog-123&auth=pt_T_short_abc&from=chat&session_id=sess-abc-123',
  share_page_id: 'sp_default',
};

// fullscreen 类型 embed-url 响应(无 snippet/widget_url,触发 precreateSession 分支)
const FULLSCREEN_EMBED_RESPONSE = {
  iframe_url: '',
  ragflow_type: 'chat',
  share_page_id: 'sp_default',
  expires_in: 300,
};

// widget 类型仍调 embed-url(返回 snippet,不预创建 session)
const WIDGET_EMBED_RESPONSE = {
  embed_type: 'widget',
  widget_url: '/widget/sp_widget',
  snippet: '<iframe class="ragflow-widget-frame" src="/widget/sp_widget"></iframe>',
  share_page_id: 'sp_widget',
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

  it('Slice 21:fullscreen 类型挂载调 POST /sessions(预创建),iframe src 含 session_id', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: FULLSCREEN_EMBED_RESPONSE },
      {
        url: '/share-pages/sp_default/sessions',
        method: 'POST',
        status: 200,
        body: PRECREATE_RESPONSE,
      },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    expect(iframe).toBeInTheDocument();
    // iframe src 来自预创建响应(含 session_id 参数)
    expect(iframe).toHaveAttribute('src', PRECREATE_RESPONSE.iframe_url);
    expect(iframe.getAttribute('src')).toContain('session_id=sess-abc-123');
  });

  it('iframe URL 不含真实 beta Token(仅含 T_short 的 auth 参数)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: FULLSCREEN_EMBED_RESPONSE },
      {
        url: '/share-pages/sp_default/sessions',
        method: 'POST',
        status: 200,
        body: PRECREATE_RESPONSE,
      },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    const src = iframe.getAttribute('src') ?? '';

    // auth 参数存在(T_short,Slice 19 带 pt_ 前缀)
    expect(src).toContain('auth=pt_T_short_abc');
    // 不含真实 beta Token
    expect(src).not.toMatch(/[?&]beta=/);
    expect(src).not.toContain('ragflow-');
  });

  it('Slice 21:widget 类型仍调 GET /embed-url(返回 snippet,不预创建 session)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_widget/embed-url', status: 200, body: WIDGET_EMBED_RESPONSE },
      {
        url: '/share-pages/sp_widget/sessions',
        method: 'GET',
        status: 200,
        body: { sessions: [] },
      },
    ]);

    renderDetail('sp_widget');

    // widget 类型渲染 snippet 面板(不渲染 iframe)
    expect(await screen.findByText(/复制 snippet/)).toBeInTheDocument();
    expect(screen.queryByTitle('RAGFlow 对话')).not.toBeInTheDocument();
  });

  it('预创建 session 返回 403 时显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: FULLSCREEN_EMBED_RESPONSE },
      {
        url: '/share-pages/sp_default/sessions',
        method: 'POST',
        status: 403,
        body: { detail: '无权访问该分享页' },
      },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);

    renderDetail();

    expect(await screen.findByText('无权访问该分享页')).toBeInTheDocument();
    expect(screen.queryByTitle('RAGFlow 对话')).not.toBeInTheDocument();
  });
});
