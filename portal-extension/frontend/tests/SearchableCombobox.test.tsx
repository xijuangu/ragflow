/**
 * SearchableCombobox 组件测试(Issue 88)— 自研可搜索 combobox。
 *
 * 覆盖 AC:
 *   - 单选/多选两种模式;输入对 label + sublabel(用户名 + 邮箱)不区分大小写模糊过滤
 *   - 键盘可操作:上下移动、回车选中、Esc 关闭
 *   - 多选以 chips 呈现已选项,chips 可移除
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import SearchableCombobox from '../src/components/SearchableCombobox';
import type { ComboboxOption } from '../src/components/SearchableCombobox';

const OPTIONS: ComboboxOption[] = [
  { value: 'u_admin', label: 'admin', sublabel: 'admin@example.com' },
  { value: 'u_alice', label: 'Alice', sublabel: 'alice@example.com' },
  { value: 'u_bob', label: 'bob', sublabel: 'bob@test.org' },
];

function SingleDemo({
  onChange,
  clearLabel,
  disabled,
}: {
  onChange?: (v: string) => void;
  clearLabel?: string;
  disabled?: boolean;
}) {
  const [value, setValue] = useState('');
  return (
    <SearchableCombobox
      mode="single"
      id="demo-single"
      ariaLabel="选择用户"
      options={OPTIONS}
      value={value}
      onChange={(v) => {
        setValue(v);
        onChange?.(v);
      }}
      placeholder="选择用户…"
      clearLabel={clearLabel}
      disabled={disabled}
    />
  );
}

function MultiDemo({ onChange }: { onChange?: (vs: string[]) => void }) {
  const [values, setValues] = useState<string[]>([]);
  return (
    <SearchableCombobox
      mode="multiple"
      id="demo-multi"
      ariaLabel="选择成员"
      options={OPTIONS}
      values={values}
      onChange={(vs) => {
        setValues(vs);
        onChange?.(vs);
      }}
      placeholder="搜索用户名或邮箱…"
    />
  );
}

describe('SearchableCombobox 单选模式', () => {
  it('输入过滤:对 label 不区分大小写模糊匹配,点击选项触发 onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SingleDemo onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: '选择用户' });
    await user.click(input);
    // 全部选项可见
    expect(screen.getAllByRole('option')).toHaveLength(3);

    await user.type(input, 'ALI');
    const opts = screen.getAllByRole('option');
    expect(opts).toHaveLength(1);
    expect(opts[0]).toHaveTextContent('Alice');

    await user.click(opts[0]);
    expect(onChange).toHaveBeenCalledWith('u_alice');
    // 选中后输入框显示 label,下拉关闭
    expect(input).toHaveValue('Alice');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('输入过滤:sublabel(邮箱)同样参与匹配', async () => {
    const user = userEvent.setup();
    render(<SingleDemo />);

    const input = screen.getByRole('combobox', { name: '选择用户' });
    await user.click(input);
    await user.type(input, 'test.org');
    const opts = screen.getAllByRole('option');
    expect(opts).toHaveLength(1);
    expect(opts[0]).toHaveTextContent('bob');
  });

  it('无匹配时显示空态文案', async () => {
    const user = userEvent.setup();
    render(<SingleDemo />);

    const input = screen.getByRole('combobox', { name: '选择用户' });
    await user.click(input);
    await user.type(input, '不存在的人');
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    expect(screen.getByText('无匹配结果')).toBeInTheDocument();
  });

  it('键盘:ArrowDown/ArrowUp 移动高亮,Enter 选中,Esc 关闭', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SingleDemo onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: '选择用户' });
    await user.click(input);
    expect(input).toHaveAttribute('aria-expanded', 'true');

    await user.keyboard('{ArrowDown}'); // → admin
    await user.keyboard('{ArrowDown}'); // → Alice
    await user.keyboard('{ArrowUp}'); // → admin
    await user.keyboard('{Enter}');
    expect(onChange).toHaveBeenCalledWith('u_admin');
    expect(input).toHaveValue('admin');

    // 重新打开后 Esc 关闭、不改动值
    await user.click(input);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('clearLabel 提供"全部"伪选项,选中后值清空', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SingleDemo onChange={onChange} clearLabel="全部" />);

    const input = screen.getByRole('combobox', { name: '选择用户' });
    await user.click(input);
    await user.click(screen.getByRole('option', { name: /Alice/ }));
    expect(onChange).toHaveBeenLastCalledWith('u_alice');

    await user.click(input);
    await user.click(screen.getByRole('option', { name: '全部' }));
    expect(onChange).toHaveBeenLastCalledWith('');
    expect(input).toHaveValue('');
  });

  it('disabled 时不可交互', async () => {
    const user = userEvent.setup();
    render(<SingleDemo disabled />);
    const input = screen.getByRole('combobox', { name: '选择用户' });
    expect(input).toBeDisabled();
    await user.click(input);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});

describe('SearchableCombobox 多选模式(chips)', () => {
  it('点击选项加入 chips;已选项从下拉中排除', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiDemo onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: '选择成员' });
    await user.click(input);
    await user.click(screen.getByRole('option', { name: /Alice/ }));
    expect(onChange).toHaveBeenLastCalledWith(['u_alice']);

    // chip 呈现
    expect(screen.getByText('Alice')).toBeInTheDocument();
    // 下拉仍打开,且不再含 Alice
    expect(screen.queryByRole('option', { name: /Alice/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(2);

    await user.click(screen.getByRole('option', { name: /bob/ }));
    expect(onChange).toHaveBeenLastCalledWith(['u_alice', 'u_bob']);
  });

  it('chip 移除按钮移除已选项', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiDemo onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: '选择成员' });
    await user.click(input);
    await user.click(screen.getByRole('option', { name: /Alice/ }));
    await user.click(screen.getByRole('option', { name: /bob/ }));

    await user.click(screen.getByRole('button', { name: '移除 Alice' }));
    expect(onChange).toHaveBeenLastCalledWith(['u_bob']);
    // chip 消失(下拉仍打开,Alice 作为选项重新出现)
    const chips = screen.getByRole('list', { name: '已选项' });
    expect(chips).not.toHaveTextContent('Alice');
    expect(screen.getByRole('option', { name: /Alice/ })).toBeInTheDocument();
  });

  it('键盘:Enter 添加高亮项;输入为空时 Backspace 移除最后一个 chip', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiDemo onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: '选择成员' });
    await user.click(input);
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}'); // 添加 admin
    expect(onChange).toHaveBeenLastCalledWith(['u_admin']);

    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}'); // 添加 Alice
    expect(onChange).toHaveBeenLastCalledWith(['u_admin', 'u_alice']);

    // 输入为空时 Backspace 删除最后一个 chip(Alice)
    await user.keyboard('{Backspace}');
    expect(onChange).toHaveBeenLastCalledWith(['u_admin']);
  });

  it('全部选中后下拉显示空态', async () => {
    const user = userEvent.setup();
    render(<MultiDemo />);

    const input = screen.getByRole('combobox', { name: '选择成员' });
    await user.click(input);
    for (const name of ['admin', 'Alice', 'bob']) {
      await user.click(screen.getByRole('option', { name: new RegExp(name) }));
    }
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    expect(screen.getByText('无可选项')).toBeInTheDocument();
  });
});
