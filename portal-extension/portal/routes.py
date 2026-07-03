"""API 路由 — 登录、分享页 embed-url、SSE 代理。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portal.auth import get_current_user
from portal.gateway import build_iframe_url, proxy_sse_to_ragflow
from portal.password import verify_password

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """登录端点:校验凭据,建立同源会话 cookie(对应验收点 1)。"""
    seed = request.app.state.seed
    user = seed.users_by_username.get(body.username)
    if not user or not user.enabled or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    request.session["user_id"] = user.id
    return {"username": user.username, "is_admin": user.is_admin}


@router.get("/share-pages/{share_page_id}/embed-url")
async def get_embed_url(share_page_id: str, request: Request, user=Depends(get_current_user)):
    """返回 iframe URL(含 auth=T_short,不含真实 beta Token)。

    对应验收点 2:iframe URL 含 auth=T_short,不含真实 beta Token。
    """
    seed = request.app.state.seed
    share_page = seed.share_pages_by_id.get(share_page_id)
    if not share_page or not share_page.enabled:
        raise HTTPException(status_code=404, detail="分享页不存在或已禁用")
    # Slice 1:校验 admin 对默认分享页有 use 权限(硬编码 grant)
    has_grant = any(
        g.share_page_id == share_page.id
        and g.subject_id == user.id
        and g.permission == "use"
        for g in seed.grants
    )
    if not has_grant:
        raise HTTPException(status_code=403, detail="无权访问该分享页")
    # 签发短期 T_short(内存存储,5 分钟过期)
    settings = request.app.state.settings
    token_store = request.app.state.token_store
    t_short = token_store.issue(user.id, share_page.id, settings.t_short_ttl_seconds)
    iframe_url = build_iframe_url(
        settings.ragflow_host, share_page.ragflow_resource_id, t_short
    )
    return {
        "iframe_url": iframe_url,
        "share_page_id": share_page.id,
        "expires_in": settings.t_short_ttl_seconds,
    }


@router.post("/proxy/chatbots/{dialog_id}/completions")
async def proxy_chatbot_completions(dialog_id: str, request: Request):
    """SSE 代理:校验 T_short → 用 beta Token 调 RAGFlow bot_api → 流式回传。

    对应验收点 4(无效/过期 T_short → 401)与验收点 6(beta Token 调 RAGFlow SSE)。
    真实 beta Token 只在网关→RAGFlow 这一跳出现,绝不返回浏览器。
    """
    return await proxy_sse_to_ragflow(request, dialog_id)
