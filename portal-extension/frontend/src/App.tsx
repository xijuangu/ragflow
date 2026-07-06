/**
 * 应用根路由 — 定义 /login、/share-pages、/share-pages/:id 三条路由。
 *
 * - /login:登录页(已登录时跳转 /share-pages)
 * - /share-pages:分享页列表(受 ProtectedRoute 保护)
 * - /share-pages/:id:分享页详情(iframe 加载,受 ProtectedRoute 保护)
 * - /:跳转 /share-pages
 */
import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import LoginPage from './pages/LoginPage';
import SharePagesPage from './pages/SharePagesPage';
import SharePageDetailPage from './pages/SharePageDetailPage';

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
      <Route path="/" element={<Navigate to="/share-pages" replace />} />
      <Route path="*" element={<Navigate to="/share-pages" replace />} />
    </Routes>
  );
}
