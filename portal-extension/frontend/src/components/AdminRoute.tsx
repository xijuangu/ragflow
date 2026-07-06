/**
 * 管理员路由守卫(Slice 11)— 未登录跳 /login,已登录非管理员跳 /share-pages。
 *
 * 对应 Issue 11 验收点 1:
 *   - 管理员能看到管理后台入口。
 *   - 普通用户看不到且直接访问 URL → 403/跳转(此处实现跳转 /share-pages)。
 *
 * 与 ProtectedRoute 的区别:ProtectedRoute 只校验登录态,AdminRoute 额外校验 is_admin。
 */
import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAuth } from '../auth/AuthContext';

export default function AdminRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="loading">加载中…</div>;
  }

  if (!user) {
    // 未登录 → 跳登录页(登录后回到管理后台)
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (!user.is_admin) {
    // 已登录但非管理员 → 跳分享页列表(对应验收点 1:普通用户直接访问 URL → 跳转)
    return <Navigate to="/share-pages" replace />;
  }

  return <>{children}</>;
}
