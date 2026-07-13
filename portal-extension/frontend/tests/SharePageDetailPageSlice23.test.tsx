/**
 * Slice 23/25 前端测试 — 新建会话防抖 + 会话列表定时轮询刷新。
 *
 * 覆盖验收点(ISSUES.md Issue 23):
 *   1. 快速点击"新建会话"按钮 2 次 → 只调 1 次 POST /sessions(防抖)。
 *   2. iframe 加载后 2s 定时轮询 GET /sessions 刷新左侧列表(检测新会话)。
 *
 * 根因(Issue 23):
 *   - handleNewSession 用 sessionBusy state 做守卫,但 setSessionBusy(true) 异步,
 *     React 18 批处理 → 快速点击绕过守卫 → 多次 precreateSession → 弹多个会话。
 *   - 会话列表只在挂载时调一次 listSessions,发消息后不刷新 → 看不到新会话。
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

describe('Slice 23 — 新建会话防抖 + 会话列表轮询', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('快速点击"新建会话"2 次 → 只触发 1 次新建会话请求(防抖)', async () => {
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: { sessions: [] } },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();

    await screen.findByTitle('RAGFlow 对话');

    // 用 fireEvent.click(同步)快速点击 2 次 — 模拟用户双击绕过 state 守卫
    // useRef 守卫(ref 赋值同步)应阻止第二次点击进入新建会话请求
    const newBtn = screen.getByRole('button', { name: '新建会话' });
    fireEvent.click(newBtn);
    fireEvent.click(newBtn);

    // 等待异步操作完成,断言只触发 1 次新建会话请求(防抖生效)
    // Slice 28:fullscreen 新建会话改调 GET /embed-url(初始挂载 1 次 + 新建 1 次 = 2 次)
    // 若防抖失效,新建会话会调 2 次 → 总 3 次
    await waitFor(() => {
      const calls = getFetchCalls(fetchMock);
      const embedCalls = calls.filter(
        (c) => c.url === '/share-pages/sp_default/embed-url' && c.method === 'GET',
      );
      expect(embedCalls.length).toBe(2);
    });
  });

  it('iframe 加载后 2s 定时轮询 GET /sessions 刷新左侧列表(检测新会话)', async () => {
    // 初始列表为空,轮询后返回新会话
    const SESSION_AFTER_POLL = {
      sessions: [
        {
          session_id: 'sess-from-ragflow',
          title: '新会话',
          created_at: 1700000000,
          last_active_at: 1700000100,
          message_count: 1,
        },
      ],
    };
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: { sessions: [] } },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSION_AFTER_POLL },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();

    // 初始列表为空
    expect(await screen.findByText('暂无历史会话，开始新对话')).toBeInTheDocument();

    // 等待轮询触发:断言 GET /sessions 被调用 > 1 次(初始 + 至少 1 次轮询)
    // mockFetch 对同 URL+method 总匹配第一个响应(空列表),这里只验证轮询发生,
    // 列表内容变化由 E2E 验证(轮询调 listSessions 后 setSessions 更新)
    await waitFor(
      () => {
        const calls = getFetchCalls(fetchMock);
        const getCalls = calls.filter(
          (c) => c.url === '/share-pages/sp_default/sessions' && c.method === 'GET',
        );
        expect(getCalls.length).toBeGreaterThan(1);
      },
      { timeout: 3000 },
    );
  }, 6000);
});
