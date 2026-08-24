/**
 * useAdminList 视图能力测试(Issue 86)— 搜索/筛选/三态排序的组合逻辑。
 *
 * 对应 Issue 86 验收点:
 *   - useAdminList 提供 search/filter/sort 通用能力,页面侧只声明列配置
 *   - 搜索对声明文本列做不区分大小写模糊匹配
 *   - 枚举筛选下拉、表头三态排序(升→降→不排)
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  applyAdminListView,
  useAdminListView,
  type AdminListViewConfig,
} from '../src/hooks/useAdminList';

interface Row {
  name: string;
  email: string;
  status: 'active' | 'archived';
  created: number;
}

const ROWS: Row[] = [
  { name: 'beta', email: 'Beta@example.com', status: 'active', created: 200 },
  { name: 'Alpha', email: 'alpha@example.com', status: 'archived', created: 100 },
  { name: 'gamma', email: 'gamma@test.com', status: 'active', created: 300 },
];

const CONFIG: AdminListViewConfig<Row> = {
  searchColumns: [(r) => r.name, (r) => r.email],
  filters: [
    {
      key: 'status',
      label: '状态',
      options: [
        { value: 'active', label: '启用' },
        { value: 'archived', label: '禁用' },
      ],
      match: (r, v) => r.status === v,
    },
  ],
  sortColumns: [
    { key: 'name', label: '名称', compare: (a, b) => a.name.localeCompare(b.name) },
    { key: 'created', label: '创建时间', compare: (a, b) => a.created - b.created },
  ],
};

function names(rows: Row[] | null): string[] {
  return (rows ?? []).map((r) => r.name);
}

describe('applyAdminListView(纯函数)', () => {
  it('items 为 null 时 view 为 null(加载中)', () => {
    expect(
      applyAdminListView(null, CONFIG, { search: '', filterValues: {}, sort: null }),
    ).toBeNull();
  });

  it('无搜索/筛选/排序时保持原始顺序', () => {
    expect(
      names(applyAdminListView(ROWS, CONFIG, { search: '', filterValues: {}, sort: null })),
    ).toEqual(['beta', 'Alpha', 'gamma']);
  });

  it('搜索不区分大小写,命中任一声明文本列', () => {
    // 大写查询命中 name 列
    expect(
      names(
        applyAdminListView(ROWS, CONFIG, { search: 'ALPHA', filterValues: {}, sort: null }),
      ),
    ).toEqual(['Alpha']);
    // 命中 email 列(name 列不含该词)
    expect(
      names(
        applyAdminListView(ROWS, CONFIG, { search: 'test.com', filterValues: {}, sort: null }),
      ),
    ).toEqual(['gamma']);
  });

  it('搜索词 trim;空搜索不过滤', () => {
    expect(
      names(
        applyAdminListView(ROWS, CONFIG, { search: '  beta  ', filterValues: {}, sort: null }),
      ),
    ).toEqual(['beta']);
  });

  it('筛选只保留 match 为真的行;空值(全部)不过滤', () => {
    expect(
      names(
        applyAdminListView(ROWS, CONFIG, {
          search: '',
          filterValues: { status: 'active' },
          sort: null,
        }),
      ),
    ).toEqual(['beta', 'gamma']);
    expect(
      names(
        applyAdminListView(ROWS, CONFIG, {
          search: '',
          filterValues: { status: '' },
          sort: null,
        }),
      ),
    ).toEqual(['beta', 'Alpha', 'gamma']);
  });

  it('排序按 compare 升/降;未知 sort key 忽略', () => {
    const asc = applyAdminListView(ROWS, CONFIG, {
      search: '',
      filterValues: {},
      sort: { key: 'created', direction: 'asc' },
    });
    expect(names(asc)).toEqual(['Alpha', 'beta', 'gamma']);
    const desc = applyAdminListView(ROWS, CONFIG, {
      search: '',
      filterValues: {},
      sort: { key: 'created', direction: 'desc' },
    });
    expect(names(desc)).toEqual(['gamma', 'beta', 'Alpha']);
    const unknown = applyAdminListView(ROWS, CONFIG, {
      search: '',
      filterValues: {},
      sort: { key: 'nope', direction: 'asc' },
    });
    expect(names(unknown)).toEqual(['beta', 'Alpha', 'gamma']);
  });

  it('搜索 + 筛选 + 排序组合', () => {
    const out = applyAdminListView(ROWS, CONFIG, {
      search: 'example.com', // beta + Alpha
      filterValues: { status: 'active' }, // 只剩 beta
      sort: { key: 'name', direction: 'asc' },
    });
    expect(names(out)).toEqual(['beta']);
  });
});

describe('useAdminListView(hook)', () => {
  it('toggleSort 三态循环:升 → 降 → 不排(恢复原始顺序)', () => {
    const { result } = renderHook(() => useAdminListView(ROWS, CONFIG));
    expect(names(result.current.view)).toEqual(['beta', 'Alpha', 'gamma']);

    act(() => result.current.toggleSort('name'));
    expect(result.current.sort).toEqual({ key: 'name', direction: 'asc' });
    expect(names(result.current.view)).toEqual(['Alpha', 'beta', 'gamma']);

    act(() => result.current.toggleSort('name'));
    expect(result.current.sort).toEqual({ key: 'name', direction: 'desc' });
    expect(names(result.current.view)).toEqual(['gamma', 'beta', 'Alpha']);

    act(() => result.current.toggleSort('name'));
    expect(result.current.sort).toBeNull();
    expect(names(result.current.view)).toEqual(['beta', 'Alpha', 'gamma']);
  });

  it('切换排序列时重置为升序', () => {
    const { result } = renderHook(() => useAdminListView(ROWS, CONFIG));
    act(() => result.current.toggleSort('name'));
    act(() => result.current.toggleSort('name')); // desc
    act(() => result.current.toggleSort('created')); // 换列 → asc
    expect(result.current.sort).toEqual({ key: 'created', direction: 'asc' });
    expect(names(result.current.view)).toEqual(['Alpha', 'beta', 'gamma']);
  });

  it('setSearch / setFilterValue 驱动视图', () => {
    const { result } = renderHook(() => useAdminListView(ROWS, CONFIG));
    act(() => result.current.setSearch('A'));
    expect(names(result.current.view)).toEqual(['beta', 'Alpha', 'gamma']); // 都含 a/A
    act(() => result.current.setFilterValue('status', 'archived'));
    expect(names(result.current.view)).toEqual(['Alpha']);
    act(() => result.current.setFilterValue('status', ''));
    expect(names(result.current.view)).toEqual(['beta', 'Alpha', 'gamma']);
  });

  it('未配置视图时 view 等于原列表', () => {
    const { result } = renderHook(() => useAdminListView(ROWS, {}));
    act(() => result.current.setSearch('nothing matches'));
    expect(result.current.view).toEqual(ROWS);
  });
});
