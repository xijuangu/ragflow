import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ConfirmDialog from '../src/components/ConfirmDialog';
import InputDialog from '../src/components/InputDialog';

describe('ConfirmDialog', () => {
  it('展示操作后果并支持键盘取消', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();

    render(
      <ConfirmDialog
        open
        title="硬删除用户"
        message="确定删除 user1 吗?"
        confirmText="确认删除"
        variant="danger"
        details={['会级联删除该用户的所有会话', '此操作不可恢复']}
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole('dialog', { name: '硬删除用户' })).toBeInTheDocument();
    expect(screen.getByText('会级联删除该用户的所有会话')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalledOnce();
  });
});

describe('InputDialog', () => {
  it('预填现有值并支持 Enter 提交', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(
      <InputDialog
        open
        title="重命名会话"
        label="会话名称"
        initialValue="会话一"
        confirmText="保存"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );

    const input = screen.getByLabelText('会话名称');
    expect(input).toHaveValue('会话一');
    await user.clear(input);
    await user.type(input, '新标题{Enter}');
    expect(onConfirm).toHaveBeenCalledWith('新标题');
  });
});
