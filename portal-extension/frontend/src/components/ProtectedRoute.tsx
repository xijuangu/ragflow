/**
 * 路由守卫 — 未登录用户跳转 /login。
 *
 * 对应 Slice 9 验收点 5:未登录用户直接访问分享页详情页 → 跳转登录页。
 */
import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAuth } from '../auth/AuthContext';

export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="loading">加载中…</div>;
  }

  if (!user) {
    // 跳转登录页,登录后回到原页面(from state)
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
