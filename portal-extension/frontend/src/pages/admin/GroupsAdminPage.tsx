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
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, api, type AdminGroup, type AdminUser } from '../../api/client';

export default function GroupsAdminPage() {
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // `${groupId}:action:userId` 或 groupId

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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [groupsRes, usersRes] = await Promise.all([
          api.listAdminGroups(),
          api.listAdminUsers(),
        ]);
        if (cancelled) return;
        setGroups(groupsRes.groups);
        setUsers(usersRes.users);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : '加载用户组失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCreate = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (creating) return;
      const name = groupName.trim();
      if (!name) return;
      setCreating(true);
      try {
        const created = await api.createAdminGroup(name);
        setGroups((prev) => (prev ? [...prev, created] : [created]));
        setGroupName('');
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '创建用户组失败');
      } finally {
        setCreating(false);
      }
    },
    [creating, groupName],
  );

  const handleAddMember = useCallback(
    async (groupId: string, userId: string) => {
      if (!userId || busy) return;
      setBusy(`${groupId}:add:${userId}`);
      // 乐观更新:追加 user_id 到成员列表
      setGroups((prev) =>
        prev
          ? prev.map((g) =>
              g.id === groupId && !g.members.includes(userId)
                ? { ...g, members: [...g.members, userId], member_count: g.member_count + 1 }
                : g,
            )
          : prev,
      );
      try {
        await api.addAdminGroupMember(groupId, userId);
      } catch (e) {
        // 回滚
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
        );
        setError(e instanceof ApiError ? e.message : '添加成员失败');
      } finally {
        setBusy(null);
      }
    },
    [busy],
  );

  const handleRemoveMember = useCallback(
    async (groupId: string, userId: string) => {
      if (busy) return;
      setBusy(`${groupId}:remove:${userId}`);
      // 乐观更新:从成员列表移除
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
      );
      try {
        await api.removeAdminGroupMember(groupId, userId);
      } catch (e) {
        // 回滚
        setGroups((prev) =>
          prev
            ? prev.map((g) =>
                g.id === groupId && !g.members.includes(userId)
                  ? { ...g, members: [...g.members, userId], member_count: g.member_count + 1 }
                  : g,
              )
            : prev,
        );
        setError(e instanceof ApiError ? e.message : '移除成员失败');
      } finally {
        setBusy(null);
      }
    },
    [busy],
  );

  return (
    <section>
      <h2 className="page-title">用户组管理</h2>

      {error && <div className="alert-error">{error}</div>}

      {/* 创建用户组表单 */}
      <form className="admin-form card" onSubmit={handleCreate} aria-label="创建用户组表单">
        <h3 className="form-title">创建用户组</h3>
        <div className="admin-form-row">
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

      {/* 用户组列表 */}
      <div className="admin-groups">
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
                          disabled={busy === `${g.id}:remove:${uid}`}
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
                  disabled={busy !== null}
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
      <button type="submit" className="btn btn-ghost btn-sm" disabled={disabled || !selected}>
        添加
      </button>
    </form>
  );
}
