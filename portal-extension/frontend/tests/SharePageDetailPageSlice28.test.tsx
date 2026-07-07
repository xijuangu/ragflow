/**
 * Slice 28 前端测试 — 修复「新建会话」按钮创建空 session 无 greeting。
 *
 * 覆盖验收点(ISSUES.md Issue 28):
 *   1. fullscreen 类型点「新建会话」→ 调 GET /embed-url(而非 POST /sessions)。
 *   2. fullscreen 类型新建会话后 iframe src 不含 session_id(RAGFlow 前端走
 *      fetchSessionId 创建新 session + greeting,而非读 URL session_id 恢复空 session)。
 *   3. widget 类型保持原 precreate 行为(Slice 16 约定,widget 不重载 iframe)。
 *
 * 根因(Issue 28):
 *   - Slice 24 改了 RAGFlow 前端 use-send-shared-message.ts:URL 有 session_id 参数
 *     → 跳过 fetchSessionId(greeting)→ 调 GET history 恢复 derivedMessages。
 *   - 但 portal 前端 handleNewSession 仍调 precreateSession,返回的 iframe_url 带
 *     session_id 参数 → RAGFlow 前端读 URL session_id → 跳过 greeting → 取 precreate
 *     的空 session → 显示空。
 *   - 修复:fullscreen 类型改调 getEmbedUrl,用不含 session_id 的 iframe_url 重载
 *     iframe,让 RAGFlow 前端走 fetchSessionId 创建新 session + greeting。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import SharePageDetailPage from '../src/pages/SharePageDetailPage';
import { getFetchCalls, mockFetch } from './setup';

const EMBED_RESPONSE = {
  iframe_url: '/chats/share?shared_id=dialog-123&auth=pt_T_short_abc&from=chat',
  ragflow_type: 'chat',
  share_page_id: 'sp_default',
  expires_in: 300,
};

const PRECREATE_RESPONSE = {
  session_id: 'sess-new-999',
  iframe_url:
    '/chats/share?shared_id=dialog-123&auth=pt_T_short_new&from=chat&session_id=sess-new-999',
  share_page_id: 'sp_default',
};

const WIDGET_EMBED_RESPONSE = {
  embed_type: 'widget',
  widget_url: '/widget/sp_widget',
  snippet: '<iframe class="ragflow-widget-frame" src="/widget/sp_widget"></iframe>',
  share_page_id: 'sp_widget',
  expires_in: 300,
};

const WIDGET_PRECREATE_RESPONSE = {
  session_id: 'sess-widget-new',
  iframe_url:
    '/chats/share?shared_id=dialog-w&auth=pt_T_short_w&from=chat&session_id=sess-widget-new',
  share_page_id: 'sp_widget',
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

describe('Slice 28 — 新建会话显示 greeting(fullscreen 走 getEmbedUrl)', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('fullscreen 类型点「新建会话」→ 调 GET /embed-url(而非 POST /sessions)', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
      { url: '/share-pages/sp_default/sessions', method: 'POST', status: 200, body: PRECREATE_RESPONSE },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();

    await screen.findByTitle('RAGFlow 对话');

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    await waitFor(() => {
      const calls = getFetchCalls(fetchMock);
      const embedCalls = calls.filter(
        (c) => c.url === '/share-pages/sp_default/embed-url' && c.method === 'GET',
      );
      const postSessionsCalls = calls.filter(
        (c) => c.url === '/share-pages/sp_default/sessions' && c.method === 'POST',
      );
      // 初始挂载 + 新建会话 = 2 次 GET /embed-url
      expect(embedCalls.length).toBe(2);
      // 不应再调 POST /sessions(precreateSession)
      expect(postSessionsCalls.length).toBe(0);
    });
  });

  it('fullscreen 类型新建会话后 iframe src 不含 session_id(greeting 走 fetchSessionId)', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
      { url: '/share-pages/sp_default/sessions', method: 'POST', status: 200, body: PRECREATE_RESPONSE },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    // 初始 src 不含 session_id
    expect(iframe.getAttribute('src') ?? '').not.toMatch(/[?&]session_id=/);

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    await waitFor(() => {
      const calls = getFetchCalls(fetchMock);
      const embedCalls = calls.filter(
        (c) => c.url === '/share-pages/sp_default/embed-url' && c.method === 'GET',
      );
      // 确认 handleNewSession 已触发(第二次 embed-url 调用)
      expect(embedCalls.length).toBe(2);
    });

    // 新建会话后 src 仍不含 session_id(RAGFlow 前端走 fetchSessionId 创建新 greeting session)
    const updatedIframe = screen.getByTitle('RAGFlow 对话');
    expect(updatedIframe.getAttribute('src') ?? '').not.toMatch(/[?&]session_id=/);
  });

  it('widget 类型保持原 precreate 行为(调 POST /sessions,Slice 16 约定)', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_widget/embed-url', status: 200, body: WIDGET_EMBED_RESPONSE },
      { url: '/share-pages/sp_widget/sessions', method: 'GET', status: 200, body: { sessions: [] } },
      {
        url: '/share-pages/sp_widget/sessions',
        method: 'POST',
        status: 200,
        body: WIDGET_PRECREATE_RESPONSE,
      },
      {
        url: '/share-pages/sp_widget/sessions',
        method: 'GET',
        status: 200,
        body: { sessions: [] },
      },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail('sp_widget');

    await screen.findByText(/复制 snippet/);

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    await waitFor(() => {
      const calls = getFetchCalls(fetchMock);
      const postCalls = calls.filter(
        (c) => c.url === '/share-pages/sp_widget/sessions' && c.method === 'POST',
      );
      // widget 类型仍调 POST /sessions(Slice 16 约定保留)
      expect(postCalls.length).toBe(1);
    });
  });
});
