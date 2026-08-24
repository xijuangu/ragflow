/**
 * 自研可搜索 combobox(Issue 88,§12.1)— 不引组件库,视觉沿用 D1 Graphite token。
 *
 * 两种模式:
 *   - single:单选搜索(授权对象 / 分享页 / 长列表筛选场景)。选中后输入框显示 label;
 *     可选 clearLabel(如"全部")在下拉顶部提供清空伪选项。
 *   - multiple:多选 + chips(添加成员场景)。已选项从下拉排除,以 chips 呈现,
 *     每个 chip 带移除按钮;输入为空时 Backspace 移除最后一个 chip。
 *
 * 过滤:输入即对 label + sublabel(用户名 + 邮箱)做不区分大小写包含匹配。
 * 键盘:ArrowDown/ArrowUp 移动高亮,Enter 选中高亮项,Esc 关闭下拉。
 * ARIA:input role=combobox(aria-expanded/controls/activedescendant),
 *       下拉 ul role=listbox(multiple 时 aria-multiselectable),li role=option。
 */
import { useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from 'react';

export interface ComboboxOption {
  value: string;
  label: string;
  /** 副文本(如邮箱),参与过滤并显示在 label 之后。 */
  sublabel?: string;
}

interface CommonProps {
  options: ComboboxOption[];
  /** input 的 id(供外部 <label htmlFor> 关联)。 */
  id?: string;
  /** 无可见 label 时的可访问名。 */
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  /** 下拉无匹配/无可选项时的文案。 */
  emptyText?: string;
}

export interface SingleComboboxProps extends CommonProps {
  mode: 'single';
  /** 当前值;'' 表示未选。 */
  value: string;
  onChange: (value: string) => void;
  /** 提供时下拉顶部出现该伪选项(如"全部"),选中后值清空为 ''。 */
  clearLabel?: string;
}

export interface MultiComboboxProps extends CommonProps {
  mode: 'multiple';
  values: string[];
  onChange: (values: string[]) => void;
}

export type SearchableComboboxProps = SingleComboboxProps | MultiComboboxProps;

export default function SearchableCombobox(props: SearchableComboboxProps) {
  const {
    options,
    id,
    ariaLabel,
    placeholder,
    disabled = false,
    emptyText,
  } = props;
  const isMulti = props.mode === 'multiple';
  const selectedValues = useMemo(
    () => (isMulti ? props.values : props.value ? [props.value] : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mode 决定取 values 还是 value
    [isMulti, props.mode === 'multiple' ? props.values : props.value],
  );
  const clearLabel = props.mode === 'single' ? props.clearLabel : undefined;

  const autoId = useId();
  const inputId = id ?? `${autoId}-input`;
  const listboxId = `${autoId}-listbox`;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  // -1 = 无高亮;打开后首次 ArrowDown 落在第一项
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  const optionMap = useMemo(() => {
    const m = new Map<string, ComboboxOption>();
    for (const o of options) m.set(o.value, o);
    return m;
  }, [options]);

  // 过滤:多选排除已选;再按 label+sublabel 不区分大小写包含匹配
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return options.filter((o) => {
      if (isMulti && selectedValues.includes(o.value)) return false;
      if (!q) return true;
      return (
        o.label.toLowerCase().includes(q) ||
        (o.sublabel ?? '').toLowerCase().includes(q)
      );
    });
  }, [options, isMulti, selectedValues, query]);

  // 单选 + clearLabel:顶部"全部"伪选项(value='')
  const items: ComboboxOption[] = useMemo(
    () => (clearLabel ? [{ value: '', label: clearLabel }, ...filtered] : filtered),
    [clearLabel, filtered],
  );

  const clampedActive =
    items.length === 0 || activeIndex < 0 ? -1 : Math.min(activeIndex, items.length - 1);

  const close = () => {
    setOpen(false);
    setQuery('');
    setActiveIndex(-1);
  };

  const openDropdown = () => {
    decideDirection();
    setOpen(true);
    setQuery('');
    setActiveIndex(-1);
  };

  const commitValue = (value: string) => {
    if (isMulti) {
      if (value && !props.values.includes(value)) props.onChange([...props.values, value]);
      // 多选:保持打开继续选
      setQuery('');
      setActiveIndex(-1);
    } else {
      props.onChange(value);
      close();
    }
  };

  const removeValue = (value: string) => {
    if (isMulti) props.onChange(props.values.filter((v) => v !== value));
  };

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) {
        openDropdown();
        return;
      }
      if (items.length === 0) return;
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((prev) => {
        if (prev < 0) return delta === 1 ? 0 : items.length - 1;
        const cur = Math.min(prev, items.length - 1);
        return (cur + delta + items.length) % items.length;
      });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (open && clampedActive >= 0) commitValue(items[clampedActive].value);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'Backspace' && isMulti && query === '' && props.values.length > 0) {
      removeValue(props.values[props.values.length - 1]);
    }
  };

  // mousedown 抢在 input blur 之前提交,避免下拉先关闭导致点击丢失
  const handleOptionMouseDown = (e: ReactMouseEvent, value: string) => {
    e.preventDefault();
    commitValue(value);
    inputRef.current?.focus();
  };

  // 2026-08-24:向下空间不足时向上翻转。下拉是绝对定位,不参与滚动祖先的
  // 滚动高度计算 — admin 各页的 section 滚动容器(Slice 51,overflow-y:auto)
  // 高度只到视口,下拉超出其底边的部分会被静默裁掉(空态/短表场景必然超出;
  // 即使当前未滚动,overflow 裁切同样生效)。打开时测 input 距最近的
  // overflow 裁切祖先(含 overflow:hidden,如 .main)底边的剩余空间,不足则向上弹。
  const [dropUp, setDropUp] = useState(false);
  const decideDirection = () => {
    const input = inputRef.current;
    if (!input) return;
    const inputBox = input.getBoundingClientRect();
    // 最近的 overflow 裁切祖先:auto/scroll/hidden 都会裁绝对定位溢出
    let ancestor: HTMLElement | null = input.parentElement;
    let bottomEdge = window.innerHeight;
    while (ancestor && ancestor !== document.body) {
      const { overflow, overflowY } = getComputedStyle(ancestor);
      const clips = [overflow, overflowY].some(v => v === 'auto' || v === 'scroll' || v === 'hidden');
      if (clips) {
        bottomEdge = ancestor.getBoundingClientRect().bottom;
        break;
      }
      ancestor = ancestor.parentElement;
    }
    const spaceBelow = bottomEdge - inputBox.bottom;
    // 向上需要容纳下拉(240px)+ 间距;不足则翻转
    setDropUp(spaceBelow < 250);
  };

  // 单选关闭时输入框显示选中项 label
  const selectedLabel =
    !isMulti && props.value ? optionMap.get(props.value)?.label ?? props.value : '';
  const inputValue = open ? query : isMulti ? query : selectedLabel;

  const resolvedEmptyText =
    emptyText ?? (query.trim() ? '无匹配结果' : '无可选项');

  return (
    <div className={`combobox${isMulti ? ' combobox-multi' : ''}`}>
      {isMulti && props.values.length > 0 && (
        <div className="combobox-chips" role="list" aria-label="已选项">
          {props.values.map((v) => {
            const opt = optionMap.get(v);
            const text = opt?.label ?? v;
            return (
              <span key={v} className="combobox-chip" role="listitem">
                <span className="combobox-chip-label">{text}</span>
                <button
                  type="button"
                  className="combobox-chip-remove"
                  aria-label={`移除 ${text}`}
                  // preventDefault 避免点击 chip 时 input blur 关闭下拉
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => removeValue(v)}
                  disabled={disabled}
                >
                  <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true" focusable="false">
                    <path d="M1 1l6 6M7 1L1 7" stroke="currentColor" strokeWidth="1.5" />
                  </svg>
                </button>
              </span>
            );
          })}
        </div>
      )}
      <input
        ref={inputRef}
        id={inputId}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={
          open && clampedActive >= 0 ? `${listboxId}-opt-${clampedActive}` : undefined
        }
        aria-autocomplete="list"
        autoComplete="off"
        value={inputValue}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => {
          setQuery(e.target.value);
          setActiveIndex(-1);
          if (!open) openDropdown();
        }}
        onFocus={() => {
          if (!disabled) openDropdown();
        }}
        // input 已聚焦时再次点击不会触发 focus,需 click 兜底重新打开
        onClick={() => {
          if (!disabled && !open) openDropdown();
        }}
        onBlur={close}
        onKeyDown={handleKeyDown}
      />
      {open && (
        <ul
          className={`combobox-dropdown${dropUp ? ' drop-up' : ''}`}
          role="listbox"
          id={listboxId}
          aria-multiselectable={isMulti || undefined}
        >
          {items.length === 0 && <li className="combobox-empty">{resolvedEmptyText}</li>}
          {items.map((o, i) => (
            <li
              key={o.value || '__clear__'}
              id={`${listboxId}-opt-${i}`}
              role="option"
              aria-selected={selectedValues.includes(o.value)}
              className={`combobox-option${i === clampedActive ? ' active' : ''}`}
              onMouseDown={(e) => handleOptionMouseDown(e, o.value)}
              onMouseEnter={() => setActiveIndex(i)}
            >
              <span className="combobox-option-label">{o.label}</span>
              {o.sublabel && <span className="combobox-option-sub">{o.sublabel}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
