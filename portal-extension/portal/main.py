"""FastAPI 应用入口 — 门户 + 嵌入访问网关。

启动:
  uvicorn portal.main:app --port 8000 --reload

Slice 1:最小可登录的分享页访问骨架(对应 ISSUES.md Issue 1)。
Slice 2:session_id 捕获与归属绑定 + 历史恢复(对应 ISSUES.md Issue 2)。
RAGFlow 侧仅在 bot_api.py 加 GET 端点;真实 beta Token 全程不离开网关服务端。
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from portal.config import load_settings
from portal.gateway import TokenStore
from portal.models import AuditStore, SessionStore, build_seed_data
from portal.routes import router


def create_app() -> FastAPI:
    """构造 FastAPI 应用:挂载会话中间件、硬编码数据、令牌表、会话表、审计表、路由。"""
    settings = load_settings()
    app = FastAPI(title="RAGFlow 权限门户", version="0.2.0")
    # 同源 HTTP-only 签名会话 cookie
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    # PRD D10:同源嵌入 — 所有响应加 X-Frame-Options: SAMEORIGIN,
    # 阻止分享页被任意外部站点 iframe 规避门户登录态。
    @app.middleware("http")
    async def enforce_sameorigin_frame(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    # 硬编码数据、配置、内存令牌表、内存会话表挂到 app.state,供路由读取
    app.state.settings = settings
    app.state.seed = build_seed_data(settings)
    app.state.token_store = TokenStore()
    # Slice 2:chat_session_owner 内存表(Slice 4 才上 DB)
    app.state.session_store = SessionStore()
    # Slice 6:audit_log 内存表(永久保留,无 TTL/自动清理,PR D8b)
    app.state.audit_store = AuditStore()
    app.include_router(router)
    # Slice 9:静态托管前端 SPA(放在路由注册之后,html=True 兜底 SPA 路由)。
    # 只有 frontend/dist 存在时才挂载(开发时 Vite dev server 不需要此挂载)。
    _frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if _frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
    return app


# uvicorn portal.main:app 直接引用的模块级实例
app = create_app()
