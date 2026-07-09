/**
 * 应用顶栏(Issue 54)— topbar 组件,替换旧 AppHeader。
 *
 * 登出调 POST /logout(清除 HTTP-only 会话 cookie),成功后回登录页。
 * 对应 Slice 9 验收点 6:登出后回登录页,再访问分享页 → 跳转登录页。
 *
 * Slice 11:isAdmin 可选 prop,管理员可见「管理后台」入口。
 *
 * Issue 54(Slice 54):从 .app-header 迁移到 .topbar(粘性毛玻璃 + 发丝边),
 * 砍掉设计稿的全局搜索框和通知铃铛(纯视觉换皮,无后端)。brand 左对齐,
 * avatar(用户名)+ 登出右对齐。所有非 login 页共享(SharePagesPage /
 * SharePageDetailPage / AdminLayout)。
 */
import { Link, useNavigate } from 'react-router-dom';

interface AppHeaderProps {
  username?: string;
  onLogout: () => Promise<void>;
  isAdmin?: boolean;
}

export default function AppHeader({ username, onLogout, isAdmin = false }: AppHeaderProps) {
  const navigate = useNavigate();

  async function handleLogout() {
    await onLogout();
    navigate('/login', { replace: true });
  }

  const initial = username ? username.charAt(0).toUpperCase() : '?';

  return (
    <header className="topbar">
      <div className="brand">
        <span className="dot"></span>
        <span>RAGFlow 权限门户</span>
        <small>v0.26</small>
      </div>
      <div className="topbar-right">
        {isAdmin && (
          <Link className="btn btn-ghost btn-sm" to="/admin/users">
            管理后台
          </Link>
        )}
        {username && (
          <div className="avatar">
            <span className="chip">{initial}</span>
            <span className="name">{username}</span>
          </div>
        )}
        <button className="btn btn-ghost btn-sm" onClick={handleLogout}>
          登出
        </button>
      </div>
    </header>
  );
}
