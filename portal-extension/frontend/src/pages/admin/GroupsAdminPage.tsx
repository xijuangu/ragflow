/**
 * 用户组管理页(Slice 11;Issue 89 重设计为 master-detail 主从视图)。
 *
 * 布局:左侧可搜索组列表(Issue 86 搜索能力),右侧选中组详情面板——
 *   顶部"添加成员"入口(Issue 88 combobox 多选 + chips,一次可添加多人)、
 *   成员列表(每行带移除按钮,移除需确认弹窗)、
 *   该组已获得的分享页授权列表(选中组时并行拉取各分享页 grants 后按组过滤)。
 *
 * 对应后端:
 *   - GET /admin/groups(列表,含 members: user_id[])
 *   - POST /admin/groups(创建,201)
 *   - POST /admin/groups/:id/members(添加成员)
 *   - DELETE /admin/groups/:id/members/:user_id(移除成员)
 *   - GET /admin/share-pages + GET /admin/share-pages/:id/grants(组授权来源)
 *
 * 额外调 GET /admin/users 构建 user_id → username 映射,用于:
 *   1. 成员列表显示用户名(而非裸 ID)。
 *   2. 添加成员 combobox 选项(用户名 + 邮箱参与搜索)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import {
  ApiError,
  api,
  type AdminGrant,
  type AdminGroup,
  type AdminSharePage,
  type AdminUser,
} from '../../api/client';
import { useAdminList, useAdminListView } from '../../hooks/useAdminList';
import type { AdminListViewConfig } from '../../hooks/useAdminList';
import { useOptimisticToggle } from '../../hooks/useOptimisticToggle';
import { AdminListToolbar } from '../../components/AdminListControls';
import SearchableCombobox from '../../components/SearchableCombobox';
import ConfirmDialog from '../../components/ConfirmDialog';

/** Issue 87/89:组列表搜索列 = 组名;master-detail 下筛选/排序不适用(无枚举列与表头)。 */
const GROUP_LIST_VIEW: AdminListViewConfig<AdminGroup> = {
  searchColumns: [(g) => g.name],
};

export default function GroupsAdminPage() {
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [sharePages, setSharePages] = useState<AdminSharePage[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState('');
  const { error, setError } = useAdminList(
    async () => {
      const [groupsRes, usersRes, pagesRes] = await Promise.all([
        api.listAdminGroups(),
        api.listAdminUsers(),
        api.listAdminSharePages(),
      ]);
      return { groups: groupsRes.groups, users: usersRes.users, sharePages: pagesRes.share_pages };
    },
    {
      errorMessage: '加载用户组失败',
      onSuccess: (d) => {
        setGroups(d.groups);
        setUsers(d.users);
        setSharePages(d.sharePages);
        // 默认选中首组,右侧详情即所见
        if (d.groups.length > 0) setSelectedGroupId(d.groups[0].id);
      },
    },
  );
  const { busyId, run } = useOptimisticToggle({ onError: setError });

  // 组名搜索(左侧列表)
  const {
    view: visibleGroups,
    search,
    setSearch,
  } = useAdminListView(groups, GROUP_LIST_VIEW);

  const [groupName, setGroupName] = useState('');
  const [creating, setCreating] = useState(false);

  // 选中组详情
  const selectedGroup = useMemo(
    () => groups?.find((g) => g.id === selectedGroupId) ?? null,
    [groups, selectedGroupId],
  );

  // 该组已获得的分享页授权(选中组切换时并行拉取各分享页 grants 后过滤;列表量级小,§12.1)
  const [groupGrants, setGroupGrants] = useState<AdminGrant[] | null>(null);
  const sharePageMap = useMemo(() => {
    const m = new Map<string, AdminSharePage>();
    for (const p of sharePages) m.set(p.id, p);
    return m;
  }, [sharePages]);

  useEffect(() => {
    if (!selectedGroupId || sharePages.length === 0) {
      setGroupGrants(null);
      return;
    }
    let cancelled = false;
    setGroupGrants(null);
    (async () => {
      try {
        const results = await Promise.all(sharePages.map((p) => api.listAdminGrants(p.id)));
        if (cancelled) return;
        setGroupGrants(
          results
            .flatMap((r) => r.grants)
            .filter((g) => g.subject_type === 'group' && g.subject_id === selectedGroupId),
        );
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : '加载组授权失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedGroupId, sharePages, setError]);

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
        setSelectedGroupId(safeCreated.id);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : '创建用户组失败');
      } finally {
        setCreating(false);
      }
    },
    [creating, groupName, setGroups, setError],
  );

  // Issue 88:添加成员支持一次多选(combobox chips),逐个乐观更新 + 调后端
  const handleAddMember = useCallback(
    async (groupId: string, userIds: string[]) => {
      for (const userId of userIds) {
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
      }
    },
    [run, setGroups],
  );

  // Issue 89:移除成员需确认(ConfirmDialog)
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);

  const confirmRemoveMember = useCallback(async () => {
    if (!selectedGroup || !removeTarget) return;
    const groupId = selectedGroup.id;
    const userId = removeTarget;
    setRemoveTarget(null);
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
  }, [run, setGroups, selectedGroup, removeTarget]);

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 用户组</div>
          <h1>用户组管理</h1>
          <div className="sub">左侧选择用户组,右侧直接管理成员与查看该组已获得的分享页授权。</div>
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

      <div className="group-master-detail">
        {/* 左侧:可搜索组列表 */}
        <div className="card group-master">
          <AdminListToolbar
            search={search}
            onSearchChange={setSearch}
            searchLabel="搜索用户组"
            searchPlaceholder="按组名搜索"
          />
          {groups === null && !error && <div className="loading">加载中…</div>}
          {groups !== null && groups.length === 0 && (
            <div className="empty-state">暂无用户组</div>
          )}
          {groups !== null && groups.length > 0 && visibleGroups !== null && visibleGroups.length === 0 && (
            <div className="empty-state">无匹配结果</div>
          )}
          {visibleGroups !== null && visibleGroups.length > 0 && (
            <ul className="group-master-list" role="list" aria-label="用户组列表">
              {visibleGroups.map((g) => (
                <li key={g.id}>
                  <button
                    type="button"
                    data-testid={`group-row-${g.id}`}
                    className={`group-master-item${g.id === selectedGroupId ? ' active' : ''}`}
                    aria-current={g.id === selectedGroupId ? 'true' : undefined}
                    onClick={() => setSelectedGroupId(g.id)}
                  >
                    <span className="group-master-item-name">{g.name}</span>
                    <span className="badge badge-info">{g.member_count} 成员</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 右侧:选中组详情(成员 + 分享页授权 + 添加成员) */}
        <div className="card group-detail" data-testid="group-detail">
          {!selectedGroup && <div className="empty-state">从左侧选择一个用户组查看详情</div>}
          {selectedGroup && (
            <>
              <div className="admin-group-header">
                <h3 className="admin-group-name">{selectedGroup.name}</h3>
                <span className="badge badge-info">{selectedGroup.member_count} 成员</span>
              </div>

              {/* 顶部添加成员入口(combobox 多选 + chips) */}
              <div className="admin-group-add">
                <MemberSelect
                  key={selectedGroup.id}
                  groupId={selectedGroup.id}
                  users={users}
                  existingMembers={selectedGroup.members}
                  onAdd={handleAddMember}
                  disabled={busyId !== null}
                />
              </div>

              {/* 成员列表 */}
              <div className="admin-group-members">
                <h4 className="group-detail-subhead">成员</h4>
                {selectedGroup.members.length === 0 ? (
                  <div className="empty-state empty-state-sm">暂无成员</div>
                ) : (
                  <ul className="member-list" role="list">
                    {selectedGroup.members.map((uid) => (
                      <li key={uid} className="member-item">
                        <span>{resolveName(uid)}</span>
                        <button
                          type="button"
                          className="btn btn-danger btn-xs"
                          onClick={() => setRemoveTarget(uid)}
                          disabled={busyId === `${selectedGroup.id}:remove:${uid}`}
                        >
                          移除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* 该组已获得的分享页授权 */}
              <div className="group-grants">
                <h4 className="group-detail-subhead">已获得的分享页授权</h4>
                {groupGrants === null && <div className="loading">加载中…</div>}
                {groupGrants !== null && groupGrants.length === 0 && (
                  <div className="empty-state empty-state-sm">暂无授权</div>
                )}
                {groupGrants !== null && groupGrants.length > 0 && (
                  <ul className="member-list" role="list">
                    {groupGrants.map((g) => (
                      <li key={g.share_page_id} className="member-item">
                        <span>{sharePageMap.get(g.share_page_id)?.name ?? g.share_page_id}</span>
                        <span className="badge badge-info">{g.permission}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={removeTarget !== null}
        title="移除成员"
        message={`将把 ${resolveName(removeTarget ?? '')} 从「${selectedGroup?.name ?? ''}」移除。`}
        confirmText="确认移除"
        variant="danger"
        onConfirm={confirmRemoveMember}
        onCancel={() => setRemoveTarget(null)}
      />
    </section>
  );
}

/** 添加成员 combobox(多选 + chips)+ 按钮(排除已在组内的用户)。Issue 88/89。 */
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
  onAdd: (groupId: string, userIds: string[]) => Promise<void>;
  disabled: boolean;
}) {
  const [selected, setSelected] = useState<string[]>([]);

  const candidates = users
    .filter((u) => !existingMembers.includes(u.id) && u.enabled)
    .map((u) => ({ value: u.id, label: u.username, sublabel: u.email }));

  return (
    <form
      className="admin-add-member-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (selected.length > 0) {
          await onAdd(groupId, selected);
          setSelected([]);
        }
      }}
    >
      <label htmlFor={`member-select-${groupId}`} className="sr-only">
        选择用户
      </label>
      <SearchableCombobox
        mode="multiple"
        id={`member-select-${groupId}`}
        ariaLabel="选择用户"
        options={candidates}
        values={selected}
        onChange={setSelected}
        placeholder={candidates.length === 0 ? '无可添加用户' : '搜索用户名或邮箱…'}
        disabled={disabled || candidates.length === 0}
      />
      <button type="submit" className="btn btn-outline btn-sm" disabled={disabled || selected.length === 0}>
        添加
      </button>
    </form>
  );
}
