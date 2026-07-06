"""FastAPI 应用入口 — 门户 + 嵌入访问网关。

启动:
  uvicorn portal.main:app --port 8000 --reload

Slice 1:最小可登录的分享页访问骨架(对应 ISSUES.md Issue 1)。
Slice 2:session_id 捕获与归属绑定 + 历史恢复(对应 ISSUES.md Issue 2)。
RAGFlow 侧仅在 bot_api.py 加 GET 端点;真实 beta Token 全程不离开网关服务端。

Slice 8:DB 持久化迁移(内存存储 → SQLAlchemy)。
  - 启动时根据 PORTAL_DB_URL 创建 engine(SQLite/MySQL 由 URL scheme 决定)。
  - init_db 创建 7 张表(idempotent,可重复执行)。
  - build_seed_data 从 DB 读 seed,不存在则写入(idempotent)。
  - TokenStore 保留内存(T_short 5min 过期 + 可撤销,重启失效可接受,
    用户重新登录获取新 T_short;见 gateway.py 文档说明)。

Slice 12:双删重试定时任务(asyncio.create_task + asyncio.sleep 循环)。
  - lifespan startup 启动 ``_retry_delete_loop`` 后台 task。
  - lifespan shutdown 取消 task(优雅退出,无残留)。
  - 间隔走 ``settings.retry_delete_interval_seconds``(默认 300s,<=0 禁用)。
  - 选型理由见 ``portal/tasks.py`` 模块文档。
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from portal.config import load_settings
from portal.db import create_session_maker, init_db
from portal.gateway import IPRateLimiter, TokenStore
from portal.models import AuditStore, SessionStore, build_seed_data
from portal.routes import router
from portal.tasks import _retry_delete_loop

logger = logging.getLogger(__name__)


def _create_engine_from_url(db_url: str):
    """根据 DB URL 创建 engine。

    SQLite in-memory(`sqlite://` 或 `sqlite:///:memory:`)用 StaticPool
    共享连接,保证 :memory: 跨 session 可见;其他方言(MySQL 等)用默认 pool。
    """
    if db_url.startswith("sqlite"):
        # SQLite:check_same_thread=False 让 FastAPI 多线程也能用;
        # in-memory 必须 StaticPool 共享同一连接(否则每连接独立 DB)
        is_memory = db_url in ("sqlite://", "sqlite:///:memory:")
        return create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if is_memory else None,
            future=True,
        )
    # MySQL/其他:默认连接池
    return create_engine(db_url, future=True, pool_pre_ping=True)


def create_app() -> FastAPI:
    """构造 FastAPI 应用:挂载会话中间件、DB engine、令牌表、会话表、审计表、路由。"""
    settings = load_settings()

    # Slice 8:创建 DB engine + 初始化表 + session_maker(在 lifespan 之外创建,
    # 保证 app.state 在路由注册前就绪;startup 阶段启动后台 task)
    engine = _create_engine_from_url(settings.portal_db_url)
    init_db(engine)  # idempotent:表已存在则跳过
    session_maker = create_session_maker(engine)

    # Slice 12:lifespan context manager 替代 deprecated 的 @app.on_event
    # startup:启动双删重试定时任务;shutdown:取消 task(优雅退出,无残留)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # startup
        start_retry_delete_task(app)
        try:
            yield
        finally:
            # shutdown(无论正常退出或异常都取消 task,避免残留)
            await stop_retry_delete_task(app)

    app = FastAPI(title="RAGFlow 权限门户", version="0.2.0", lifespan=lifespan)
    # 同源 HTTP-only 签名会话 cookie
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    # PRD D10:同源嵌入 — 所有响应加 X-Frame-Options: SAMEORIGIN,
    # 阻止分享页被任意外部站点 iframe 规避门户登录态。
    # Slice 16:widget 场景需跨域嵌入,对 /widget/* 路径改用 CSP frame-ancestors
    # 允许跨域(由 WIDGET_FRAME_ANCESTORS 配置,默认 *),并跳过 X-Frame-Options
    # (X-Frame-Options 与 CSP frame-ancestors 同时存在时浏览器行为不一致,
    # 故 widget 路径只发 CSP 不发 XFO;其他路径保持 XFO: SAMEORIGIN)。
    @app.middleware("http")
    async def enforce_sameorigin_frame(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/widget/"):
            # widget 页面:CSP frame-ancestors 允许跨域嵌入,不设 X-Frame-Options
            response.headers["Content-Security-Policy"] = f"frame-ancestors {settings.widget_frame_ancestors};"
        else:
            # 其他页面:保持 X-Frame-Options: SAMEORIGIN(D10 同源嵌入)
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    # 配置、DB 后端存储、内存令牌表挂到 app.state,供路由读取
    app.state.settings = settings
    app.state.db_engine = engine
    app.state.session_maker = session_maker
    # build_seed_data 启动时 idempotent 写入(admin/user2/默认分享页/grant)
    app.state.seed = build_seed_data(settings, session_maker)
    # TokenStore 保留内存(T_short 短命 + 可撤销,重启失效可接受)
    app.state.token_store = TokenStore()
    # chat_session_owner DB 持久化(进程重启后会话归属不丢)
    app.state.session_store = SessionStore(session_maker)
    # audit_log DB 持久化(永久保留,PR D8b)
    app.state.audit_store = AuditStore(session_maker)
    # Slice 15:公开分享页 IP 限流器(内存滑动窗口,每 IP 每分钟 N 次)
    app.state.rate_limiter = IPRateLimiter(settings.public_rate_limit_per_min)
    app.include_router(router)
    # Slice 9:静态托管前端 SPA(放在路由注册之后,html=True 兜底 SPA 路由)。
    # 只有 frontend/dist 存在时才挂载(开发时 Vite dev server 不需要此挂载)。
    _frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if _frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")

    return app


def start_retry_delete_task(app: FastAPI) -> None:
    """启动双删重试定时任务(Slice 12)。

    在 FastAPI startup hook 中调用。若 ``settings.retry_delete_interval_seconds <= 0``
    则不启动(禁用定时任务,管理员仍可手动触发 retry-delete 端点)。
    task 引用存到 ``app.state.retry_delete_task`` 供 shutdown 取消。
    """
    import asyncio

    settings = app.state.settings
    interval = settings.retry_delete_interval_seconds
    if interval <= 0:
        logger.info("双删重试定时任务已禁用(retry_delete_interval_seconds=%s)<=0", interval)
        return
    task = asyncio.create_task(_retry_delete_loop(settings, app.state.session_store, interval))
    app.state.retry_delete_task = task
    logger.info("双删重试定时任务已启动,间隔 %s 秒", interval)


async def stop_retry_delete_task(app: FastAPI) -> None:
    """取消双删重试定时任务(Slice 12,在 FastAPI shutdown hook 中调用)。

    取消 task 后等待其优雅退出(``_retry_delete_loop`` 捕获 CancelledError 退出循环)。
    若 task 已完成(自然结束或未启动),则跳过。
    """
    task = getattr(app.state, "retry_delete_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except BaseException:
        # CancelledError(Python 3.8+ 是 BaseException 子类)或其他异常都不影响 shutdown
        pass
    app.state.retry_delete_task = None
    logger.info("双删重试定时任务已停止")


# uvicorn portal.main:app 直接引用的模块级实例
app = create_app()
