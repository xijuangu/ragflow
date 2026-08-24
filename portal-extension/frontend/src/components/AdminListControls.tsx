/**
 * admin 列表页统一的工具栏与排序表头(Issue 86,§12.1 全站列表交互约定)。
 *
 * - AdminListToolbar:搜索框 + 声明式筛选控件(枚举下拉 / 日期输入),值由 useAdminList(View) 驱动。
 * - SortableTh:可排序表头,点击三态(升 → 降 → 不排),箭头为内联 SVG(不引 icon 库)。
 *
 * 视觉沿用 D1 Graphite token(.filters / .form-field / var(--accent) 等),不引第三方组件库。
 */
import { useId } from 'react';
import type {
  AdminListFilterDef,
  AdminListSortState,
} from '../hooks/useAdminList';
import SearchableCombobox from './SearchableCombobox';

export interface AdminListToolbarProps<T> {
  search: string;
  onSearchChange: (value: string) => void;
  /** 搜索框可访问名(同时是可见 label)。 */
  searchLabel: string;
  searchPlaceholder?: string;
  filters?: AdminListFilterDef<T>[];
  filterValues?: Record<string, string>;
  onFilterChange?: (key: string, value: string) => void;
}

export function AdminListToolbar<T>({
  search,
  onSearchChange,
  searchLabel,
  searchPlaceholder,
  filters = [],
  filterValues = {},
  onFilterChange,
}: AdminListToolbarProps<T>) {
  const baseId = useId();
  return (
    <div className="filters admin-list-toolbar">
      <div className="form-field">
        <label htmlFor={`${baseId}-search`}>{searchLabel}</label>
        <input
          id={`${baseId}-search`}
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={searchPlaceholder}
        />
      </div>
      {filters.map((f) => (
        <div className="form-field" key={f.key}>
          <label htmlFor={`${baseId}-filter-${f.key}`}>{f.label}</label>
          {f.control === 'date' ? (
            <input
              id={`${baseId}-filter-${f.key}`}
              type="date"
              value={filterValues[f.key] ?? ''}
              onChange={(e) => onFilterChange?.(f.key, e.target.value)}
            />
          ) : f.control === 'combobox' ? (
            /* Issue 88:长列表筛选(如会话页用户/分享页)用可搜索 combobox,"全部"为清空伪选项 */
            <SearchableCombobox
              mode="single"
              id={`${baseId}-filter-${f.key}`}
              ariaLabel={f.label}
              options={f.options ?? []}
              value={filterValues[f.key] ?? ''}
              onChange={(v) => onFilterChange?.(f.key, v)}
              clearLabel="全部"
              placeholder="全部"
            />
          ) : (
            <select
              id={`${baseId}-filter-${f.key}`}
              value={filterValues[f.key] ?? ''}
              onChange={(e) => onFilterChange?.(f.key, e.target.value)}
            >
              <option value="">全部</option>
              {(f.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          )}
        </div>
      ))}
    </div>
  );
}

export interface SortableThProps {
  columnKey: string;
  label: string;
  sort: AdminListSortState | null;
  onToggle: (key: string) => void;
}

/** 可排序表头:点击循环 升 → 降 → 不排;非本列排序激活时本列回到未激活态。 */
export function SortableTh({ columnKey, label, sort, onToggle }: SortableThProps) {
  const active = sort?.key === columnKey;
  const direction = active ? sort.direction : null;
  return (
    <th
      aria-sort={
        direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : undefined
      }
    >
      <button
        type="button"
        className={`th-sort${active ? ' active' : ''}`}
        onClick={() => onToggle(columnKey)}
        aria-label={`按${label}排序`}
      >
        {label}
        <svg width="8" height="10" viewBox="0 0 8 10" aria-hidden="true" focusable="false">
          {direction === 'desc' ? (
            <path d="M4 10 0 4h8L4 10Z" fill="currentColor" />
          ) : (
            <path d="M4 0l4 6H0L4 0Z" fill="currentColor" />
          )}
        </svg>
      </button>
    </th>
  );
}
