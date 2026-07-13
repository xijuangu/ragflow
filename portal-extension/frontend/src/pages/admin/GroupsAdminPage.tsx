/**
 * 用户组管理页(Slice 11,验收点 3)— 列表 / 创建 / 添加成员 / 移除成员。
 *
 * 对应后端:
 *   - GET /admin/groups(列表,含 members: user_id[])
 *   - POST /admin/groups(创建,201)
 *   - POST /admin/groups/:id/members(添加成员)
 *   - DELETE /admin/groups/:id/members/:user_id(移除成员)
 *
 * 额外调 GET /admin/users 构建 user_id → username 映射,用于:
 *   1. 成员列表显示用户名(而非裸 ID)。
 *   2. 添加成员下拉选项。
 */
import { useCallback, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminGroup, type AdminUser } from '../../api/client';
import { useAdminList } from '../../hooks/useAdminList';
import { useOptimisticToggle } from '../../hooks/useOptimisticToggle';

export default function GroupsAdminPage() {
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const { error, setError } = useAdminList(
    async () => {
      const [groupsRes, usersRes] = await Promise.all([
        api.listAdminGroups(),
        api.listAdminUsers(),
      ]);
      return { groups: groupsRes.groups, users: usersRes.users };
    },
    {
      errorMessage: '加载用户组失败',
      onSuccess: (d) => {
        setGroups(d.groups);
        setUsers(d.users);
      },
    },
  );
  const { busyId, run } = useOptimisticToggle({ onError: setError });

  const [groupName, setGroupName] = useState('');
  const [creating, setCreating] = useState(false);

  // user_id → username 映射(成员显示用)
  const userMap = useMemo(() => {
    const m = new Map<string, AdminUser>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);

  const resolveName = useCallback(
    (userId: string) => userMap.get(userId)?.username ?? userId,
    [userMap],
  );

  const handleCreate = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (creating) return;
      const name = groupName.trim();
      if (!name) return;
      setCreating(true);
      try {
        const created = await api.createAdminGroup(name);
        // Slice 43:兜底 — 后端可能漏 member_count/members 字段,加默认值防御
        // 避免渲染时 g.members.length 抛 TypeError 导致整个 admin SPA 崩溃
        const safeCreated: AdminGroup = {
          ...created,
          member_count: created.member_count ?? 0,
          members: created.members ?? [],
        };
        setGroups((prev) => (prev ? [...prev, safeCreated] : [safeCreated]));
        setGroupName('');
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '创建用户组失败');
      } finally {
        setCreating(false);
      }
    },
    [creating, groupName, setGroups, setError],
  );

  const handleAddMember = useCallback(
    async (groupId: string, userId: string) => {
      if (!userId) return;
      await run({
        id: `${groupId}:add:${userId}`,
        optimistic: () =>
          setGroups((prev) =>
            prev
              ? prev.map((g) =>
                  g.id === groupId && !g.members.includes(userId)
                    ? { ...g, members: [...g.members, userId], member_count: g.member_count + 1 }
                    : g,
                )
              : prev,
          ),
        rollback: () =>
          setGroups((prev) =>
            prev
              ? prev.map((g) =>
                  g.id === groupId
                    ? {
                        ...g,
                        members: g.members.filter((id) => id !== userId),
                        member_count: Math.max(0, g.member_count - 1),
                      }
                    : g,
                )
              : prev,
          ),
        action: () => api.addAdminGroupMember(groupId, userId),
        errorMessage: '添加成员失败',
      });
    },
    [run, setGroups],
  );

  const handleRemoveMember = useCallback(
    async (groupId: string, userId: string) => {
      await run({
        id: `${groupId}:remove:${userId}`,
        optimistic: () =>
          setGroups((prev) =>
            prev
              ? prev.map((g) =>
                  g.id === groupId
                    ? {
                        ...g,
                        members: g.members.filter((id) => id !== userId),
                        member_count: Math.max(0, g.member_count - 1),
                      }
                    : g,
                )
              : prev,
          ),
        rollback: () =>
          setGroups((prev) =>
            prev
              ? prev.map((g) =>
                  g.id === groupId && !g.members.includes(userId)
                    ? { ...g, members: [...g.members, userId], member_count: g.member_count + 1 }
                    : g,
                )
              : prev,
          ),
        action: () => api.removeAdminGroupMember(groupId, userId),
        errorMessage: '移除成员失败',
      });
    },
    [run, setGroups],
  );

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 用户组</div>
          <h1>用户组管理</h1>
          <div className="sub">按业务线组织用户,每个组可单独管理成员。用户可同时属于多个组。</div>
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {/* 创建用户组表单(内联,无 drawer) */}
      <form className="admin-form card" onSubmit={handleCreate} aria-label="创建用户组表单">
        <h3 className="form-title">创建用户组</h3>
        <div className="admin-form-row admin-form-row-compact">
          <div className="form-field">
            <label htmlFor="new-group-name">用户组名称</label>
            <input
              id="new-group-name"
              type="text"
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              required
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={creating}>
            {creating ? '创建中…' : '创建用户组'}
          </button>
        </div>
      </form>

      {/* 用户组列表(卡片网格,每卡含内联成员管理) */}
      <div className="admin-groups mobile-cards" aria-label="用户组列表">
        {groups === null && !error && <div className="loading">加载中…</div>}
        {groups !== null && groups.length === 0 && (
          <div className="empty-state card">暂无用户组</div>
        )}
        {groups !== null &&
          groups.length > 0 &&
          groups.map((g) => (
            <div key={g.id} className="card admin-group-card" data-testid={`group-row-${g.id}`}>
              <div className="admin-group-header">
                <h3 className="admin-group-name">{g.name}</h3>
                <span className="badge badge-info">{g.member_count} 成员</span>
              </div>

              {/* 成员列表 */}
              <div className="admin-group-members">
                {g.members.length === 0 ? (
                  <div className="empty-state empty-state-sm">暂无成员</div>
                ) : (
                  <ul className="member-list" role="list">
                    {g.members.map((uid) => (
                      <li key={uid} className="member-item">
                        <span>{resolveName(uid)}</span>
                        <button
                          type="button"
                          className="btn btn-danger btn-xs"
                          onClick={() => handleRemoveMember(g.id, uid)}
                          disabled={busyId === `${g.id}:remove:${uid}`}
                        >
                          移除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* 添加成员 */}
              <div className="admin-group-add">
                <MemberSelect
                  groupId={g.id}
                  users={users}
                  existingMembers={g.members}
                  onAdd={handleAddMember}
                  disabled={busyId !== null}
                />
              </div>
            </div>
          ))}
      </div>
    </section>
  );
}

/** 添加成员下拉 + 按钮(排除已在组内的用户)。 */
function MemberSelect({
  groupId,
  users,
  existingMembers,
  onAdd,
  disabled,
}: {
  groupId: string;
  users: AdminUser[];
  existingMembers: string[];
  onAdd: (groupId: string, userId: string) => void;
  disabled: boolean;
}) {
  const [selected, setSelected] = useState('');

  const candidates = users.filter((u) => !existingMembers.includes(u.id) && u.enabled);

  return (
    <form
      className="admin-add-member-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (selected) {
          onAdd(groupId, selected);
          setSelected('');
        }
      }}
    >
      <label htmlFor={`member-select-${groupId}`} className="sr-only">
        选择用户
      </label>
      <select
        id={`member-select-${groupId}`}
        aria-label="选择用户"
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
        disabled={disabled || candidates.length === 0}
      >
        <option value="">{candidates.length === 0 ? '无可添加用户' : '选择用户…'}</option>
        {candidates.map((u) => (
          <option key={u.id} value={u.id}>
            {u.username}({u.email})
          </option>
        ))}
      </select>
      <button type="submit" className="btn btn-outline btn-sm" disabled={disabled || !selected}>
        添加
      </button>
    </form>
  );
}
