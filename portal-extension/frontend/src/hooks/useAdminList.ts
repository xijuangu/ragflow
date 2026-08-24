/**
 * admin 列表页通用的初始加载 hook(TD7:从 6 个 admin 页同构 useEffect 抽取)。
 *
 * 封装 mount 时的 cancelled IIFE + fetch + ApiError catch + data/error state。
 *
 * 两种用法:
 *   - 单列表(初始 null):直接用返回的 data/setData 作为列表状态。
 *       const { data: users, setData: setUsers, error, setError } =
 *         useAdminList(() => api.listAdminUsers().then(r => r.users), { errorMessage: '...' });
 *   - 多列表/非 null 初始值:传 onSuccess 把结果分发给外部 setter,忽略 data。
 *       const { error, setError } = useAdminList(fetcher, {
 *         errorMessage: '...',
 *         onSuccess: (d) => { setGroups(d.groups); setUsers(d.users); },
 *       });
 *
 * Issue 86 起,hook 同时收编全站统一的列表视图能力(§12.1 约定):
 *   - 搜索框:对声明的文本列做不区分大小写模糊匹配;
 *   - 筛选:枚举/状态列(或日期列)按声明的 match 过滤;
 *   - 排序:表头点击三态(升 → 降 → 不排),操作列不排。
 * 页面侧只需在 options.view 声明列配置,不再手写过滤逻辑:
 *   const { view, search, setSearch, filterValues, setFilterValue, sort, toggleSort } =
 *     useAdminList(fetcher, {
 *       errorMessage: '...',
 *       view: { searchColumns: [...], filters: [...], sortColumns: [...] },
 *     });
 * 列表状态在 hook 外管理的页面(如按分享页切换的授权列表)可单独使用 useAdminListView。
 */
import { useCallback, useEffect, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { ApiError } from '../api/client';

/** 数组元素类型提取(view 配置的 item 类型)。 */
type ItemOf<T> = T extends readonly (infer I)[] ? I : never;

export type AdminListSortDirection = 'asc' | 'desc';

export interface AdminListSortState {
  key: string;
  direction: AdminListSortDirection;
}

export interface AdminListFilterOption {
  value: string;
  label: string;
}

export interface AdminListFilterDef<T> {
  /** 稳定 key,标识该筛选。 */
  key: string;
  label: string;
  /** 控件类型:枚举下拉(默认)或日期输入(时间范围筛选,值为 YYYY-MM-DD)。 */
  control?: 'select' | 'date';
  /** control=select 时的选项;空值(全部)由工具栏统一提供,不需在此声明。 */
  options?: AdminListFilterOption[];
  /** 筛选值非空时 item 是否保留;空值视为不筛选。 */
  match: (item: T, value: string) => boolean;
}

export interface AdminListSortDef<T> {
  key: string;
  label: string;
  compare: (a: T, b: T) => number;
}

export interface AdminListViewConfig<T> {
  /** 搜索命中的文本列(不区分大小写包含匹配,任一列命中即保留)。 */
  searchColumns?: ((item: T) => string | null | undefined)[];
  filters?: AdminListFilterDef<T>[];
  sortColumns?: AdminListSortDef<T>[];
}

export interface AdminListViewState {
  search: string;
  /** 当前筛选值:key → value;空串/缺省表示全部。 */
  filterValues: Record<string, string>;
  /** 当前排序;null 表示不排(恢复原始顺序)。 */
  sort: AdminListSortState | null;
}

export interface UseAdminListViewResult<T> extends AdminListViewState {
  setSearch: (value: string) => void;
  setFilterValue: (key: string, value: string) => void;
  /** 表头点击:升 → 降 → 不排。 */
  toggleSort: (key: string) => void;
  /** 应用搜索+筛选+排序后的列表(null 表示源列表尚未加载)。 */
  view: T[] | null;
}

/** 搜索/筛选/排序的纯函数实现(hook 与测试共用)。 */
export function applyAdminListView<T>(
  items: T[] | null,
  config: AdminListViewConfig<T>,
  state: AdminListViewState,
): T[] | null {
  if (items === null) return null;
  let out = items;
  const q = state.search.trim().toLowerCase();
  if (q && config.searchColumns && config.searchColumns.length > 0) {
    out = out.filter((item) =>
      config.searchColumns!.some((col) => (col(item) ?? '').toLowerCase().includes(q)),
    );
  }
  for (const f of config.filters ?? []) {
    const v = state.filterValues[f.key];
    if (v) out = out.filter((item) => f.match(item, v));
  }
  if (state.sort) {
    const def = config.sortColumns?.find((s) => s.key === state.sort!.key);
    if (def) {
      const dir = state.sort.direction === 'asc' ? 1 : -1;
      out = [...out].sort((a, b) => def.compare(a, b) * dir);
    }
  }
  return out;
}

/**
 * 列表视图 hook:对调用方管理的数组应用搜索/筛选/排序。
 * useAdminList 内部即用它实现 options.view;列表状态在 useAdminList 之外的页面直接用本 hook。
 */
export function useAdminListView<T>(
  items: T[] | null,
  config: AdminListViewConfig<T>,
): UseAdminListViewResult<T> {
  const [search, setSearch] = useState('');
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<AdminListSortState | null>(null);

  const setFilterValue = useCallback((key: string, value: string) => {
    setFilterValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  const toggleSort = useCallback((key: string) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, direction: 'asc' };
      if (prev.direction === 'asc') return { key, direction: 'desc' };
      return null;
    });
  }, []);

  // 列表量级为几十级(§12.1),每次渲染直接计算,不做 memo(config 每渲染重建,memo 无意义)
  const view = applyAdminListView(items, config, { search, filterValues, sort });

  return { search, setSearch, filterValues, setFilterValue, sort, toggleSort, view };
}

export interface UseAdminListOptions<T> {
  /** 拉取失败且非 ApiError 时的兜底错误文案。 */
  errorMessage: string;
  /** 拉取成功后把结果分发给外部 setter(并行加载多列表的场景)。 */
  onSuccess?: (data: T) => void;
  /** 列表视图配置(搜索/筛选/排序);仅当 T 为数组类型时使用。 */
  view?: AdminListViewConfig<ItemOf<T>>;
}

export type UseAdminListResult<T> = {
  /** 拉取结果(null 表示尚未加载)。 */
  data: T | null;
  setData: Dispatch<SetStateAction<T | null>>;
  error: string | null;
  setError: Dispatch<SetStateAction<string | null>>;
} & UseAdminListViewResult<ItemOf<T>>;

export function useAdminList<T>(
  fetcher: () => Promise<T>,
  options: UseAdminListOptions<T>,
): UseAdminListResult<T> {
  const { errorMessage, onSuccess } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const viewState = useAdminListView<ItemOf<T>>(
    data as unknown as ItemOf<T>[] | null,
    (options.view ?? {}) as AdminListViewConfig<ItemOf<T>>,
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetcher();
        if (cancelled) return;
        setData(res);
        onSuccess?.(res);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : errorMessage);
      }
    })();
    return () => {
      cancelled = true;
    };
    // 仅在 mount 时拉取一次;fetcher/onSuccess/errorMessage 为配置,不应触发重新拉取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { data, setData, error, setError, ...viewState };
}
