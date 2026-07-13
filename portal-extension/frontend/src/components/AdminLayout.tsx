/**
 * 管理员后台布局(Slice 11 + Slice 55 重构)— topbar + 左侧栏 + 子路由渲染区。
 *
 * Slice 55:从横向 6 tab 改为 topbar(Issue 54)+ 左侧栏(`shell` = `sidebar` + `main`)。
 *   - sidebar 是 admin 专属(share-list / share-detail / login 不套)
 *   - 两组导航:工作区("分享页"入口,Link 到 /share-pages)+ 管理后台(6 个 nav-item)
 *   - 砍掉设计稿的"系统-设置"项(无对应页)
 *   - nav-item active 态:accent 竖条 + surface-2 背景(CSS `.nav-item.active::before`)
 *
 * 六个 admin nav-item 对应 Issue 11 验收点 2-7:
 *   - 用户管理(/admin/users,验收点 2)
 *   - 用户组管理(/admin/groups,验收点 3)
 *   - 分享页管理(/admin/share-pages,验收点 4)
 *   - 授权管理(/admin/grants,验收点 5)
 *   - 会话搜索(/admin/sessions,验收点 6)
 *   - 审计日志(/admin/audit,验收点 7)
 *
 * 子路由由 App.tsx 用嵌套 <Route> 配置,本组件用 <Outlet/> 渲染。
 * Slice 51 布局修复保留:`.admin-main > section { flex:1; min-height:0; overflow-y:auto }`。
 */
import { useState, type ReactNode } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import AppHeader from './AppHeader';

interface NavDef {
  to: string;
  label: string;
  icon: ReactNode;
}

/** 管理后台 6 项导航(不含"系统-设置",无对应页)。 */
const ADMIN_NAVS: readonly NavDef[] = [
  {
    to: '/admin/users',
    label: '用户管理',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="9" cy="8" r="3.5" />
        <path d="M3 20a6 6 0 0 1 12 0" />
        <path d="M16 5a3 3 0 0 1 0 6" />
        <path d="M17 14a6 6 0 0 1 4 6" />
      </svg>
    ),
  },
  {
    to: '/admin/groups',
    label: '用户组',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="8" cy="9" r="3" />
        <circle cx="16" cy="9" r="3" />
        <path d="M3 19a5 5 0 0 1 10 0" />
        <path d="M11 19a5 5 0 0 1 10 0" />
      </svg>
    ),
  },
  {
    to: '/admin/share-pages',
    label: '分享页管理',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 13v7h16v-7" />
        <path d="M12 3v12" />
        <path d="m8 7 4-4 4 4" />
      </svg>
    ),
  },
  {
    to: '/admin/grants',
    label: '授权',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <rect x="4" y="10" width="16" height="10" rx="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      </svg>
    ),
  },
  {
    to: '/admin/sessions',
    label: '会话搜索',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 5h16v10H8l-4 4z" />
      </svg>
    ),
  },
  {
    to: '/admin/audit',
    label: '审计日志',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M5 4h14v16H5z" />
        <path d="M9 9h6M9 13h6M9 17h3" />
      </svg>
    ),
  },
];

export default function AdminLayout() {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = () => setMenuOpen(false);

  return (
    <>
      <AppHeader
        username={user?.username}
        onLogout={logout}
        isAdmin={user?.is_admin ?? false}
        menuOpen={menuOpen}
        onMenuToggle={() => setMenuOpen((open) => !open)}
      />
      <div className="shell">
        <aside
          id="admin-sidebar"
          className={`sidebar${menuOpen ? ' open' : ''}`}
          aria-label="管理后台导航"
        >
          <div className="nav-group-label">工作区</div>
          <Link className="nav-item" to="/share-pages" onClick={closeMenu}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
              <path d="M4 13v7h16v-7" />
              <path d="M12 3v12" />
              <path d="m8 7 4-4 4 4" />
            </svg>
            分享页
          </Link>
          <div className="nav-group-label">管理后台</div>
          {ADMIN_NAVS.map((nav) => (
            <NavLink
              key={nav.to}
              to={nav.to}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
              onClick={closeMenu}
            >
              {nav.icon}
              {nav.label}
            </NavLink>
          ))}
        </aside>
        {menuOpen && (
          <button
            type="button"
            className="sidebar-backdrop"
            aria-label="关闭管理菜单"
            onClick={closeMenu}
          />
        )}
        <main className="main admin-main">
          <Outlet />
        </main>
      </div>
    </>
  );
}
