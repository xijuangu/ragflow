/**
 * 用量统计页测试(Issue 92)— 汇总/明细两视图 + 筛选/排序 + CSV 导出一致性。
 *
 * 覆盖 AC:
 *   - 汇总视图按用户聚合(默认本月),列 = 问答次数、input/output token、incomplete 次数;
 *   - 明细视图逐条 usage_record;
 *   - 筛选(时间段/分享页)、排序遵循 Issue 86 全站约定;
 *   - CSV 导出内容与当前筛选结果一致;
 *   - 页面标注口径为 RAGFlow 本地计词,非厂商账单。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../src/auth/AuthContext';
import UsageStatsAdminPage from '../src/pages/admin/UsageStatsAdminPage';
import { aggregateUsageByUser } from '../src/pages/admin/usageAggregation';
import { buildCsv } from '../src/utils/csv';
import { mockFetch } from './setup';

// downloadCsv 走 DOM 下载(jsdom 无 URL.createObjectURL),mock 掉并捕获导出内容;
// buildCsv 保留真实实现,确保断言的是真实 CSV 文本。
vi.mock('../src/utils/csv', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/utils/csv')>();
  return { ...actual, downloadCsv: vi.fn() };
});
import { downloadCsv } from '../src/utils/csv';

const USERS_RESPONSE = {
  users: [
    { id: 'u_admin', username: 'admin', email: 'admin@example.com', is_admin: true, enabled: true, created_at: 1700000000, sso_provider: null, sso_external_id: null },
    { id: 'u_user1', username: 'user1', email: 'user1@example.com', is_admin: false, enabled: true, created_at: 1700000100, sso_provider: null, sso_external_id: null },
    { id: 'u_anonymous', username: 'anonymous', email: 'anonymous@portal.local', is_admin: false, enabled: true, created_at: 1700000200, sso_provider: null, sso_external_id: null },
  ],
};

const SHARE_PAGES_RESPONSE = {
  share_pages: [
    { id: 'sp_a', name: '页面A', ragflow_type: 'chat', ragflow_resource_id: 'dlg-a', embed_type: 'fullscreen', enabled: true, created_at: 1700000000 },
    { id: 'sp_b', name: '页面B', ragflow_type: 'chat', ragflow_resource_id: 'dlg-b', embed_type: 'fullscreen', enabled: true, created_at: 1700000001 },
  ],
};

// 动态日期:本月 5 日与上月 15 日(UTC),保证「默认本月」语义不随运行时间失效
const NOW = new Date();
const THIS_MONTH = Date.UTC(NOW.getUTCFullYear(), NOW.getUTCMonth(), 5, 12) / 1000;
const LAST_MONTH = Date.UTC(NOW.getUTCFullYear(), NOW.getUTCMonth() - 1, 15, 12) / 1000;

const USAGE_RESPONSE = {
  usage_records: [
    { id: 'ur_1', created_at: THIS_MONTH + 100, portal_user_id: 'u_admin', share_page_id: 'sp_a', session_id: 'sess-1', model_name: '', input_tokens: 10, output_tokens: 20, incomplete: false, org_id: 'default' },
    { id: 'ur_2', created_at: THIS_MONTH + 200, portal_user_id: 'u_admin', share_page_id: 'sp_a', session_id: 'sess-2', model_name: '', input_tokens: 3, output_tokens: 4, incomplete: true, org_id: 'default' },
    { id: 'ur_3', created_at: THIS_MONTH + 300, portal_user_id: 'u_user1', share_page_id: 'sp_b', session_id: 'sess-3', model_name: '', input_tokens: 5, output_tokens: 6, incomplete: false, org_id: 'default' },
    { id: 'ur_4', created_at: LAST_MONTH, portal_user_id: 'u_user1', share_page_id: 'sp_b', session_id: 'sess-4', model_name: '', input_tokens: 7, output_tokens: 8, incomplete: false, org_id: 'default' },
  ],
};

function mockAll() {
  globalThis.fetch = mockFetch([
    { url: '/me', status: 200, body: { username: 'admin', is_admin: true } },
    { url: '/admin/users', status: 200, body: USERS_RESPONSE },
    { url: '/admin/share-pages', status: 200, body: SHARE_PAGES_RESPONSE },
    { url: '/admin/usage-records?limit=10000', status: 200, body: USAGE_RESPONSE },
  ]);
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/usage']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/usage" element={<UsageStatsAdminPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('UsageStatsAdminPage', () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    vi.mocked(downloadCsv).mockClear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('汇总视图默认本月,按用户聚合(问答次数/token/incomplete 次数)', async () => {
    mockAll();
    renderPage();

    const adminRow = await screen.findByTestId('usage-summary-row-u_admin');
    const user1Row = screen.getByTestId('usage-summary-row-u_user1');

    // admin:本月 2 条(ur_1 + ur_2),input 13 / output 24,incomplete 1
    const adminCells = within(adminRow).getAllByRole('cell').map((c) => c.textContent);
    expect(adminCells).toEqual(['admin', '2', '13', '24', '1']);
    // user1:默认本月只计 ur_3(ur_4 在上月,被默认时间段筛掉)
    const user1Cells = within(user1Row).getAllByRole('cell').map((c) => c.textContent);
    expect(user1Cells).toEqual(['user1', '1', '5', '6', '0']);
  });

  it('清空起始日期后计入全部时间(上月记录入汇总)', async () => {
    const user = userEvent.setup();
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');

    await user.clear(screen.getByLabelText('起始日期'));

    const user1Cells = within(screen.getByTestId('usage-summary-row-u_user1'))
      .getAllByRole('cell')
      .map((c) => c.textContent);
    expect(user1Cells).toEqual(['user1', '2', '12', '14', '0']);
  });

  it('按分享页筛选(搜索 combobox),汇总随之变化', async () => {
    const user = userEvent.setup();
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');

    const combobox = screen.getByRole('combobox', { name: '分享页' });
    await user.click(combobox);
    await user.click(screen.getByRole('option', { name: /页面B/ }));

    expect(screen.queryByTestId('usage-summary-row-u_admin')).not.toBeInTheDocument();
    const user1Cells = within(screen.getByTestId('usage-summary-row-u_user1'))
      .getAllByRole('cell')
      .map((c) => c.textContent);
    expect(user1Cells).toEqual(['user1', '1', '5', '6', '0']);
  });

  it('汇总列排序:点击「输出 token」升序排列', async () => {
    const user = userEvent.setup();
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');

    await user.click(screen.getByRole('button', { name: '按输出 token排序' }));

    const rows = screen.getAllByTestId(/^usage-summary-row-/);
    // user1(output 6)在 admin(output 24)之前
    expect(rows[0]).toHaveAttribute('data-testid', 'usage-summary-row-u_user1');
    expect(rows[1]).toHaveAttribute('data-testid', 'usage-summary-row-u_admin');
  });

  it('明细视图逐条展示记录(时间/用户/分享页/会话/token/incomplete 标记)', async () => {
    const user = userEvent.setup();
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');

    await user.click(screen.getByRole('tab', { name: '明细' }));

    // 默认本月:ur_1/2/3 可见,ur_4(上月)不可见;按 created_at 倒序(后端返回序)
    const row3 = await screen.findByTestId('usage-detail-row-ur_3');
    expect(within(row3).getByText('user1')).toBeInTheDocument();
    expect(within(row3).getByText('页面B')).toBeInTheDocument();
    expect(within(row3).getByText('sess-3')).toBeInTheDocument();
    const row2 = screen.getByTestId('usage-detail-row-ur_2');
    expect(within(row2).getByText('估算')).toBeInTheDocument();
    expect(screen.queryByTestId('usage-detail-row-ur_4')).not.toBeInTheDocument();
  });

  it('CSV 导出与当前筛选结果一致(汇总视图)', async () => {
    const user = userEvent.setup();
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');

    // 筛选分享页 = 页面B 后导出
    await user.click(screen.getByRole('combobox', { name: '分享页' }));
    await user.click(screen.getByRole('option', { name: /页面B/ }));
    await user.click(screen.getByRole('button', { name: '导出 CSV' }));

    expect(downloadCsv).toHaveBeenCalledTimes(1);
    const [filename, csv] = vi.mocked(downloadCsv).mock.calls[0];
    expect(filename).toMatch(/^usage-summary-\d{6}\.csv$/);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe('用户,问答次数,输入 token,输出 token,incomplete 次数');
    expect(lines).toHaveLength(2); // 表头 + user1 一行(admin 被筛掉)
    expect(lines[1]).toBe('user1,1,5,6,0');
  });

  it('明细视图 CSV 导出含逐条记录(与当前筛选一致)', async () => {
    const user = userEvent.setup();
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');

    await user.click(screen.getByRole('tab', { name: '明细' }));
    await screen.findByTestId('usage-detail-row-ur_1');
    await user.click(screen.getByRole('button', { name: '导出 CSV' }));

    expect(downloadCsv).toHaveBeenCalledTimes(1);
    const [, csv] = vi.mocked(downloadCsv).mock.calls[0];
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe('时间,用户,分享页,会话 ID,输入 token,输出 token,incomplete');
    // 默认本月 3 条明细(ur_4 上月被筛掉)
    expect(lines).toHaveLength(4);
    expect(csv).toContain('sess-1');
    expect(csv).not.toContain('sess-4');
  });

  it('页面标注口径为 RAGFlow 本地计词、非厂商账单', async () => {
    mockAll();
    renderPage();
    await screen.findByTestId('usage-summary-row-u_admin');
    expect(screen.getByText(/RAGFlow 本地计词/)).toBeInTheDocument();
    expect(screen.getByText(/非厂商账单/)).toBeInTheDocument();
  });
});

describe('aggregateUsageByUser(纯函数)', () => {
  it('按用户聚合计数与 token 求和,incomplete 单独计数', () => {
    const rows = aggregateUsageByUser(USAGE_RESPONSE.usage_records, (id) => `name:${id}`);
    const admin = rows.find((r) => r.portal_user_id === 'u_admin')!;
    expect(admin).toMatchObject({ username: 'name:u_admin', request_count: 2, input_tokens: 13, output_tokens: 24, incomplete_count: 1 });
    const user1 = rows.find((r) => r.portal_user_id === 'u_user1')!;
    expect(user1).toMatchObject({ request_count: 2, input_tokens: 12, output_tokens: 14, incomplete_count: 0 });
  });
});

describe('buildCsv(纯函数)', () => {
  it('转义逗号/引号/换行(RFC 4180)', () => {
    const csv = buildCsv(['a', 'b'], [['x,y', 'he said "hi"'], ['line1\nline2', 3]]);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe('a,b');
    expect(lines[1]).toBe('"x,y","he said ""hi"""');
    expect(lines[2]).toBe('"line1\nline2",3');
  });
});
