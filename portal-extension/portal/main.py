"""FastAPI 应用入口 — 门户 + 嵌入访问网关。

启动:
  uvicorn portal.main:app --port 8000 --reload

Slice 1:最小可登录的分享页访问骨架(对应 ISSUES.md Issue 1)。
RAGFlow 侧无任何修改;真实 beta Token 全程不离开网关服务端。
"""
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from portal.config import load_settings
from portal.gateway import TokenStore
from portal.models import build_seed_data
from portal.routes import router


def create_app() -> FastAPI:
    """构造 FastAPI 应用:挂载会话中间件、硬编码数据、令牌表、路由。"""
    settings = load_settings()
    app = FastAPI(title="RAGFlow 权限门户", version="0.1.0")
    # 同源 HTTP-only 签名会话 cookie
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    # 硬编码数据、配置、内存令牌表挂到 app.state,供路由读取
    app.state.settings = settings
    app.state.seed = build_seed_data(settings)
    app.state.token_store = TokenStore()
    app.include_router(router)
    return app


# uvicorn portal.main:app 直接引用的模块级实例
app = create_app()
