/**
 * Slice 16 前端测试 — 悬浮组件(widget)+ Agent 支持。
 *
 * 覆盖验收点(ISSUES.md Issue 16):
 *   1. 管理员创建分享页时能选择 embed_type=widget / ragflow_type=agent。
 *   2. widget 类型详情页展示 snippet(可复制)而非 iframe。
 *   3. agent 类型详情页加载 /agent/share 路径的 iframe URL。
 *
 * 设计:
 *   - SharePagesAdminPage:填表 + 选下拉 + 提交,断言 POST body 含 embed_type/ragflow_type。
 *   - SharePageDetailPage widget:mock embed-url 返回 widget_url + snippet,断言展示 snippet。
 *   - SharePageDetailPage agent:mock embed-url 返回 /agent/share 路径,断言 iframe src。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePagesAdminPage from '../src/pages/admin/SharePagesAdminPage';
import SharePageDetailPage from '../src/pages/SharePageDetailPage';
import { getFetchCalls, mockFetch } from './setup';

function renderAdminPage() {
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

function renderDetail(id = 'sp_widget') {
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

describe('Slice 16 — SharePagesAdminPage widget/agent 选择器', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('创建 widget 类型分享页 — POST body 含 embed_type=widget', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: { share_pages: [] } },
      {
        url: '/admin/share-pages',
        method: 'POST',
        status: 201,
        body: {
          id: 'sp_widget_new',
          name: '悬浮组件',
          ragflow_type: 'chat',
          ragflow_resource_id: 'dialog-widget',
          embed_type: 'widget',
          enabled: true,
          created_at: 1700000300,
        },
      },
    ]);
    globalThis.fetch = fetchMock;

    renderAdminPage();

    // 等待表单加载(用 label 而非标题文本,避免与按钮文本冲突)
    await screen.findByLabelText('分享页名称');

    await user.type(screen.getByLabelText('分享页名称'), '悬浮组件');
    await user.type(screen.getByLabelText('RAGFlow 资源 ID'), 'dialog-widget');
    await user.selectOptions(screen.getByLabelText('嵌入类型'), 'widget');
    await user.click(screen.getByRole('button', { name: '创建分享页' }));

    const calls = getFetchCalls(fetchMock);
    const postCall = calls.find(
      (c) => c.url === '/admin/share-pages' && c.method === 'POST',
    );
    expect(postCall).toBeDefined();
    expect(postCall!.body).toEqual({
      name: '悬浮组件',
      ragflow_resource_id: 'dialog-widget',
      embed_type: 'widget',
      ragflow_type: 'chat',
    });

    // 创建后列表含新分享页(用 row testid 定位,避免与表单文本冲突)
    expect(await screen.findByTestId('share-page-row-sp_widget_new')).toBeInTheDocument();
    // row 内含 widget 文本(embed_type 列)
    const row = screen.getByTestId('share-page-row-sp_widget_new');
    expect(row.textContent).toContain('widget');
  });

  it('创建 agent 类型分享页 — POST body 含 ragflow_type=agent', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/admin/share-pages', status: 200, body: { share_pages: [] } },
      {
        url: '/admin/share-pages',
        method: 'POST',
        status: 201,
        body: {
          id: 'sp_agent_new',
          name: 'Agent 分享页',
          ragflow_type: 'agent',
          ragflow_resource_id: 'agent-001',
          embed_type: 'fullscreen',
          enabled: true,
          created_at: 1700000400,
        },
      },
    ]);
    globalThis.fetch = fetchMock;

    renderAdminPage();

    await screen.findByLabelText('分享页名称');

    await user.type(screen.getByLabelText('分享页名称'), 'Agent 分享页');
    await user.type(screen.getByLabelText('RAGFlow 资源 ID'), 'agent-001');
    await user.selectOptions(screen.getByLabelText('RAGFlow 类型'), 'agent');
    await user.click(screen.getByRole('button', { name: '创建分享页' }));

    const calls = getFetchCalls(fetchMock);
    const postCall = calls.find(
      (c) => c.url === '/admin/share-pages' && c.method === 'POST',
    );
    expect(postCall).toBeDefined();
    expect(postCall!.body).toEqual({
      name: 'Agent 分享页',
      ragflow_resource_id: 'agent-001',
      embed_type: 'fullscreen',
      ragflow_type: 'agent',
    });

    // row 内含 agent 文本(ragflow_type 列)
    expect(await screen.findByTestId('share-page-row-sp_agent_new')).toBeInTheDocument();
    const row = screen.getByTestId('share-page-row-sp_agent_new');
    expect(row.textContent).toContain('agent');
  });

  it('列表展示 embed_type 与 ragflow_type 列', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      {
        url: '/admin/share-pages',
        status: 200,
        body: {
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
              id: 'sp_widget',
              name: '悬浮组件',
              ragflow_type: 'chat',
              ragflow_resource_id: 'dialog-widget',
              embed_type: 'widget',
              enabled: true,
              created_at: 1700000100,
            },
            {
              id: 'sp_agent',
              name: 'Agent',
              ragflow_type: 'agent',
              ragflow_resource_id: 'agent-001',
              embed_type: 'fullscreen',
              enabled: false,
              created_at: 1700000200,
            },
          ],
        },
      },
    ]);

    renderAdminPage();

    expect(await screen.findByTestId('share-page-row-sp_default')).toBeInTheDocument();
    expect(screen.getByTestId('share-page-row-sp_widget')).toBeInTheDocument();
    expect(screen.getByTestId('share-page-row-sp_agent')).toBeInTheDocument();
    // 列头含「嵌入类型」「RAGFlow 类型」(用 table header role 定位,避免与 label 冲突)
    const headers = screen.getAllByRole('columnheader');
    const headerTexts = headers.map((h) => h.textContent);
    expect(headerTexts).toContain('嵌入类型');
    expect(headerTexts).toContain('RAGFlow 类型');
    // row 内展示 embed_type 与 ragflow_type 值
    expect(screen.getByTestId('share-page-row-sp_widget').textContent).toContain('widget');
    expect(screen.getByTestId('share-page-row-sp_agent').textContent).toContain('agent');
  });
});

describe('Slice 16 — SharePageDetailPage widget 类型展示 snippet', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('widget 类型详情页展示 snippet 面板(不渲染 iframe)', async () => {
    const snippet =
      '<iframe class="ragflow-widget-frame" src="/widget/sp_widget" style="position:fixed;bottom:20px;right:20px;width:400px;height:600px;border:0;" title="RAGFlow 悬浮组件"></iframe>';
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      {
        url: '/share-pages/sp_widget/embed-url',
        status: 200,
        body: {
          embed_type: 'widget',
          widget_url: '/widget/sp_widget',
          snippet,
          share_page_id: 'sp_widget',
          expires_in: 300,
        },
      },
      {
        url: '/share-pages/sp_widget/sessions',
        status: 200,
        body: { sessions: [] },
      },
    ]);

    renderDetail('sp_widget');

    // 等待 snippet 面板出现
    expect(await screen.findByTestId('widget-snippet-panel')).toBeInTheDocument();
    expect(await screen.findByTestId('widget-snippet-code')).toBeInTheDocument();
    // snippet 代码区域含 iframe 标签
    expect(screen.getByTestId('widget-snippet-code').textContent).toContain('<iframe');
    expect(screen.getByTestId('widget-snippet-code').textContent).toContain('/widget/sp_widget');
    // 不渲染 iframe 标题
    expect(screen.queryByTitle('RAGFlow 对话')).not.toBeInTheDocument();
    // 标题是「悬浮组件嵌入」
    expect(screen.getByText('悬浮组件嵌入')).toBeInTheDocument();
  });

  it('widget 类型含「复制 snippet」按钮', async () => {
    const snippet = '<iframe src="/widget/sp_widget2"></iframe>';
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      {
        url: '/share-pages/sp_widget2/embed-url',
        status: 200,
        body: {
          embed_type: 'widget',
          widget_url: '/widget/sp_widget2',
          snippet,
          share_page_id: 'sp_widget2',
          expires_in: 300,
        },
      },
      {
        url: '/share-pages/sp_widget2/sessions',
        status: 200,
        body: { sessions: [] },
      },
    ]);

    renderDetail('sp_widget2');

    expect(await screen.findByRole('button', { name: '复制 snippet' })).toBeInTheDocument();
  });

  it('agent 类型详情页加载 /agent/share 路径的 iframe', async () => {
    const agentIframeUrl =
      'http://ragflow.local/agent/share?shared_id=agent-001&auth=T_short_xyz&from=agent';
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      {
        url: '/share-pages/sp_agent/embed-url',
        status: 200,
        body: {
          iframe_url: agentIframeUrl,
          ragflow_type: 'agent',
          share_page_id: 'sp_agent',
          expires_in: 300,
        },
      },
      {
        url: '/share-pages/sp_agent/sessions',
        status: 200,
        body: { sessions: [] },
      },
    ]);

    renderDetail('sp_agent');

    const iframe = await screen.findByTitle('RAGFlow 对话');
    expect(iframe).toBeInTheDocument();
    expect(iframe).toHaveAttribute('src', agentIframeUrl);
    // iframe URL 走 /agent/share 路径(而非 /chat/share)
    expect(iframe.getAttribute('src')).toContain('/agent/share');
    expect(iframe.getAttribute('src')).toContain('from=agent');
  });
});
