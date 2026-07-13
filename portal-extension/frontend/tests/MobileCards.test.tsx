import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MobileCard, MobileCardList } from '../src/components/MobileCards';

describe('MobileCards', () => {
  it('用语义化字段和操作区呈现桌面表格同一份信息', () => {
    render(
      <MobileCardList label="用户列表">
        <MobileCard
          title="user1"
          badge={<span>启用</span>}
          fields={[
            { label: '邮箱', value: 'user1@example.com' },
            { label: '角色', value: '普通用户' },
          ]}
          actions={<button type="button">禁用</button>}
        />
      </MobileCardList>,
    );

    expect(screen.getByRole('list', { name: '用户列表' })).toBeInTheDocument();
    expect(screen.getByText('邮箱')).toBeInTheDocument();
    expect(screen.getByText('user1@example.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '禁用' })).toBeInTheDocument();
  });
});
