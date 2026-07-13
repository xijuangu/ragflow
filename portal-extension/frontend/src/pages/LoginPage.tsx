/**
 * 登录页(Issue 53)— 左右分屏:左品牌介绍 + 右登录表单。
 *
 * 对应 Slice 9 验收点 1:用户能用浏览器打开登录页,输入 admin 凭据登录成功,跳转分享页列表。
 * 登录失败明确错误提示(用户未注册 / 密码错误 / 账号已禁用),来自后端 detail。
 *
 * Issue 53(Slice 53):从单栏表单迁移到 login-shell(login-aside + login-main),
 * 套 D1 Graphite 设计系统。aside 文案保留设计稿(pt_ 令牌 / SSE 归属 / 双删保障,
 * 已贴合项目)。登录逻辑不动(POST /login + AuthContext)。tracer bullet —— 验证
 * 新设计系统在 React JSX 中的迁移模式。
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
  const ssoEnabled = import.meta.env.VITE_SSO_ENABLED === 'true';

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
    <div className="login-shell">
      <aside className="login-aside">
        <div className="la-brand">
          <span className="dot"></span>
          <span>RAGFlow 权限门户</span>
        </div>
        <div className="la-body">
          <h2>统一权限门户,安全访问 RAGFlow 知识库</h2>
          <p>在 RAGFlow 嵌入方案之上增加权限控制层,解决租户级 Token 泄露、历史会话丢失、缺用户角色审计三大企业落地阻碍。</p>
          <div className="la-feats">
            <div className="la-feat">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
              <div><div className="feat-title">短期可撤销令牌</div><div className="feat-desc">租户级 Token 全程不离开服务端,签发 pt_ 前缀短期令牌注入 iframe</div></div>
            </div>
            <div className="la-feat">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M4 5h16v10H8l-4 4z"/></svg>
              <div><div className="feat-title">会话归属绑定</div><div className="feat-desc">SSE 成功后解析 session_id 绑定当前用户,关页面不丢失历史</div></div>
            </div>
            <div className="la-feat">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8"/><path d="M20 4v4h-4"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 16"/><path d="M4 20v-4h4"/></svg>
              <div><div className="feat-title">双删与重试保障</div><div className="feat-desc">先删 RAGFlow 再删门户,失败标记 pending_deletion 由后台清理</div></div>
            </div>
          </div>
        </div>
        <div className="la-foot">基于 RAGFlow v0.26.0 · 同源部署 · X-Frame-Options: SAMEORIGIN</div>
      </aside>

      <main className="login-main">
        <div className="login-card">
          <div className="kicker">欢迎回来</div>
          <h1>登录到工作区</h1>
          <div className="sub">输入你的账号信息以访问被分享的知识库。</div>

          <form onSubmit={handleSubmit}>
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

          {ssoEnabled && (
            <>
              <div className="login-sso"><span>或</span></div>
              <button
                type="button"
                className="btn btn-secondary btn-block"
                onClick={() => { window.location.href = `${import.meta.env.BASE_URL}sso/login`; }}
              >
                使用企业 SSO 登录
              </button>
            </>
          )}

          <div className="login-alt">还没有账号? <span className="login-contact-note">联系管理员开通</span></div>
        </div>
      </main>
    </div>
  );
}
