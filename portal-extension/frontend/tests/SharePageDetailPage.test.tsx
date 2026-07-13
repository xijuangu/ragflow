/**
 * 分享页详情页测试 — 验证 iframe 渲染与 embed-url 加载。
 *
 * 对应 Slice 9 验收点 3:iframe 加载 RAGFlow 对话界面。
 * 对应 Slice 9 验收点 4:iframe URL 来自 embed-url 端点,不含真实 beta Token。
 *
 * Slice 22:回退 Slice 21 — fullscreen 类型改回调 GET /embed-url(不 precreate)。
 *   根因:RAGFlow 前端不从 URL 读 session_id,precreate 创建的 session 不会被 iframe 使用。
 *   session 归属改由网关在 SSE 代理时绑定(见 gateway.py Slice 22)。
 *   widget 类型仍用 embed-url(返回 snippet)。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePageDetailPage from '../src/pages/SharePageDetailPage';
import { mockFetch } from './setup';

// fullscreen 类型 embed-url 响应(iframe_url 含 T_short,无 session_id — 网关 SSE 绑定)
const FULLSCREEN_EMBED_RESPONSE = {
  iframe_url: '/chats/share?shared_id=dialog-123&auth=pt_T_short_abc&from=chat',
  ragflow_type: 'chat',
  share_page_id: 'sp_default',
  expires_in: 300,
};

// widget 类型 embed-url 响应(返回 snippet,不渲染 iframe)
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

  it('fullscreen 类型挂载调 GET /embed-url,iframe src 来自响应', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: FULLSCREEN_EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    expect(iframe).toBeInTheDocument();
    // iframe src 来自 embed-url 响应(Slice 22:不再调 precreate,URL 无 session_id)
    expect(iframe).toHaveAttribute('src', FULLSCREEN_EMBED_RESPONSE.iframe_url);
  });

  it('直接打开详情页时从可访问列表补齐分享页名称', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: FULLSCREEN_EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
      {
        url: '/share-pages',
        status: 200,
        body: {
          share_pages: [{
            id: 'sp_default',
            name: '财务知识库',
            ragflow_type: 'chat',
            ragflow_resource_id: 'dialog-123',
            embed_type: 'fullscreen',
            enabled: true,
            created_at: 1700000000,
          }],
        },
      },
    ]);

    renderDetail();

    expect(await screen.findByRole('heading', { name: '财务知识库' })).toBeInTheDocument();
  });

  it('iframe URL 不含真实 beta Token(仅含 T_short 的 auth 参数)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: FULLSCREEN_EMBED_RESPONSE },
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

  it('widget 类型调 GET /embed-url(返回 snippet,不渲染 iframe)', async () => {
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

  it('embed-url 返回 403 时显示错误提示', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 403, body: { detail: '无权访问该分享页' } },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);

    renderDetail();

    expect(await screen.findByText('无权访问该分享页')).toBeInTheDocument();
    expect(screen.queryByTitle('RAGFlow 对话')).not.toBeInTheDocument();
  });
});
