/**
 * 登录页 — 用户名 + 密码表单,调 POST /login,成功后跳转分享页列表。
 *
 * 对应 Slice 9 验收点 1:用户能用浏览器打开登录页,输入 admin 凭据登录成功,跳转分享页列表。
 *
 * 登录失败明确错误提示(用户未注册 / 密码错误 / 账号已禁用),来自后端 detail。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';

interface LocationState {
  from?: { pathname: string };
}

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as LocationState | null)?.from?.pathname ?? '/share-pages';

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
      } else {
        setError('登录失败,请检查网络后重试');
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <h2>RAGFlow 权限门户</h2>
        <p className="subtitle">请输入账号密码登录</p>

        {error && <div className="alert-error" role="alert">{error}</div>}

        <div className="form-field">
          <label htmlFor="username">用户名</label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
            autoFocus
          />
        </div>

        <div className="form-field">
          <label htmlFor="password">密码</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>

        <button type="submit" className="btn btn-primary btn-block" disabled={submitting}>
          {submitting ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  );
}
