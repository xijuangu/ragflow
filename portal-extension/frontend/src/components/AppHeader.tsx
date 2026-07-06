/**
 * 应用顶栏 — 显示用户名与登出按钮;管理员可见「管理后台」入口。
 *
 * 登出调 POST /logout(清除 HTTP-only 会话 cookie),成功后回登录页。
 * 对应 Slice 9 验收点 6:登出后回登录页,再访问分享页 → 跳转登录页。
 *
 * Slice 11:新增 isAdmin 可选 prop,管理员可见「管理后台」链接(对应验收点 1:
 * 管理员能看到管理后台入口,普通用户看不到)。
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

  return (
    <header className="app-header">
      <h1>RAGFlow 权限门户</h1>
      {username && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {isAdmin && (
            <Link className="btn btn-ghost btn-sm" to="/admin/users">
              管理后台
            </Link>
          )}
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>{username}</span>
          <button className="btn btn-ghost" onClick={handleLogout}>
            登出
          </button>
        </div>
      )}
    </header>
  );
}
