/**
 * Slice 10 —「我的会话」列表 + 重新打开 + 重命名/删除/新建 测试。
 *
 * 对应 Issue 10 验收点:
 *   1. 用户能看到「我的会话」列表(标题、时间、消息数)
 *   2. 点击历史会话重新打开,iframe URL 带 session_id(RAGFlow 原生恢复历史)
 *   3. 重新打开后能继续提问(iframe 同源,流式由网关代理 — 此处仅断言 iframe 重载)
 *   4. 重命名会话,列表标题实时更新
 *   5. 删除会话,列表实时移除
 *   6. 新建会话按钮加载空会话并显示 greeting(Slice 28:fullscreen 走 getEmbedUrl)
 *   7. 用户看不到他人会话(后端隔离,前端列表只来自 GET /sessions)
 *
 * 测试通过公开 UI 行为验证,不耦合内部实现。iframe 重载通过 src 属性断言。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

const SESSIONS_RESPONSE = {
  sessions: [
    {
      session_id: 'sess-001',
      title: '会话一',
      created_at: 1700000000,
      last_active_at: 1700000100,
      message_count: 4,
    },
    {
      session_id: 'sess-002',
      title: '会话二',
      created_at: 1700000200,
      last_active_at: 1700000300,
      message_count: 2,
    },
  ],
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

describe('SharePageDetailPage — 我的会话(Slice 10)', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('加载并显示「我的会话」列表(标题、消息数)', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderDetail();

    expect(await screen.findByText('会话一')).toBeInTheDocument();
    expect(screen.getByText('会话二')).toBeInTheDocument();
    // 消息数可见(验收点 1;Slice 57 ci-meta 文案为「N 条」,对齐设计稿)
    expect(screen.getByText(/4\s*条/)).toBeInTheDocument();
    expect(screen.getByText(/2\s*条/)).toBeInTheDocument();
  });

  it('会话历史在小屏可展开和收起，默认优先保留对话区', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderDetail();

    const toggle = await screen.findByRole('button', { name: '展开' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByLabelText('我的会话')).not.toHaveClass('expanded');

    await user.click(toggle);
    expect(screen.getByRole('button', { name: '收起' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('我的会话')).toHaveClass('expanded');
  });

  it('点击历史会话重新打开 — iframe URL 追加 session_id(验收点 2)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderDetail();

    const iframe = await screen.findByTitle('RAGFlow 对话');
    // Slice 22:初始 iframe URL 来自 embed-url(无 session_id — 网关 SSE 绑定)
    expect(iframe.getAttribute('src')).not.toContain('session_id=');

    // 点击「会话一」重新打开
    await user.click(screen.getByRole('button', { name: /会话一/ }));

    // iframe URL 重新加载并追加 session_id(重新打开调 embed-url + appendSessionId)
    const reopened = await screen.findByTitle('RAGFlow 对话');
    const src = reopened.getAttribute('src') ?? '';
    expect(src).toContain('session_id=sess-001');
    // Slice 19:T_short 带 pt_ 前缀(portal 令牌)
    expect(src).toContain('auth=pt_T_short_abc');
  });

  it('点击「新建会话」加载空会话并显示 greeting(Slice 28:fullscreen 走 getEmbedUrl,验收点 6)', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: { sessions: [] } },
    ]);
    globalThis.fetch = fetchMock;

    renderDetail();

    await screen.findByTitle('RAGFlow 对话');

    await user.click(screen.getByRole('button', { name: '新建会话' }));

    // Slice 28:fullscreen 新建会话改调 GET /embed-url(初始挂载 1 次 + 新建 1 次 = 2 次),
    // 不再调 POST /sessions(precreateSession)
    await waitFor(() => {
      const embedCalls = getFetchCalls(fetchMock).filter(
        (c) => c.url === '/share-pages/sp_default/embed-url' && c.method === 'GET',
      );
      expect(embedCalls.length).toBe(2);
    });

    const iframe = screen.getByTitle('RAGFlow 对话');
    const src = iframe.getAttribute('src') ?? '';
    // iframe_url 不含 session_id(RAGFlow 前端走 fetchSessionId 创建新 session + greeting,
    // 而非读 URL session_id 恢复空 precreate session)
    expect(src).toBe(EMBED_RESPONSE.iframe_url);
    expect(src).not.toContain('session_id=');
  });

  it('重命名会话 — 输入弹窗 + PATCH + 列表标题实时更新(验收点 4)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
      {
        url: '/share-pages/sp_default/sessions/sess-001',
        method: 'PATCH',
        status: 200,
        body: { session_id: 'sess-001', title: '新标题' },
      },
    ]);

    renderDetail();

    // 等列表加载
    const item = await screen.findByRole('button', { name: /会话一/ });
    // 在该会话项的容器内找「重命名」按钮
    const renameBtn = within(item.closest('[data-session-item]') as HTMLElement).getByRole('button', {
      name: '重命名',
    });
    await user.click(renameBtn);

    const dialog = screen.getByRole('dialog', { name: '重命名会话' });
    const input = within(dialog).getByLabelText('会话名称');
    await user.clear(input);
    await user.type(input, '新标题');
    await user.click(within(dialog).getByRole('button', { name: '保存' }));
    // 列表标题实时更新为「新标题」(乐观更新,原「会话一」不再以旧标题出现)
    expect(await screen.findByText('新标题')).toBeInTheDocument();
  });

  it('重命名时弹窗取消不调 PATCH', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderDetail();

    const item = await screen.findByRole('button', { name: /会话一/ });
    const renameBtn = within(item.closest('[data-session-item]') as HTMLElement).getByRole('button', {
      name: '重命名',
    });
    await user.click(renameBtn);

    const dialog = screen.getByRole('dialog', { name: '重命名会话' });
    await user.click(within(dialog).getByRole('button', { name: '取消' }));

    // 没有 PATCH mock,若误调会返回 404;标题保持原样
    expect(screen.getByText('会话一')).toBeInTheDocument();
  });

  it('删除会话 — 统一弹窗 + DELETE + 列表实时移除(验收点 5)', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
      {
        url: '/share-pages/sp_default/sessions/sess-001',
        method: 'DELETE',
        status: 200,
        body: { session_id: 'sess-001', deleted: true },
      },
    ]);

    renderDetail();

    const item = await screen.findByRole('button', { name: /会话一/ });
    const deleteBtn = within(item.closest('[data-session-item]') as HTMLElement).getByRole('button', {
      name: '删除',
    });
    await user.click(deleteBtn);

    const dialog = screen.getByRole('dialog', { name: '删除会话' });
    expect(within(dialog).getByText('此操作不可恢复')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '确认删除' }));
    // 列表实时移除(乐观更新)
    expect(screen.queryByText('会话一')).not.toBeInTheDocument();
    expect(screen.getByText('会话二')).toBeInTheDocument();
  });

  it('删除时弹窗取消不调 DELETE', async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: SESSIONS_RESPONSE },
    ]);

    renderDetail();

    const item = await screen.findByRole('button', { name: /会话一/ });
    const deleteBtn = within(item.closest('[data-session-item]') as HTMLElement).getByRole('button', {
      name: '删除',
    });
    await user.click(deleteBtn);

    const dialog = screen.getByRole('dialog', { name: '删除会话' });
    await user.click(within(dialog).getByRole('button', { name: '取消' }));

    expect(screen.getByText('会话一')).toBeInTheDocument();
  });

  it('会话列表为空时显示空状态', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 200, body: { sessions: [] } },
    ]);

    renderDetail();

    expect(await screen.findByText('暂无历史会话，开始新对话')).toBeInTheDocument();
  });

  it('会话列表加载失败显示错误提示但不阻断 iframe', async () => {
    globalThis.fetch = mockFetch([
      { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
      { url: '/share-pages/sp_default/embed-url', status: 200, body: EMBED_RESPONSE },
      { url: '/share-pages/sp_default/sessions', status: 500, body: { detail: '会话列表加载失败' } },
    ]);

    renderDetail();

    expect(await screen.findByText('会话列表加载失败')).toBeInTheDocument();
    // iframe 仍正常加载(会话侧栏失败不阻断对话)
    expect(await screen.findByTitle('RAGFlow 对话')).toBeInTheDocument();
  });
});
