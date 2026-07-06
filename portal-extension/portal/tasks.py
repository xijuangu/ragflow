"""后台运维任务 — Slice 12 双删重试定时任务。

实现选型(见 ISSUES.md Issue 12 与 PRD.md Slice 12 决策说明):
  使用 ``asyncio.create_task`` + ``asyncio.sleep`` 循环(在 main.py 的 FastAPI
  startup hook 中启动),而非 APScheduler。理由:
    1. 无新依赖(APScheduler 需加依赖,且其高级调度能力对本场景过重)。
    2. FastAPI 已是 async 框架,天然有 event loop;5 分钟级别精度不需要 APScheduler。
    3. 生命周期清晰:startup 创建 task,shutdown 取消 task(无残留)。
    4. 异常隔离:单个 session 处理失败不崩溃循环(见 ``retry_delete_pending_sessions``)。

行为(对应 ISSUES.md Issue 12 验收点 1-3):
  - 查询 ``SessionStore.list_pending_deletion``(``deleted_at`` 非空的会话)。
  - 对每个会话调 ``delete_session_via_ragflow``(RAGFlow DELETE 端点):
      * 成功 → 门户侧硬删除(``SessionStore.delete``),无残留。
      * 失败 → 保留 ``deleted_at`` 标记,下次定时任务再试(不阻塞其他会话)。
  - 配置间隔走环境变量 ``RETRY_DELETE_INTERVAL_SECONDS``(默认 300 = 5 分钟);
    ``<=0`` 禁用定时任务(管理员仍可手动触发 ``POST /admin/sessions/{id}/retry-delete``)。
"""

import asyncio
import logging

from portal.gateway import delete_session_via_ragflow
from portal.models import SessionStore

logger = logging.getLogger(__name__)


async def retry_delete_pending_sessions(settings, session_store: SessionStore) -> None:
    """单次执行:重试所有 ``deleted_at`` 非空的会话的双删。

    对每个待重试会话调 RAGFlow DELETE:
      - 成功 → 门户侧硬删除(``session_store.delete``)。
      - 失败(任何异常)→ 保留 ``deleted_at`` 标记,下次再试;记 ``logger.warning`` 供审计。

    异常隔离:单个会话处理失败不阻塞其他会话(捕获所有异常,记 warning 后继续)。
    本函数不抛异常(除非 ``list_pending_deletion`` 自身出错,那是 DB 问题需暴露)。
    """
    pending = session_store.list_pending_deletion()
    if not pending:
        return  # 空跑:不调 RAGFlow
    for owner in pending:
        try:
            await delete_session_via_ragflow(settings, owner.ragflow_resource_id, owner.session_id)
        except Exception as e:
            # RAGFlow 失败:保留 deleted_at 标记,下次再试;不阻塞其他会话
            logger.warning(
                "双删重试任务:RAGFlow 删除会话失败(保留待下次重试),session_id=%s dialog_id=%s error=%s",
                owner.session_id,
                owner.ragflow_resource_id,
                e,
            )
            continue
        # RAGFlow 成功 → 门户侧硬删除(无残留)
        session_store.delete(owner.session_id)
        logger.info(
            "双删重试任务:RAGFlow 删除成功,门户侧硬删除 session_id=%s",
            owner.session_id,
        )


async def _retry_delete_loop(settings, session_store: SessionStore, interval_seconds: int) -> None:
    """定时任务循环:每 ``interval_seconds`` 秒执行一次 ``retry_delete_pending_sessions``。

    首次执行立即开始(不等间隔),之后每次间隔 ``interval_seconds`` 秒。
    被 ``asyncio.CancelledError`` 取消时优雅退出(shutdown 场景)。
    """
    try:
        while True:
            try:
                await retry_delete_pending_sessions(settings, session_store)
            except Exception as e:
                # 单次执行不应抛异常(retry_delete_pending_sessions 内已捕获),
                # 但 DB 故障等极端情况仍需保护循环不崩溃。
                logger.exception("双删重试任务循环异常(继续运行): %s", e)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("双删重试任务被取消(shutdown),退出循环")
        raise
