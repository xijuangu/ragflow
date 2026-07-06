/**
 * admin 页通用的「乐观更新 + 失败回滚」hook(TD7:从各 admin 页 busyId 模式抽取)。
 *
 * 封装 busyId 状态 + 繁忙守卫 + try/catch/finally + ApiError 兜底文案。
 * 调用方提供乐观更新、回滚、API 调用三项即可。
 *
 * 悲观操作(如硬删除:成功后才改列表)可不传 optimistic/rollback,
 * 把「API 调用 + 成功后改列表」一起放进 action。
 */
import { useCallback, useState } from 'react';
import { ApiError } from '../api/client';

export interface UseOptimisticToggleOptions {
  /** 失败时把错误写入外部 error state(通常是 useAdminList 的 setError)。 */
  onError: (message: string) => void;
}

export interface OptimisticRunOptions {
  /** 用于繁忙去重的 key(通常是 item.id 或 `${groupId}:add:${userId}` 复合键)。 */
  id: string;
  /** 真正的 API 调用(在乐观更新之后执行);返回值被忽略。 */
  action: () => Promise<unknown>;
  /** 乐观更新(action 之前同步执行);悲观操作可省略。 */
  optimistic?: () => void;
  /** action 失败时回滚乐观更新;悲观操作可省略。 */
  rollback?: () => void;
  /** 非 ApiError 时的兜底错误文案。 */
  errorMessage: string;
}

export interface UseOptimisticToggleResult {
  /** 当前正在操作的 id(null 表示空闲)。 */
  busyId: string | null;
  /** 执行一次「乐观更新 + API 调用 + 失败回滚」流程;繁忙时直接返回。 */
  run: (options: OptimisticRunOptions) => Promise<void>;
}

export function useOptimisticToggle(
  options: UseOptimisticToggleOptions,
): UseOptimisticToggleResult {
  const { onError } = options;
  const [busyId, setBusyId] = useState<string | null>(null);

  const run = useCallback(
    async (opts: OptimisticRunOptions) => {
      if (busyId) return;
      setBusyId(opts.id);
      opts.optimistic?.();
      try {
        await opts.action();
      } catch (e) {
        opts.rollback?.();
        onError(e instanceof ApiError ? e.message : opts.errorMessage);
      } finally {
        setBusyId(null);
      }
    },
    [busyId, onError],
  );

  return { busyId, run };
}
