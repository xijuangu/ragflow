"""API 路由 — 登录、分享页 embed-url、SSE 代理、session 预创建/列表/恢复。"""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portal.auth import get_current_user
from portal.gateway import (
    build_iframe_url,
    fetch_session_history_via_ragflow,
    precreate_session_via_ragflow,
    proxy_sse_to_ragflow,
)
from portal.models import SharePage
from portal.password import verify_password

router = APIRouter()

T = TypeVar("T")


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


def _check_share_page_access(seed, share_page_id: str, user) -> SharePage:
    """校验分享页存在且当前用户有 use 权限;返回 share_page 对象。

    校验链步骤 1(get_current_user 已做)+ 步骤 2(grant 存在性,Slice 3 提取到 SeedData.has_use_grant)。
    撤销授权后此校验失败 → 403(刷新 iframe 加载时拒绝签发新 T_short)。
    Slice 4 启用 group subject_type。
    """
    share_page = seed.share_pages_by_id.get(share_page_id)
    if not share_page or not share_page.enabled:
        raise HTTPException(status_code=404, detail="分享页不存在或已禁用")
    if not seed.has_use_grant(share_page.id, user.id):
        raise HTTPException(status_code=403, detail="无权访问该分享页")
    return share_page


async def _invoke_upstream(fn: Callable[[], Awaitable[T]], action: str) -> T:
    """统一包装上游 RAGFlow 调用的异常处理(HTTPException 透传,其他异常 → 502)。

    消除 precreate_session / resume_session 中重复的 try/except 块。
    """
    try:
        return await fn()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{action}: {e}")


@router.get("/share-pages/{share_page_id}/embed-url")
async def get_embed_url(share_page_id: str, request: Request, user=Depends(get_current_user)):
    """返回 iframe URL(含 auth=T_short,不含真实 beta Token)。

    对应验收点 2:iframe URL 含 auth=T_short,不含真实 beta Token。
    """
    seed = request.app.state.seed
    share_page = _check_share_page_access(seed, share_page_id, user)
    # 签发短期 T_short(内存存储,5 分钟过期)
    settings = request.app.state.settings
    token_store = request.app.state.token_store
    t_short = token_store.issue(user.id, share_page.id, settings.t_short_ttl_seconds)
    iframe_url = build_iframe_url(settings.ragflow_host, share_page.ragflow_resource_id, t_short)
    return {
        "iframe_url": iframe_url,
        "share_page_id": share_page.id,
        "expires_in": settings.t_short_ttl_seconds,
    }


# ---------------------------------------------------------------------------
# Slice 2:session 预创建 / 列表 / 恢复(对应 ISSUES.md Issue 2)
# ---------------------------------------------------------------------------


@router.post("/share-pages/{share_page_id}/sessions")
async def precreate_session(share_page_id: str, request: Request, user=Depends(get_current_user)):
    """预创建 session:调 RAGFlow 创建空 API4Conversation,绑定到当前用户。

    对应验收点 1:门户预创建 session 并在 chat_session_owner 绑定到当前用户。
    对应验收点 2:iframe URL 含 session_id 参数(解决 RAGFlow 双步行为)。

    流程(方案 B,见 NOTES.md「首次对话双步行为」):
      1. 网关用 beta Token 调 RAGFlow POST /completions(question="",stream=true),
         从 SSE 首帧解析 session_id。
      2. 立即写入 chat_session_owner 绑定到当前用户(portal_user_id NOT NULL)。
      3. 签发 T_short,构造含 session_id 的 iframe URL 返回。
    """
    seed = request.app.state.seed
    share_page = _check_share_page_access(seed, share_page_id, user)
    settings = request.app.state.settings
    # 调 RAGFlow 预创建 session(网关用 beta Token,绝不返回浏览器)
    session_id = await _invoke_upstream(
        lambda: precreate_session_via_ragflow(settings, share_page.ragflow_resource_id),
        "预创建 session 失败",
    )
    # 立即绑定到当前用户(chat_session_owner.portal_user_id NOT NULL)
    request.app.state.session_store.bind(
        session_id=session_id,
        share_page_id=share_page.id,
        portal_user_id=user.id,
        ragflow_resource_id=share_page.ragflow_resource_id,
    )
    # 签发 T_short 并构造含 session_id 的 iframe URL
    token_store = request.app.state.token_store
    t_short = token_store.issue(user.id, share_page.id, settings.t_short_ttl_seconds)
    iframe_url = build_iframe_url(settings.ragflow_host, share_page.ragflow_resource_id, t_short, session_id)
    return {
        "session_id": session_id,
        "iframe_url": iframe_url,
        "share_page_id": share_page.id,
    }


@router.get("/share-pages/{share_page_id}/sessions")
async def list_sessions(share_page_id: str, request: Request, user=Depends(get_current_user)):
    """我的会话列表:返回当前用户在该分享页下的 session 列表。

    对应验收点 4:用户能在「我的会话」看到该 session(标题、最后活跃时间)。
    基础隔离:只返回 portal_user_id 匹配的记录(用户看不到他人的 session)。
    """
    seed = request.app.state.seed
    _check_share_page_access(seed, share_page_id, user)
    sessions = request.app.state.session_store.list_for_user(user.id, share_page_id)
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at,
                "last_active_at": s.last_active_at,
            }
            for s in sessions
        ]
    }


@router.get("/share-pages/{share_page_id}/sessions/{session_id}")
async def resume_session(share_page_id: str, session_id: str, request: Request, user=Depends(get_current_user)):
    """重新打开/恢复 session:调 RAGFlow GET 端点取回消息 + 引用。

    对应验收点 5:完整恢复消息正文、引用片段(chunks)、引用标记、
    文档定位(doc_aggs 的 document_id 可定位 PDF 预览)。

    基础归属隔离(Slice 2 已有归属表,Slice 3 加完整校验链):
      校验 chat_session_owner.portal_user_id == 当前用户;不匹配 → 403。
    """
    seed = request.app.state.seed
    share_page = _check_share_page_access(seed, share_page_id, user)
    # 归属校验:session 必须归属当前用户
    owner = request.app.state.session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner.portal_user_id != user.id:
        # 基础隔离:用户只能查自己的 session(Slice 3 加完整校验链)
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if owner.share_page_id != share_page_id:
        raise HTTPException(status_code=403, detail="会话不属于该分享页")
    # 调 RAGFlow GET 端点取回消息 + 引用
    settings = request.app.state.settings
    history = await _invoke_upstream(
        lambda: fetch_session_history_via_ragflow(settings, share_page.ragflow_resource_id, session_id),
        "取回会话失败",
    )
    # 补充门户侧标题(chat_session_owner.title 为列表显示主源)
    if isinstance(history, dict):
        history = {**history, "title": owner.title}
    return history


@router.post("/api/v1/chatbots/{dialog_id}/completions")
async def proxy_chatbot_completions(dialog_id: str, request: Request):
    """SSE 代理:校验 T_short → 用 beta Token 调 RAGFlow bot_api → 流式回传。

    路径与 RAGFlow 前端原生 SSE 调用路径一致(同源部署下 iframe 内前端发起的
    `/api/v1/chatbots/<dialog_id>/completions` 天然走网关,无需额外反向代理配置)。
    对应验收点 4(无效/过期 T_short → 401)与验收点 6(beta Token 调 RAGFlow SSE)。
    真实 beta Token 只在网关→RAGFlow 这一跳出现,绝不返回浏览器。

    Slice 2 验收点 7(基础归属隔离):若请求体含 session_id,校验其归属当前 T_short
    持有用户,不匹配 → 403。last_active_at 仅在流成功完成后更新。

    Slice 3 完整校验链(每次请求都执行):同源 cookie + grant 存在 + T_short 有效 +
    session 归属 + dialog_id 一致,任一失败 → 403/401。详见 proxy_sse_to_ragflow 文档。
    """
    return await proxy_sse_to_ragflow(request, dialog_id)


# ---------------------------------------------------------------------------
# Slice 3:撤销授权(对应 ISSUES.md Issue 3 撤销机制)
# ---------------------------------------------------------------------------


def _require_admin(user) -> None:
    """校验当前用户是管理员(撤销授权 API 管理员专用)。"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")


@router.delete("/share-pages/{share_page_id}/grants/{subject_type}/{subject_id}")
async def revoke_grant(
    share_page_id: str,
    subject_type: str,
    subject_id: str,
    request: Request,
    user=Depends(get_current_user),
):
    """撤销授权:删除 grant + 批量吊销已签发的 T_short(管理员专用)。

    对应 ISSUES.md Issue 3 撤销机制:
      - 删除 share_page_grant 行(后续网关校验 grant 不存在 → 403)。
      - 吊销已签发给该用户该分享页的所有 T_short(内存令牌表标记 revoked=true)。
      - 后续同 T_short 请求 → 403/401(立即失效)。
      - 历史会话(chat_session_owner)保留,不删除(管理员仍可查)。

    RAGFlow 侧 beta Token 是租户级的无法按分享页撤销,撤销完全由网关实现。
    Slice 3 只支持 subject_type='user';Slice 4 才启用 group。
    """
    _require_admin(user)
    if subject_type != "user":
        raise HTTPException(status_code=400, detail="一期仅支持 subject_type=user")
    seed = request.app.state.seed
    # 分享页必须存在
    if share_page_id not in seed.share_pages_by_id:
        raise HTTPException(status_code=404, detail="分享页不存在")
    # 删除 grant(不存在 → 404)
    if not seed.revoke_grant(share_page_id, subject_id):
        raise HTTPException(status_code=404, detail="授权记录不存在")
    # 批量吊销已签发的 T_short(同用户 + 同分享页)
    revoked_count = request.app.state.token_store.revoke_tokens_for_user_share_page(subject_id, share_page_id)
    return {
        "revoked": True,
        "share_page_id": share_page_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "tokens_revoked": revoked_count,
    }
