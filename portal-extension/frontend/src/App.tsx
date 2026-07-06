/**
 * 应用根路由 — 定义 /login、/share-pages、/share-pages/:id、/admin/* 路由。
 *
 * - /login:登录页(已登录时跳转 /share-pages)
 * - /share-pages:分享页列表(受 ProtectedRoute 保护)
 * - /share-pages/:id:分享页详情(iframe 加载,受 ProtectedRoute 保护)
 * - /admin/*:管理员后台(受 AdminRoute 保护,非 admin 跳 /share-pages)
 *   嵌套路由:users / groups / share-pages / grants / sessions / audit
 * - /:跳转 /share-pages
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import AdminLayout from './components/AdminLayout';
import AdminRoute from './components/AdminRoute';
import ProtectedRoute from './components/ProtectedRoute';
import LoginPage from './pages/LoginPage';
import SharePagesPage from './pages/SharePagesPage';
import SharePageDetailPage from './pages/SharePageDetailPage';
import UsersAdminPage from './pages/admin/UsersAdminPage';
import GroupsAdminPage from './pages/admin/GroupsAdminPage';
import SharePagesAdminPage from './pages/admin/SharePagesAdminPage';
import GrantsAdminPage from './pages/admin/GrantsAdminPage';
import SessionsAdminPage from './pages/admin/SessionsAdminPage';
import AuditLogsAdminPage from './pages/admin/AuditLogsAdminPage';

export default function App() {
  const { user, loading } = useAuth();

  return (
    <Routes>
      <Route
        path="/login"
        element={
          // 已登录用户访问 /login → 跳转列表页(避免重复登录)
          !loading && user ? <Navigate to="/share-pages" replace /> : <LoginPage />
        }
      />
      <Route
        path="/share-pages"
        element={
          <ProtectedRoute>
            <SharePagesPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/share-pages/:id"
        element={
          <ProtectedRoute>
            <SharePageDetailPage />
          </ProtectedRoute>
        }
      />
      {/* 管理员后台 — 嵌套路由,AdminRoute 守卫 + AdminLayout 提供 tab 导航与 Outlet */}
      <Route
        path="/admin"
        element={
          <AdminRoute>
            <AdminLayout />
          </AdminRoute>
        }
      >
        <Route index element={<Navigate to="/admin/users" replace />} />
        <Route path="users" element={<UsersAdminPage />} />
        <Route path="groups" element={<GroupsAdminPage />} />
        <Route path="share-pages" element={<SharePagesAdminPage />} />
        <Route path="grants" element={<GrantsAdminPage />} />
        <Route path="sessions" element={<SessionsAdminPage />} />
        <Route path="audit" element={<AuditLogsAdminPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/share-pages" replace />} />
      <Route path="*" element={<Navigate to="/share-pages" replace />} />
    </Routes>
  );
}
