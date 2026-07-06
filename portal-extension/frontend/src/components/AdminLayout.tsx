/**
 * 管理员后台布局(Slice 11)— 顶栏 + 六个 tab 导航 + 子路由渲染区。
 *
 * 六个 tab 对应 Issue 11 验收点 2-7:
 *   - 用户管理(/admin/users,验收点 2)
 *   - 用户组管理(/admin/groups,验收点 3)
 *   - 分享页管理(/admin/share-pages,验收点 4)
 *   - 授权管理(/admin/grants,验收点 5)
 *   - 会话搜索(/admin/sessions,验收点 6)
 *   - 审计日志(/admin/audit,验收点 7)
 *
 * 子路由由 App.tsx 用嵌套 <Route> 配置,本组件用 <Outlet/> 渲染。
 */
import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import AppHeader from './AppHeader';

interface TabDef {
  to: string;
  label: string;
}

const TABS: readonly TabDef[] = [
  { to: '/admin/users', label: '用户管理' },
  { to: '/admin/groups', label: '用户组' },
  { to: '/admin/share-pages', label: '分享页' },
  { to: '/admin/grants', label: '授权' },
  { to: '/admin/sessions', label: '会话搜索' },
  { to: '/admin/audit', label: '审计日志' },
];

export default function AdminLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="app-layout">
      <AppHeader username={user?.username} onLogout={logout} isAdmin={user?.is_admin ?? false} />
      <nav className="admin-tabs" aria-label="管理后台导航">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) =>
              `admin-tab${isActive ? ' admin-tab-active' : ''}`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <main className="app-main admin-main">
        <Outlet />
      </main>
    </div>
  );
}
