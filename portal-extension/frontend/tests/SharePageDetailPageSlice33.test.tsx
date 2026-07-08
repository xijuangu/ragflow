/**
 * Slice 33 前端测试 — SSE 流式响应期间禁用会话切换,避免消息丢失。
 *
 * 覆盖验收点(Issue 33):
 *   1. 收到 postMessage start → 点击「新建会话」不执行切换(iframeUrl 不变)+ 显示提示。
 *   2. 收到 postMessage end → 点击「新建会话」正常切换(iframe 重载)。
 *   3. 非同源 postMessage 被忽略(切换正常执行)。
 *   4. start 拦截 handleReopen(切换历史会话),end 后恢复 — 验证两个切换入口都被守卫。
 *
 * 根因:portal 改 iframeUrl → iframe 重载 → SSE 中止 → 消息丢失。
 *   修复:RAGFlow 前端在 SSE send 前后 postMessage start/end;portal 监听同源消息,
 *   流式期间在 handleNewSession / handleReopen 入口拦截(按钮不禁用,点击弹提示)。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const SESSION_ROW = {
  session_id: 'sess-history-1',
  title: '历史会话一',
  created_at: 1717000000,
  last_active_at: 1717000100,
  message_count: 3,
};

/** 模拟 iframe 内 RAGFlow 向 parent 发 postMessage(act 包裹以 flush React 状态)。 */
function postMessageFromIframe(type: string, origin = window.location.origin) {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', { data: { type }, origin }),
    );
  });
}

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

function embedCallCount(mock: typeof fetch) {
  return getFetchCalls(mock).filter(
    (c) => c.url === '/share-pages/sp_default/embed-url' && c.method === 'GET',
  ).length;
}

describe('Slice 33 — SSE 流式期间禁用会话切换(postMessage 守卫)', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('收到 start → 点击「新建会话」不执行切换(iframeUrl 不变)+ 显示提示', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();
    const iframe = await screen.findByTitle('RAGFlow 对话');
    const initialSrc = iframe.getAttribute('src');
    const initialEmbedCount = embedCallCount(fetchMock);

    // 模拟 iframe 内 RAGFlow 发出 SSE 开始消息
    postMessageFromIframe('ragflow:completions:start');

    // 点击「新建会话」(按钮未禁用,仍可点)
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    // 提示文案出现
    await waitFor(() => {
      expect(screen.getByTestId('streaming-notice')).toBeInTheDocument();
    });
    // iframe src 不变(切换被拦截)
    expect(screen.getByTitle('RAGFlow 对话').getAttribute('src')).toBe(initialSrc);
    // 未发起额外的 embed-url 调用(切换未执行)
    expect(embedCallCount(fetchMock)).toBe(initialEmbedCount);
  });

  it('收到 end → 点击「新建会话」正常切换(发起第二次 embed-url)', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();
    await screen.findByTitle('RAGFlow 对话');

    // start 后 end:流式结束,isStreaming 恢复 false
    postMessageFromIframe('ragflow:completions:start');
    postMessageFromIframe('ragflow:completions:end');

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    // 切换正常执行:发起第二次 GET /embed-url
    await waitFor(() => {
      expect(embedCallCount(fetchMock)).toBe(2);
    });
    // 提示不应出现
    expect(screen.queryByTestId('streaming-notice')).toBeNull();
  });

  it('非同源 postMessage 被忽略(切换正常执行)', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', method: 'GET', status: 200, body: { sessions: [] } },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();
    await screen.findByTitle('RAGFlow 对话');

    // 伪造跨域 origin,start 应被忽略
    postMessageFromIframe('ragflow:completions:start', 'https://evil.example.com');

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    // 切换正常执行(非同源消息被忽略,isStreaming 仍为 false)
    await waitFor(() => {
      expect(embedCallCount(fetchMock)).toBe(2);
    });
    expect(screen.queryByTestId('streaming-notice')).toBeNull();
  });

  it('start 拦截 handleReopen(切换历史会话),end 后恢复', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      {
        url: '/share-pages/sp_default/sessions',
        method: 'GET',
        status: 200,
        body: { sessions: [SESSION_ROW] },
      },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();
    // 等待会话列表渲染出「历史会话一」
    const sessionBtn = await screen.findByRole('button', { name: /历史会话一/ });
    const initialEmbedCount = embedCallCount(fetchMock);

    // start → 点击历史会话被拦截
    postMessageFromIframe('ragflow:completions:start');
    fireEvent.click(sessionBtn);
    // 提示出现,且未发起额外 embed-url(切换被拦截)
    await waitFor(() => {
      expect(screen.getByTestId('streaming-notice')).toBeInTheDocument();
    });
    expect(embedCallCount(fetchMock)).toBe(initialEmbedCount);

    // end → 流式结束,提示隐藏,切换恢复
    postMessageFromIframe('ragflow:completions:end');
    await waitFor(() => {
      expect(screen.queryByTestId('streaming-notice')).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: /历史会话一/ }));
    // 切换执行:发起第二次 embed-url
    await waitFor(() => {
      expect(embedCallCount(fetchMock)).toBe(initialEmbedCount + 1);
    });
  });
});
