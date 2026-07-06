/**
 * admin 列表页通用的初始加载 hook(TD7:从 6 个 admin 页同构 useEffect 抽取)。
 *
 * 封装 mount 时的 cancelled IIFE + fetch + ApiError catch + data/error state。
 *
 * 两种用法:
 *   - 单列表(初始 null):直接用返回的 data/setData 作为列表状态。
 *       const { data: users, setData: setUsers, error, setError } =
 *         useAdminList(() => api.listAdminUsers().then(r => r.users), { errorMessage: '...' });
 *   - 多列表/非 null 初始值:传 onSuccess 把结果分发给外部 setter,忽略 data。
 *       const { error, setError } = useAdminList(fetcher, {
 *         errorMessage: '...',
 *         onSuccess: (d) => { setGroups(d.groups); setUsers(d.users); },
 *       });
 */
import { useEffect, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { ApiError } from '../api/client';

export interface UseAdminListOptions<T> {
  /** 拉取失败且非 ApiError 时的兜底错误文案。 */
  errorMessage: string;
  /** 拉取成功后把结果分发给外部 setter(并行加载多列表的场景)。 */
  onSuccess?: (data: T) => void;
}

export interface UseAdminListResult<T> {
  /** 拉取结果(null 表示尚未加载)。 */
  data: T | null;
  setData: Dispatch<SetStateAction<T | null>>;
  error: string | null;
  setError: Dispatch<SetStateAction<string | null>>;
}

export function useAdminList<T>(
  fetcher: () => Promise<T>,
  options: UseAdminListOptions<T>,
): UseAdminListResult<T> {
  const { errorMessage, onSuccess } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetcher();
        if (cancelled) return;
        setData(res);
        onSuccess?.(res);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : errorMessage);
      }
    })();
    return () => {
      cancelled = true;
    };
    // 仅在 mount 时拉取一次;fetcher/onSuccess/errorMessage 为配置,不应触发重新拉取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { data, setData, error, setError };
}
