/**
 * 用量统计页(Issue 92)— 汇总(按用户聚合,默认本月)+ 明细两视图,CSV 导出。
 *
 * 对应后端:
 *   - GET /admin/usage-records(全量,limit=10000;筛选/排序/聚合全在浏览器内存完成,
 *     遵循 Issue 86 全站列表交互约定 §12.1;量级增长后再评估服务端分页)
 *   - GET /admin/users(用户名解析)+ GET /admin/share-pages(分享页筛选选项)
 *
 * 口径(CONTEXT.md §12.2,页面须明确标注):
 *   token 数值为 RAGFlow 本地计词(message_fit_in / num_tokens_from_string),非厂商账单;
 *   incomplete=true 的记录为断流/末帧缺 usage 时的估算兜底(estimate_tokens 口径)。
 *   不展示、不导出任何问答正文(usage_record 表本身不存正文)。
 */
import { useMemo, useState } from 'react';
import {
  api,
  type AdminUsageRecord,
} from '../../api/client';
import { firstDayOfCurrentMonth, formatTime, parseDayEnd, parseDayStart } from '../../utils/formatTime';
import { buildCsv, downloadCsv } from '../../utils/csv';
import { useAdminList, useAdminListView } from '../../hooks/useAdminList';
import type { AdminListViewConfig } from '../../hooks/useAdminList';
import { MobileCard, MobileCardList } from '../../components/MobileCards';
import { AdminListToolbar, SortableTh } from '../../components/AdminListControls';
import { aggregateUsageByUser } from './usageAggregation';
import type { UsageSummaryRow } from './usageAggregation';

type ViewMode = 'summary' | 'detail';

export default function UsageStatsAdminPage() {
  const [viewMode, setViewMode] = useState<ViewMode>('summary');

  // Issue 86/87 约定:全量加载,筛选/排序/聚合在内存完成
  const { data, error } = useAdminList(
    async () => {
      const [usersRes, pagesRes, usageRes] = await Promise.all([
        api.listAdminUsers(),
        api.listAdminSharePages(),
        api.listAdminUsageRecords({ limit: 10000 }),
      ]);
      return { users: usersRes.users, sharePages: pagesRes.share_pages, records: usageRes.usage_records };
    },
    { errorMessage: '加载用量数据失败' },
  );
  const users = useMemo(() => data?.users ?? [], [data]);
  const sharePages = useMemo(() => data?.sharePages ?? [], [data]);
  const records = data?.records ?? null;

  const userMap = useMemo(() => {
    const m = new Map<string, (typeof users)[number]>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);
  const sharePageMap = useMemo(() => {
    const m = new Map<string, (typeof sharePages)[number]>();
    for (const p of sharePages) m.set(p.id, p);
    return m;
  }, [sharePages]);

  const resolveUser = (portalUserId: string) =>
    userMap.get(portalUserId)?.username
    ?? (portalUserId === 'u_anonymous' ? '匿名用户(公开分享)' : portalUserId);
  const resolveSharePage = (sharePageId: string) =>
    sharePageMap.get(sharePageId)?.name ?? sharePageId;

  // 记录级视图(明细直接用;汇总在其过滤结果上聚合)。
  // 默认本月:since 初始值为当月 1 号(清空即查看全部时间)。
  const recordsViewConfig: AdminListViewConfig<AdminUsageRecord> = {
    searchColumns: [
      (r) => resolveUser(r.portal_user_id),
      (r) => resolveSharePage(r.share_page_id),
      (r) => r.session_id,
    ],
    filters: [
      {
        key: 'share_page',
        label: '分享页',
        control: 'combobox',
        options: sharePages.map((p) => ({ value: p.id, label: p.name, sublabel: p.ragflow_resource_id })),
        match: (r, v) => r.share_page_id === v,
      },
      {
        key: 'since',
        label: '起始日期',
        control: 'date',
        match: (r, v) => {
          const since = parseDayStart(v);
          return since === null || r.created_at >= since;
        },
      },
      {
        key: 'until',
        label: '结束日期',
        control: 'date',
        match: (r, v) => {
          const until = parseDayEnd(v);
          return until === null || r.created_at <= until;
        },
      },
    ],
    sortColumns: [
      { key: 'created_at', label: '时间', compare: (a, b) => a.created_at - b.created_at },
      { key: 'input_tokens', label: '输入 token', compare: (a, b) => a.input_tokens - b.input_tokens },
      { key: 'output_tokens', label: '输出 token', compare: (a, b) => a.output_tokens - b.output_tokens },
    ],
  };
  const {
    view: visibleRecords,
    search,
    setSearch,
    filterValues,
    setFilterValue,
    sort: recordSort,
    toggleSort: toggleRecordSort,
  } = useAdminListView(records, recordsViewConfig, { since: firstDayOfCurrentMonth() });

  // 汇总视图:在过滤后的记录上按用户聚合,列排序用独立 sort 状态
  const summaryRows = useMemo(
    () => (visibleRecords === null ? null : aggregateUsageByUser(visibleRecords, resolveUser)),
    // resolveUser 依赖 userMap,userMap 变化时重算;useMemo 依赖用可见记录 + userMap
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visibleRecords, userMap],
  );
  const summaryViewConfig: AdminListViewConfig<UsageSummaryRow> = {
    sortColumns: [
      { key: 'username', label: '用户', compare: (a, b) => a.username.localeCompare(b.username) },
      { key: 'request_count', label: '问答次数', compare: (a, b) => a.request_count - b.request_count },
      { key: 'input_tokens', label: '输入 token', compare: (a, b) => a.input_tokens - b.input_tokens },
      { key: 'output_tokens', label: '输出 token', compare: (a, b) => a.output_tokens - b.output_tokens },
      { key: 'incomplete_count', label: 'incomplete 次数', compare: (a, b) => a.incomplete_count - b.incomplete_count },
    ],
  };
  const { view: visibleSummary, sort: summarySort, toggleSort: toggleSummarySort } =
    useAdminListView(summaryRows, summaryViewConfig);

  /** 导出当前视图可见行(与筛选/排序结果一致,验收点 2)。 */
  const exportCurrentView = () => {
    const stamp = firstDayOfCurrentMonth().replace(/-/g, '').slice(0, 6);
    if (viewMode === 'summary') {
      const rows = (visibleSummary ?? []).map((r) => [
        r.username, r.request_count, r.input_tokens, r.output_tokens, r.incomplete_count,
      ]);
      downloadCsv(
        `usage-summary-${stamp}.csv`,
        buildCsv(['用户', '问答次数', '输入 token', '输出 token', 'incomplete 次数'], rows),
      );
    } else {
      const rows = (visibleRecords ?? []).map((r) => [
        formatTime(r.created_at, 'seconds'),
        resolveUser(r.portal_user_id),
        resolveSharePage(r.share_page_id),
        r.session_id,
        r.input_tokens,
        r.output_tokens,
        r.incomplete ? '是' : '否',
      ]);
      downloadCsv(
        `usage-detail-${stamp}.csv`,
        buildCsv(['时间', '用户', '分享页', '会话 ID', '输入 token', '输出 token', 'incomplete'], rows),
      );
    }
  };

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 用量统计</div>
          <h1>用量统计</h1>
          <div className="sub">
            按用户/分享页统计问答 token 用量。口径:RAGFlow 本地计词
            (message_fit_in / num_tokens_from_string),非厂商账单;
            标记 incomplete 的记录为断流或缺 usage 字段时的估算兜底。
          </div>
        </div>
        <button type="button" className="btn btn-secondary" onClick={exportCurrentView}>
          导出 CSV
        </button>
      </div>

      {error && <div className="alert-error">{error}</div>}

      <div className="view-toggle" role="tablist" aria-label="用量统计视图">
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'summary'}
          className={viewMode === 'summary' ? 'active' : ''}
          onClick={() => setViewMode('summary')}
        >
          汇总(按用户)
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'detail'}
          className={viewMode === 'detail' ? 'active' : ''}
          onClick={() => setViewMode('detail')}
        >
          明细
        </button>
      </div>

      <div className="card card-table">
        <AdminListToolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel="搜索用户/分享页/会话"
          searchPlaceholder="按用户名、分享页名或会话 ID 搜索"
          filters={recordsViewConfig.filters}
          filterValues={filterValues}
          onFilterChange={setFilterValue}
        />
        <div className="card-body">
          {records === null && !error && <div className="loading">加载中…</div>}
          {records !== null && records.length === 0 && (
            <div className="empty-state">暂无用量的记录</div>
          )}
          {records !== null && records.length > 0
            && visibleRecords !== null && visibleRecords.length === 0 && (
            <div className="empty-state">无匹配结果</div>
          )}

          {viewMode === 'summary' && visibleSummary !== null && visibleSummary.length > 0 && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <SortableTh columnKey="username" label="用户" sort={summarySort} onToggle={toggleSummarySort} />
                      <SortableTh columnKey="request_count" label="问答次数" sort={summarySort} onToggle={toggleSummarySort} />
                      <SortableTh columnKey="input_tokens" label="输入 token" sort={summarySort} onToggle={toggleSummarySort} />
                      <SortableTh columnKey="output_tokens" label="输出 token" sort={summarySort} onToggle={toggleSummarySort} />
                      <SortableTh columnKey="incomplete_count" label="incomplete 次数" sort={summarySort} onToggle={toggleSummarySort} />
                    </tr>
                  </thead>
                  <tbody>
                    {visibleSummary.map((row) => (
                      <tr key={row.portal_user_id} data-testid={`usage-summary-row-${row.portal_user_id}`}>
                        <td>{row.username}</td>
                        <td className="mono">{row.request_count}</td>
                        <td className="mono">{row.input_tokens}</td>
                        <td className="mono">{row.output_tokens}</td>
                        <td className="mono">{row.incomplete_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <MobileCardList label="用量汇总列表">
                {visibleSummary.map((row) => (
                  <MobileCard
                    key={row.portal_user_id}
                    title={row.username}
                    testId={`mobile-usage-summary-${row.portal_user_id}`}
                    fields={[
                      { label: '问答次数', value: row.request_count },
                      { label: '输入 token', value: row.input_tokens },
                      { label: '输出 token', value: row.output_tokens },
                      { label: 'incomplete 次数', value: row.incomplete_count },
                    ]}
                  />
                ))}
              </MobileCardList>
            </>
          )}

          {viewMode === 'detail' && visibleRecords !== null && visibleRecords.length > 0 && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <SortableTh columnKey="created_at" label="时间" sort={recordSort} onToggle={toggleRecordSort} />
                      <th>用户</th>
                      <th>分享页</th>
                      <th>会话</th>
                      <SortableTh columnKey="input_tokens" label="输入 token" sort={recordSort} onToggle={toggleRecordSort} />
                      <SortableTh columnKey="output_tokens" label="输出 token" sort={recordSort} onToggle={toggleRecordSort} />
                      <th>incomplete</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRecords.map((r) => (
                      <tr key={r.id} data-testid={`usage-detail-row-${r.id}`}>
                        <td className="mono">{formatTime(r.created_at, 'seconds')}</td>
                        <td>{resolveUser(r.portal_user_id)}</td>
                        <td>{resolveSharePage(r.share_page_id)}</td>
                        <td className="mono">{r.session_id || '—'}</td>
                        <td className="mono">{r.input_tokens}</td>
                        <td className="mono">{r.output_tokens}</td>
                        <td>{r.incomplete ? <span className="badge badge-info">估算</span> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <MobileCardList label="用量明细列表">
                {visibleRecords.map((r) => (
                  <MobileCard
                    key={r.id}
                    title={resolveUser(r.portal_user_id)}
                    testId={`mobile-usage-detail-${r.id}`}
                    fields={[
                      { label: '时间', value: <span className="mono">{formatTime(r.created_at, 'seconds')}</span> },
                      { label: '分享页', value: resolveSharePage(r.share_page_id) },
                      { label: '会话', value: <span className="mono">{r.session_id || '—'}</span> },
                      { label: '输入 / 输出 token', value: `${r.input_tokens} / ${r.output_tokens}` },
                      { label: 'incomplete', value: r.incomplete ? '估算' : '—' },
                    ]}
                  />
                ))}
              </MobileCardList>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
