/**
 * 授权管理页(Slice 11,验收点 5)— 选择分享页 / 列出授权 / 授权给用户或组 / 撤销授权。
 *
 * 对应后端:
 *   - GET /admin/share-pages(选分享页下拉)
 *   - GET /admin/users + /admin/groups(授权对象下拉,显示用户名/组名)
 *   - GET /admin/share-pages/:id/grants(列出某分享页的授权)
 *   - POST /admin/share-pages/:id/grants(授权,201,后端写 grant_create 审计)
 *   - DELETE /share-pages/:id/grants/:subject_type/:subject_id(撤销,后端写 grant_revoke 审计)
 *
 * UI 流程:
 *   1. 顶部下拉选择分享页(默认第一个)→ 加载该页 grants。
 *   2. grants 列表显示主体类型(用户/组)+ 名称(解析为 username/group name)+ 权限 + 撤销按钮。
 *   3. 创建授权表单:主体类型(user/group)下拉 → 授权对象下拉(随类型切换)→ 授权按钮。
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
  type SubjectType,
} from '../../api/client';
import { useAdminList } from '../../hooks/useAdminList';
import { useOptimisticToggle } from '../../hooks/useOptimisticToggle';

export default function GrantsAdminPage() {
  const [sharePages, setSharePages] = useState<AdminSharePage[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [selectedPageId, setSelectedPageId] = useState<string>('');
  const [grants, setGrants] = useState<AdminGrant[] | null>(null);
  const { error, setError } = useAdminList(
    async () => {
      const [pagesRes, usersRes, groupsRes] = await Promise.all([
        api.listAdminSharePages(),
        api.listAdminUsers(),
        api.listAdminGroups(),
      ]);
      return {
        sharePages: pagesRes.share_pages,
        users: usersRes.users,
        groups: groupsRes.groups,
      };
    },
    {
      errorMessage: '加载分享页/用户/用户组失败',
      onSuccess: (d) => {
        setSharePages(d.sharePages);
        setUsers(d.users);
        setGroups(d.groups);
        if (d.sharePages.length > 0) setSelectedPageId(d.sharePages[0].id);
      },
    },
  );
  const { busyId: busyKey, run } = useOptimisticToggle({ onError: setError });

  // 创建授权表单
  const [subjectType, setSubjectType] = useState<SubjectType>('user');
  const [subjectId, setSubjectId] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // user_id → username / group_id → name 映射(显示用)
  const userMap = useMemo(() => {
    const m = new Map<string, AdminUser>();
    for (const u of users) m.set(u.id, u);
    return m;
  }, [users]);
  const groupMap = useMemo(() => {
    const m = new Map<string, AdminGroup>();
    for (const g of groups) m.set(g.id, g);
    return m;
  }, [groups]);

  const resolveSubjectName = useCallback(
    (grant: AdminGrant) => {
      if (grant.subject_type === 'user') {
        return userMap.get(grant.subject_id)?.username ?? grant.subject_id;
      }
      return groupMap.get(grant.subject_id)?.name ?? grant.subject_id;
    },
    [userMap, groupMap],
  );

  // 初始加载由 useAdminList 完成(上方);以下仅保留分享页切换 → 加载 grants
  const loadGrants = useCallback(async (pageId: string) => {
    if (!pageId) {
      setGrants(null);
      return;
    }
    setGrants(null);
    setError(null);
    try {
      const res = await api.listAdminGrants(pageId);
      setGrants(res.grants);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载授权列表失败');
    }
  }, [setError]);

  useEffect(() => {
    if (selectedPageId) void loadGrants(selectedPageId);
  }, [selectedPageId, loadGrants]);

  const handleCreate = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (creating || !selectedPageId) return;
      setFormError(null);
      if (!subjectId) {
        setFormError('请选择授权对象');
        return;
      }
      setCreating(true);
      try {
        const created = await api.createAdminGrant(selectedPageId, {
          subject_type: subjectType,
          subject_id: subjectId,
        });
        setGrants((prev) => (prev ? [...prev, created] : [created]));
        setSubjectId('');
      } catch (e) {
        setFormError(e instanceof ApiError ? e.message : '授权失败');
      } finally {
        setCreating(false);
      }
    },
    [creating, selectedPageId, subjectType, subjectId, setGrants],
  );

  const handleRevoke = useCallback(
    async (grant: AdminGrant) => {
      if (!selectedPageId) return;
      const key = `${grant.subject_type}:${grant.subject_id}`;
      await run({
        id: key,
        optimistic: () =>
          setGrants((prev) =>
            prev
              ? prev.filter(
                  (g) =>
                    !(g.subject_type === grant.subject_type && g.subject_id === grant.subject_id),
                )
              : prev,
          ),
        rollback: () => setGrants((prev) => (prev ? [...prev, grant] : [grant])),
        action: () => api.revokeGrant(selectedPageId, grant.subject_type, grant.subject_id),
        errorMessage: '撤销授权失败',
      });
    },
    [run, selectedPageId, setGrants],
  );

  // 授权对象下拉选项(随 subjectType 切换)
  const subjectOptions: { value: string; label: string }[] =
    subjectType === 'user'
      ? users.map((u) => ({ value: u.id, label: `${u.username}(${u.email})` }))
      : groups.map((g) => ({ value: g.id, label: g.name }));

  return (
    <section>
      <div className="page-head">
        <div>
          <div className="kicker">管理后台 / 授权</div>
          <h1>授权管理</h1>
          <div className="sub">选择分享页后,将其授权给用户或用户组。授权创建与撤销均写入审计日志。</div>
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {/* 选择分享页 */}
      <div className="card admin-form admin-form-row admin-form-row-compact">
        <div className="form-field">
          <label htmlFor="grant-share-page">选择分享页</label>
          <select
            id="grant-share-page"
            value={selectedPageId}
            onChange={(e) => setSelectedPageId(e.target.value)}
            disabled={sharePages.length === 0}
          >
            {sharePages.length === 0 && <option value="">无可用分享页</option>}
            {sharePages.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}({p.ragflow_resource_id})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* 创建授权表单(内联,无 drawer) */}
      {selectedPageId && (
        <form className="admin-form card" onSubmit={handleCreate} aria-label="创建授权表单">
          <h3 className="form-title">授权给用户或组</h3>
          {formError && <div className="alert-error">{formError}</div>}
          <div className="admin-form-row">
            <div className="form-field">
              <label htmlFor="grant-subject-type">主体类型</label>
              <select
                id="grant-subject-type"
                value={subjectType}
                onChange={(e) => {
                  setSubjectType(e.target.value as SubjectType);
                  setSubjectId('');
                }}
              >
                <option value="user">用户</option>
                <option value="group">用户组</option>
              </select>
            </div>
            <div className="form-field">
              <label htmlFor="grant-subject-id">授权对象</label>
              <select
                id="grant-subject-id"
                value={subjectId}
                onChange={(e) => setSubjectId(e.target.value)}
                disabled={subjectOptions.length === 0}
              >
                <option value="">{subjectOptions.length === 0 ? '无可选对象' : '选择…'}</option>
                {subjectOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="btn btn-primary" disabled={creating || !subjectId}>
              {creating ? '授权中…' : '授权'}
            </button>
          </div>
        </form>
      )}

      {/* 授权列表 */}
      {selectedPageId && (
        <div className="card card-table">
          <div className="card-body">
            <div className="table-wrap">
              {grants === null && <div className="loading">加载中…</div>}
              {grants !== null && grants.length === 0 && (
                <div className="empty-state">暂无授权</div>
              )}
              {grants !== null && grants.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>主体类型</th>
                      <th>名称</th>
                      <th>权限</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {grants.map((g) => {
                      const key = `${g.subject_type}-${g.subject_id}`;
                      return (
                        <tr key={key} data-testid={`grant-row-${key}`}>
                          <td>{g.subject_type === 'user' ? '用户' : '用户组'}</td>
                          <td>{resolveSubjectName(g)}</td>
                          <td>{g.permission}</td>
                          <td>
                            <div className="admin-actions">
                              <button
                                type="button"
                                className="btn btn-danger btn-sm"
                                onClick={() => handleRevoke(g)}
                                disabled={busyKey === `${g.subject_type}:${g.subject_id}`}
                              >
                                撤销
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
