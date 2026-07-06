/**
 * 认证上下文 — 管理当前登录用户状态。
 *
 * 由于会话 cookie 是 HTTP-only(前端 JS 无法读取),通过 GET /me 探测登录态:
 *   - 200 → 已登录,user 有值
 *   - 403 → 未登录,user 为 null
 *
 * 路由守卫(ProtectedRoute / LoginPage)依据 user 状态决定渲染或跳转。
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api, ApiError, type UserInfo } from '../api/client';

interface AuthContextValue {
  user: UserInfo | null;
  loading: boolean; // 初始探测中
  login: (username: string, password: string) => Promise<UserInfo>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setUser(null);
      } else {
        // 网络错误等,保守视为未登录
        setUser(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const me = await api.login(username, password);
    setUser(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // 无论 logout 请求成功与否,前端都清除用户状态
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- React context 标准模式:hook 与 Provider 同文件
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error('useAuth 必须在 AuthProvider 内使用');
  }
  return ctx;
}
