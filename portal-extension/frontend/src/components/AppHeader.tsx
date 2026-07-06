/**
 * 应用顶栏 — 显示用户名与登出按钮。
 *
 * 登出调 POST /logout(清除 HTTP-only 会话 cookie),成功后回登录页。
 * 对应 Slice 9 验收点 6:登出后回登录页,再访问分享页 → 跳转登录页。
 */
import { useNavigate } from 'react-router-dom';

interface AppHeaderProps {
  username?: string;
  onLogout: () => Promise<void>;
}

export default function AppHeader({ username, onLogout }: AppHeaderProps) {
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
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>{username}</span>
          <button className="btn btn-ghost" onClick={handleLogout}>
            登出
          </button>
        </div>
      )}
    </header>
  );
}
