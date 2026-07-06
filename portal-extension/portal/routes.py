"""API 路由 — 登录、分享页 embed-url、SSE 代理、session 预创建/列表/恢复/重命名/删除、CRUD。

Slice 4 新增:
  - 管理员 CRUD(/admin/users、/admin/groups、/admin/share-pages、grants)。
  - 普通用户 GET /share-pages(只看自己被授权的)。
  - 登录失败明确错误(用户未注册 / 密码错误 / 账号已禁用)。
  - 撤销授权支持 user 与 group 两种 subject_type。

Slice 5 新增:
  - PATCH /share-pages/{id}/sessions/{sid} — 用户重命名自己的会话(同步:RAGFlow 失败则 502,不更新门户 title)。
  - DELETE /share-pages/{id}/sessions/{sid} — 用户删除自己的会话(双删:RAGFlow 失败标记 deleted_at)。
  - DELETE /admin/share-pages/{id}/sessions/{sid} — 管理员删除任意会话(双删,校验 share_page_id 一致性)。
  - DELETE /admin/users/{id} — 管理员硬删除用户(级联硬删所有会话,无孤儿;RAGFlow 失败记日志)。

Slice 6 新增:
  - 审计日志写入点:8 类敏感操作(login_success/failure、grant_create/revoke、session_delete、
    session_view_elevated、user_enable/disable)。
  - GET /admin/sessions — 管理员列出所有会话(按用户/分享页/时间过滤,返回元数据)。
  - GET /admin/sessions/pending-deletion — 管理员查看待重试删除的会话。
  - GET /admin/sessions/{session_id} — 管理员查看会话(默认元数据;?elevated=true 写审计+返回正文)。
  - POST /admin/sessions/{session_id}/retry-delete — 管理员手动触发清理(调 RAGFlow DELETE + 删门户记录)。
  - GET /admin/audit-logs — 管理员查看审计日志(按 action/actor/时间过滤)。
"""

import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from portal.auth import LoginError, authenticate, get_current_user, require_admin
from portal.gateway import (
    build_iframe_url,
    delete_session_via_ragflow,
    fetch_session_history_via_ragflow,
    precreate_session_via_ragflow,
    proxy_sse_to_ragflow,
    rename_session_via_ragflow,
)
from portal.models import Permission, PortalGroup, PortalUser, SharePage, SharePageGrant, SubjectType
from portal.password import hash_password

router = APIRouter()

T = TypeVar("T")


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str


class CreateGroupRequest(BaseModel):
    name: str


class AddGroupMemberRequest(BaseModel):
    user_id: str


class CreateSharePageRequest(BaseModel):
    name: str
    ragflow_resource_id: str


class UpdateEnabledRequest(BaseModel):
    enabled: bool


class RenameSessionRequest(BaseModel):
    title: str


class CreateGrantRequest(BaseModel):
    subject_type: SubjectType  # user / group — Literal 触发 Pydantic 422 校验
    subject_id: str
    permission: Permission = "use"


# ---------------------------------------------------------------------------
# 序列化辅助(避免 password_hash 进入响应)
# ---------------------------------------------------------------------------


def _user_to_dict(user: PortalUser) -> dict:
    """用户 → 响应 dict(剔除 password_hash,避免泄露)。"""
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": user.is_admin,
        "enabled": user.enabled,
        "created_at": user.created_at,
    }


def _group_to_dict(group: PortalGroup) -> dict:
    """用户组 → 响应 dict。"""
    return {
        "id": group.id,
        "name": group.name,
        "created_at": group.created_at,
    }


def _share_page_to_dict(page: SharePage) -> dict:
    """分享页 → 响应 dict。"""
    return {
        "id": page.id,
        "name": page.name,
        "ragflow_type": page.ragflow_type,
        "ragflow_resource_id": page.ragflow_resource_id,
        "embed_type": page.embed_type,
        "enabled": page.enabled,
        "created_at": page.created_at,
    }


def _grant_to_dict(grant: SharePageGrant) -> dict:
    """授权 → 响应 dict。"""
    return {
        "share_page_id": grant.share_page_id,
        "subject_type": grant.subject_type,
        "subject_id": grant.subject_id,
        "permission": grant.permission,
    }


def _audit_log_to_dict(log) -> dict:
    """审计日志 → 响应 dict(Slice 6)。meta_json 解析回 dict 便于前端读取。"""
    try:
        meta = json.loads(log.meta_json) if log.meta_json else None
    except (ValueError, TypeError):
        meta = None
    return {
        "id": log.id,
        "actor_user_id": log.actor_user_id,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "at": log.at,
        "meta": meta,
    }


def _session_owner_to_metadata_dict(owner) -> dict:
    """会话归属记录 → 管理员元数据 dict(不含正文,Slice 6 验收点 3)。

    含 message_count 字段(spec 要求「元数据含消息数」):
    预创建时为 0,恢复会话(GET history)后用 len(messages) 更新;
    SSE 代理后可能滞后,管理员想看准确数用 elevated 查正文。
    """
    return {
        "session_id": owner.session_id,
        "title": owner.title,
        "portal_user_id": owner.portal_user_id,
        "share_page_id": owner.share_page_id,
        "ragflow_resource_id": owner.ragflow_resource_id,
        "created_at": owner.created_at,
        "last_active_at": owner.last_active_at,
        "deleted_at": owner.deleted_at,
        "message_count": owner.message_count,
    }


def _audit(
    request: Request,
    user,
    action: str,
    target_type: str,
    target_id: str,
    *,
    actor_user_id: str | None = None,
    **meta,
) -> None:
    """记录审计日志的统一入口(消除 audit_store.record 调用重复)。

    默认 actor_user_id 取当前登录用户 ``user.id``;
    login_failure 等无当前用户的场景(user=None)需显式传 ``actor_user_id``
    (如 attempted.id 或 username)。
    meta 通过 ``**kwargs`` 传入,内部组装为 dict,减少各调用点重复构造 dict。
    """
    if actor_user_id is None:
        if user is None:
            raise ValueError("无当前用户时必须显式传 actor_user_id")
        actor_user_id = user.id
    request.app.state.audit_store.record(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        meta=meta if meta else None,
    )


# ---------------------------------------------------------------------------
# 登录(Slice 1,Slice 4 改进明确错误,Slice 6 加审计)
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """登录端点:校验凭据,建立同源会话 cookie(对应验收点 1)。

    Slice 4 改进:登录失败明确错误(用户未注册 / 密码错误 / 账号已禁用)。
    Slice 6 加审计:登录成功记 login_success,登录失败记 login_failure(PR D7b)。
    """
    seed = request.app.state.seed
    try:
        user = await authenticate(seed, body.username, body.password)
    except LoginError as e:
        # 登录失败审计:actor 用 attempted user id(若用户存在)或 username(若不存在)
        attempted = seed.get_user_by_username(body.username)
        actor_id = attempted.id if attempted else body.username
        _audit(
            request,
            None,
            "login_failure",
            "user",
            actor_id,
            actor_user_id=actor_id,
            username=body.username,
            reason=e.detail,
        )
        raise
    # 登录成功审计
    _audit(request, user, "login_success", "user", user.id, username=user.username)
    request.session["user_id"] = user.id
    return {"username": user.username, "is_admin": user.is_admin}


# ---------------------------------------------------------------------------
# 分享页访问(普通用户)
# ---------------------------------------------------------------------------


def _check_share_page_access(seed, share_page_id: str, user) -> SharePage:
    """校验分享页存在且当前用户有 use 权限;返回 share_page 对象。

    校验链步骤 1(get_current_user 已做)+ 步骤 2(grant 存在性,Slice 4 升级
    has_use_grant 支持 user + group)。撤销授权后此校验失败 → 403。
    """
    # Slice 8:经 SeedData 公开 API 查分享页(原直接访问 share_pages_by_id dict,现 DB 后端)
    share_page = seed.get_share_page(share_page_id)
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


async def _dual_delete_session(settings, store, dialog_id: str, session_id: str) -> bool:
    """单会话双删协调:RAGFlow DELETE 成功 → 门户硬删除;失败 → 标记 deleted_at 待重试。

    用于 delete_session / admin_delete_session 的双删策略(单会话场景)。
    与 ``SessionStore.cascade_delete_for_user`` 的区别:级联删除用户时 RAGFlow 失败
    也硬删除门户侧(用户已不存在无法重试,避免孤儿);此处单会话删除失败时保留
    门户侧记录标记 deleted_at,供后台重试任务后续清理。

    返回 True 表示 RAGFlow 删除成功(门户侧已硬删除);
    返回 False 表示 RAGFlow 失败(门户侧已标记 deleted_at,记录保留待重试)。
    """
    try:
        await delete_session_via_ragflow(settings, dialog_id, session_id)
        store.delete(session_id)
        return True
    except HTTPException:
        store.mark_deleted(session_id)
        return False


@router.get("/share-pages")
async def list_my_share_pages(request: Request, user=Depends(get_current_user)):
    """普通用户:列出自己被授权的分享页(直接授权或所属组授权,验收点 5)。

    管理员可调用此端点(返回其被授权的分享页);列出全部分享页用 GET /admin/share-pages。
    """
    seed = request.app.state.seed
    pages = seed.list_share_pages_for_user(user.id)
    return {"share_pages": [_share_page_to_dict(p) for p in pages]}


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
    # 恢复会话时用 len(messages) 更新 message_count(简化实现:字段存在,恢复后准确)
    # Slice 8:DB 后端需经公开 API update_message_count 持久化(原直接改 dataclass 属性不生效)
    if isinstance(history, dict):
        messages = history.get("messages", [])
        if owner.message_count != len(messages):
            request.app.state.session_store.update_message_count(session_id, len(messages))
    # 补充门户侧标题(chat_session_owner.title 为列表显示主源)
    if isinstance(history, dict):
        history = {**history, "title": owner.title}
    return history


# ---------------------------------------------------------------------------
# Slice 5:会话重命名 / 删除(双删)
# ---------------------------------------------------------------------------


@router.patch("/share-pages/{share_page_id}/sessions/{session_id}")
async def rename_session(
    share_page_id: str,
    session_id: str,
    body: RenameSessionRequest,
    request: Request,
    user=Depends(get_current_user),
):
    """重命名会话(对应 Slice 5 验收点 1:用户重命名自己的会话)。

    同步策略(与删除的双删策略不同):RAGFlow PATCH 成功才更新门户 title;
    失败则抛 502(不吞异常,让用户感知失败),门户 title 不更新
    (保证 RAGFlow 侧 name 与门户 title 同步,不出现一侧更新一侧未更新的不一致)。
    归属校验:session 必须归属当前用户且属于该分享页;不匹配 → 403/404。
    """
    seed = request.app.state.seed
    share_page = _check_share_page_access(seed, share_page_id, user)
    # 归属校验:session 必须归属当前用户
    owner = request.app.state.session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner.portal_user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if owner.share_page_id != share_page_id:
        raise HTTPException(status_code=403, detail="会话不属于该分享页")
    # 同步策略:RAGFlow PATCH 成功才更新门户 title;失败抛 502(不吞异常,不更新门户 title)
    # rename_session_via_ragflow 在非 200 时已抛 HTTPException(502),此处直接透传
    settings = request.app.state.settings
    await rename_session_via_ragflow(settings, share_page.ragflow_resource_id, session_id, body.title)
    # RAGFlow 成功 → 更新门户 title(两侧同步)
    request.app.state.session_store.rename(session_id, body.title)
    return {"session_id": session_id, "title": body.title}


@router.delete("/share-pages/{share_page_id}/sessions/{session_id}")
async def delete_session(share_page_id: str, session_id: str, request: Request, user=Depends(get_current_user)):
    """删除会话(对应 Slice 5 验收点 3-4:用户删除自己的会话,双删协调)。

    双删事务策略(``_dual_delete_session``):
      - 先调 RAGFlow DELETE;成功 → 门户侧硬删除 chat_session_owner。
      - RAGFlow 失败 → 标记 deleted_at(记录保留待重试),返回 200(不暴露失败)。
    归属校验:session 必须归属当前用户且属于该分享页;不匹配 → 403/404。

    Slice 6 加审计:RAGFlow 删除成功(门户侧已硬删除)后记 session_delete 审计日志。
    """
    seed = request.app.state.seed
    share_page = _check_share_page_access(seed, share_page_id, user)
    # 归属校验:session 必须归属当前用户
    owner = request.app.state.session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner.portal_user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if owner.share_page_id != share_page_id:
        raise HTTPException(status_code=403, detail="会话不属于该分享页")
    # 双删:RAGFlow DELETE 成功 → 门户硬删除;失败 → 标记 deleted_at(返回 200 不暴露失败)
    settings = request.app.state.settings
    deleted = await _dual_delete_session(
        settings, request.app.state.session_store, share_page.ragflow_resource_id, session_id
    )
    # Slice 6 审计:仅 RAGFlow 成功(门户侧已硬删除)时记 session_delete
    if deleted:
        _audit(
            request,
            user,
            "session_delete",
            "session",
            session_id,
            share_page_id=share_page_id,
            owner_user_id=owner.portal_user_id,
        )
    return {"session_id": session_id, "deleted": True}


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

    Slice 4:网关 ACL 解析支持 user 与 group 两种 subject_type(has_use_grant 升级)。
    """
    return await proxy_sse_to_ragflow(request, dialog_id)


# ===========================================================================
# Slice 4:管理员 CRUD(均要求 is_admin=true)
# ===========================================================================


# ---------------------------------------------------------------------------
# 用户 CRUD
# ---------------------------------------------------------------------------


@router.post("/admin/users", status_code=201)
async def admin_create_user(body: CreateUserRequest, request: Request, user=Depends(require_admin)):
    """管理员创建用户(用户名 + 邮箱 + 初始密码)。

    重复用户名 → 400。响应不含 password_hash。
    """
    seed = request.app.state.seed
    if seed.get_user_by_username(body.username) is not None:
        raise HTTPException(status_code=400, detail="用户名已存在")
    new_user = seed.create_user(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    return _user_to_dict(new_user)


@router.get("/admin/users")
async def admin_list_users(request: Request, user=Depends(require_admin)):
    """管理员列出所有用户。"""
    seed = request.app.state.seed
    return {"users": [_user_to_dict(u) for u in seed.list_users()]}


@router.get("/admin/users/{user_id}")
async def admin_get_user(user_id: str, request: Request, user=Depends(require_admin)):
    """管理员查用户详情。"""
    seed = request.app.state.seed
    target = seed.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _user_to_dict(target)


@router.patch("/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: UpdateEnabledRequest, request: Request, user=Depends(require_admin)):
    """管理员启用/禁用用户(对应 PRD 用户故事 4-5)。

    禁用后:用户无法登录(明确错误),会话保留,网关拒绝其请求。
    禁用不删会话(与硬删除的区别:禁用走 PATCH,会话保留;硬删除走 DELETE,级联删会话)。

    Slice 6 加审计:启用 → user_enable,禁用 → user_disable(PR D7b)。
    """
    seed = request.app.state.seed
    if not seed.set_user_enabled(user_id, body.enabled):
        raise HTTPException(status_code=404, detail="用户不存在")
    target = seed.get_user(user_id)
    # Slice 6 审计:user_enable / user_disable
    _audit(
        request,
        user,
        "user_enable" if body.enabled else "user_disable",
        "user",
        user_id,
        username=target.username,
    )
    return _user_to_dict(target)


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request, user=Depends(require_admin)):
    """管理员硬删除用户(对应 Slice 5 验收点 6:级联删除 chat_session_owner + RAGFlow API4Conversation,无孤儿)。

    级联策略(``SessionStore.cascade_delete_for_user``,与单会话双删不同):
      - 遍历用户的所有 chat_session_owner(含 deleted_at 标记的,跨分享页)。
      - 每条调 RAGFlow DELETE:**无论成功失败都硬删除门户侧记录**(避免孤儿)。
        理由:用户已不存在,保留 orphan 会话无法后续重试(无用户上下文);
        RAGFlow 侧的 API4Conversation 残留由管理员后续手动清理(脚本/管理界面)。
      - RAGFlow 失败记 ``logger.warning`` 供审计(Slice 6 加端点暴露)。
      - 最后删 portal_user。
    禁用用户不删会话(走 PATCH /admin/users/{id});硬删除才级联删会话。

    Slice 6 加审计:级联删除的每个会话记 session_delete(门户侧已硬删除,无孤儿)。
    """
    seed = request.app.state.seed
    if seed.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    settings = request.app.state.settings
    session_store = request.app.state.session_store
    # 级联删除用户所有会话(RAGFlow 失败也硬删除门户侧,避免孤儿;失败记日志)
    cascade_results = await session_store.cascade_delete_for_user(user_id, settings)
    # Slice 6 审计:每个级联删除的会话记 session_delete(门户侧已硬删除)
    for session_id, _success in cascade_results:
        _audit(
            request,
            user,
            "session_delete",
            "session",
            session_id,
            cascade=True,
            owner_user_id=user_id,
        )
    # 删除用户(无论 RAGFlow 是否失败)
    seed.delete_user(user_id)
    return {"user_id": user_id, "deleted": True}


# ---------------------------------------------------------------------------
# 用户组 CRUD
# ---------------------------------------------------------------------------


@router.post("/admin/groups", status_code=201)
async def admin_create_group(body: CreateGroupRequest, request: Request, user=Depends(require_admin)):
    """管理员创建用户组(对应 PRD 用户故事 8)。"""
    seed = request.app.state.seed
    group = seed.create_group(name=body.name)
    return _group_to_dict(group)


@router.get("/admin/groups")
async def admin_list_groups(request: Request, user=Depends(require_admin)):
    """管理员列出所有用户组(对应 PRD 用户故事 10)。"""
    seed = request.app.state.seed
    groups = []
    for g in seed.list_groups():
        d = _group_to_dict(g)
        # Slice 8:经 SeedData 公开 API 取成员(原直接访问 seed.group_members dict,现 DB 后端)
        members = seed.list_group_members(g.id)
        d["member_count"] = len(members)
        d["members"] = list(members)
        groups.append(d)
    return {"groups": groups}


@router.post("/admin/groups/{group_id}/members")
async def admin_add_group_member(
    group_id: str,
    body: AddGroupMemberRequest,
    request: Request,
    user=Depends(require_admin),
):
    """管理员添加用户到用户组(对应 PRD 用户故事 8)。"""
    seed = request.app.state.seed
    if seed.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    if seed.get_user(body.user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    seed.add_group_member(group_id, body.user_id)
    return {"group_id": group_id, "user_id": body.user_id, "added": True}


@router.delete("/admin/groups/{group_id}/members/{user_id}")
async def admin_remove_group_member(
    group_id: str,
    user_id: str,
    request: Request,
    user=Depends(require_admin),
):
    """管理员从用户组移除用户(对应 PRD 用户故事 9)。"""
    seed = request.app.state.seed
    if seed.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    if not seed.remove_group_member(group_id, user_id):
        raise HTTPException(status_code=404, detail="成员不在该用户组")
    return {"group_id": group_id, "user_id": user_id, "removed": True}


# ---------------------------------------------------------------------------
# 分享页 CRUD
# ---------------------------------------------------------------------------


@router.post("/admin/share-pages", status_code=201)
async def admin_create_share_page(body: CreateSharePageRequest, request: Request, user=Depends(require_admin)):
    """管理员创建分享页(对应 PRD 用户故事 12)。

    embed_type/ragflow_type 一期固定值(D9),不开放选择器。
    """
    seed = request.app.state.seed
    page = seed.create_share_page(name=body.name, ragflow_resource_id=body.ragflow_resource_id)
    return _share_page_to_dict(page)


@router.get("/admin/share-pages")
async def admin_list_share_pages(request: Request, user=Depends(require_admin)):
    """管理员列出所有分享页(对应 PRD 用户故事 14)。"""
    seed = request.app.state.seed
    return {"share_pages": [_share_page_to_dict(p) for p in seed.list_share_pages()]}


@router.patch("/admin/share-pages/{share_page_id}")
async def admin_update_share_page(
    share_page_id: str, body: UpdateEnabledRequest, request: Request, user=Depends(require_admin)
):
    """管理员启用/禁用分享页(对应 PRD 用户故事 13)。"""
    seed = request.app.state.seed
    if not seed.set_share_page_enabled(share_page_id, body.enabled):
        raise HTTPException(status_code=404, detail="分享页不存在")
    return _share_page_to_dict(seed.get_share_page(share_page_id))


@router.delete("/admin/share-pages/{share_page_id}/sessions/{session_id}")
async def admin_delete_session(share_page_id: str, session_id: str, request: Request, user=Depends(require_admin)):
    """管理员删除任意用户的会话(对应 Slice 5 验收点 5:管理员双删,不校验归属)。

    双删策略与普通用户删除一致(``_dual_delete_session``):RAGFlow 成功 → 门户硬删除;
    失败 → 标记 deleted_at。
    校验:share_page_id 存在 + session 属于该分享页(与 rename_session/delete_session
    校验链一致,不匹配 → 403);管理员不校验 portal_user_id 归属(可删任意用户的会话)。

    Slice 6 加审计:RAGFlow 删除成功(门户侧已硬删除)后记 session_delete 审计日志。
    """
    seed = request.app.state.seed
    if seed.get_share_page(share_page_id) is None:
        raise HTTPException(status_code=404, detail="分享页不存在")
    owner = request.app.state.session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 校验 session 属于该分享页(与 rename_session/delete_session 校验链一致)
    if owner.share_page_id != share_page_id:
        raise HTTPException(status_code=403, detail="会话不属于该分享页")
    # 双删:RAGFlow DELETE(管理员不校验 portal_user_id 归属,可删任意用户的会话)
    settings = request.app.state.settings
    deleted = await _dual_delete_session(
        settings, request.app.state.session_store, owner.ragflow_resource_id, session_id
    )
    # Slice 6 审计:仅 RAGFlow 成功(门户侧已硬删除)时记 session_delete
    if deleted:
        _audit(
            request,
            user,
            "session_delete",
            "session",
            session_id,
            share_page_id=share_page_id,
            owner_user_id=owner.portal_user_id,
            admin_initiated=True,
        )
    return {"session_id": session_id, "deleted": True}


# ---------------------------------------------------------------------------
# 授权 CRUD
# ---------------------------------------------------------------------------


def _validate_subject_exists(seed, subject_type: str, subject_id: str) -> None:
    """校验 subject 存在;不存在 → 404。"""
    if subject_type == "user":
        if seed.get_user(subject_id) is None:
            raise HTTPException(status_code=404, detail="用户不存在")
    elif subject_type == "group":
        if seed.get_group(subject_id) is None:
            raise HTTPException(status_code=404, detail="用户组不存在")
    else:
        raise HTTPException(status_code=400, detail="subject_type 必须为 user 或 group")


@router.post("/admin/share-pages/{share_page_id}/grants", status_code=201)
async def admin_create_grant(
    share_page_id: str, body: CreateGrantRequest, request: Request, user=Depends(require_admin)
):
    """管理员把分享页授权给用户或用户组(对应 PRD 用户故事 16-17)。

    subject_type/permission 由 CreateGrantRequest 的 Literal 类型在入口处
    做 Pydantic 422 校验,无需 handler 内手写 if 校验。

    Slice 6 加审计:授权创建后记 grant_create(PR D7b)。
    """
    seed = request.app.state.seed
    if seed.get_share_page(share_page_id) is None:
        raise HTTPException(status_code=404, detail="分享页不存在")
    _validate_subject_exists(seed, body.subject_type, body.subject_id)
    grant = seed.create_grant(share_page_id, body.subject_type, body.subject_id, body.permission)
    # Slice 6 审计:grant_create
    _audit(
        request,
        user,
        "grant_create",
        "grant",
        share_page_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        permission=body.permission,
    )
    return _grant_to_dict(grant)


@router.get("/admin/share-pages/{share_page_id}/grants")
async def admin_list_grants(share_page_id: str, request: Request, user=Depends(require_admin)):
    """管理员列出某分享页的所有授权。"""
    seed = request.app.state.seed
    if seed.get_share_page(share_page_id) is None:
        raise HTTPException(status_code=404, detail="分享页不存在")
    return {"grants": [_grant_to_dict(g) for g in seed.list_grants(share_page_id)]}


@router.delete("/share-pages/{share_page_id}/grants/{subject_type}/{subject_id}")
async def revoke_grant(
    share_page_id: str,
    subject_type: str,
    subject_id: str,
    request: Request,
    user=Depends(require_admin),
):
    """撤销授权:删除 grant + 批量吊销已签发的 T_short(管理员专用)。

    对应 ISSUES.md Issue 3 撤销机制 + Slice 4 启用 group subject_type:
      - 删除 share_page_grant 行(后续网关校验 grant 不存在 → 403)。
      - 吊销已签发给该 subject 的所有 T_short(内存令牌表标记 revoked=true)。
      - subject_type='user':吊销该用户该分享页的 T_short。
      - subject_type='group':吊销该组所有成员该分享页的 T_short(组成员失权)。
      - 历史会话(chat_session_owner)保留,不删除(管理员仍可查)。

    路径保留 Slice 3 的 /share-pages/... 前缀以保证向后兼容(Slice 3 测试无回归);
    管理员校验由 Depends(require_admin) 强制(Slice 4 升级)。
    """
    if subject_type not in ("user", "group"):
        raise HTTPException(status_code=400, detail="subject_type 必须为 user 或 group")
    seed = request.app.state.seed
    # 分享页必须存在(Slice 8:经 SeedData 公开 API 查,原直接访问 share_pages_by_id dict)
    if seed.get_share_page(share_page_id) is None:
        raise HTTPException(status_code=404, detail="分享页不存在")
    # 删除 grant(不存在 → 404)
    if not seed.revoke_grant(share_page_id, subject_type, subject_id):
        raise HTTPException(status_code=404, detail="授权记录不存在")
    # 批量吊销已签发的 T_short
    token_store = request.app.state.token_store
    if subject_type == "user":
        revoked_count = token_store.revoke_tokens_for_user_share_page(subject_id, share_page_id)
    else:
        # 组授权撤销:吊销该组所有成员对该分享页的 T_short
        # 通过 seed.list_group_members 封装访问(消除 Feature Envy,不直接读 seed.group_members)
        members = seed.list_group_members(subject_id)
        revoked_count = 0
        for member_id in members:
            revoked_count += token_store.revoke_tokens_for_user_share_page(member_id, share_page_id)
    # Slice 6 审计:grant_revoke
    _audit(
        request,
        user,
        "grant_revoke",
        "grant",
        share_page_id,
        subject_type=subject_type,
        subject_id=subject_id,
        tokens_revoked=revoked_count,
    )
    return {
        "revoked": True,
        "share_page_id": share_page_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "tokens_revoked": revoked_count,
    }


# ===========================================================================
# Slice 6:管理员后台(会话搜索/分级查看/审计日志/待重试清理)
# ===========================================================================
#
# 全部 require_admin(普通用户调任何 /admin/* → 403,Slice 4 已强制)。
# 审计日志写入点散落在 login / grant / session_delete / user_enable/disable 等
# 已有路由;此处只新增查询端点与 elevated 查正文端点。


@router.get("/admin/sessions")
async def admin_list_all_sessions(
    request: Request,
    user_id: str | None = Query(None, description="按门户用户 ID 过滤"),
    share_page_id: str | None = Query(None, description="按分享页 ID 过滤"),
    since: float | None = Query(None, description="起始时间(unix 时间戳,按 created_at 过滤)"),
    until: float | None = Query(None, description="截止时间(unix 时间戳,按 created_at 过滤)"),
    keyword: str | None = Query(None, description="按会话标题模糊匹配(大小写不敏感)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数上限"),
    user=Depends(require_admin),
):
    """管理员列出所有用户的会话(按用户/分享页/时间/关键词过滤,返回元数据,不含正文)。

    对应 Slice 6 验收点 2:管理员能搜索/列出所有用户的会话。
    默认按 created_at 倒序;排除 deleted_at 非空的(待重试删除的会话由
    /admin/sessions/pending-deletion 单独查询)。keyword 按标题模糊匹配(大小写不敏感)。
    """
    session_store = request.app.state.session_store
    sessions = session_store.list_all(
        portal_user_id=user_id,
        share_page_id=share_page_id,
        since=since,
        until=until,
        keyword=keyword,
        limit=limit,
    )
    return {"sessions": [_session_owner_to_metadata_dict(s) for s in sessions]}


@router.get("/admin/sessions/pending-deletion")
async def admin_list_pending_deletion(request: Request, user=Depends(require_admin)):
    """管理员查看待重试删除的会话(deleted_at 非空的记录,Slice 5 标记 + Slice 6 暴露查询)。

    对应 Slice 6 验收点 6:管理员能查看待重试删除的会话并手动触发清理。
    """
    session_store = request.app.state.session_store
    pending = session_store.list_pending_deletion()
    return {"sessions": [_session_owner_to_metadata_dict(s) for s in pending]}


@router.get("/admin/sessions/{session_id}")
async def admin_get_session(
    session_id: str,
    request: Request,
    elevated: bool = Query(False, description="true=查正文(写审计);缺省/false=只看元数据"),
    user=Depends(require_admin),
):
    """管理员查看会话详情(分级查看,对应 Slice 6 验收点 3-4)。

    - 默认(elevated 缺省/false):返回元数据(标题、用户、时间等,不含正文)。
    - elevated=true:
      1. 写 audit_log(action=session_view_elevated, target_type=session,
         target_id=session_id, meta={owner_user_id, share_page_id})。
      2. 调 RAGFlow GET 端点取回正文(messages + reference)。
      3. 返回 {metadata, messages, reference}。

    对应 PRD D7a:管理员默认只看元数据,查正文需二次确认 + 写审计。
    """
    session_store = request.app.state.session_store
    owner = session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    metadata = _session_owner_to_metadata_dict(owner)
    if not elevated:
        # 默认只返回元数据,不写审计,不调 RAGFlow(验收点 3)
        return metadata
    # elevated=true:写审计 + 调 RAGFlow 取正文(验收点 4)
    _audit(
        request,
        user,
        "session_view_elevated",
        "session",
        session_id,
        owner_user_id=owner.portal_user_id,
        share_page_id=owner.share_page_id,
    )
    settings = request.app.state.settings
    history = await _invoke_upstream(
        lambda: fetch_session_history_via_ragflow(settings, owner.ragflow_resource_id, session_id),
        "取回会话失败",
    )
    # 恢复会话时用 len(messages) 更新 message_count(简化实现:字段存在,恢复后准确)
    # Slice 8:DB 后端需经公开 API update_message_count 持久化(原直接改 dataclass 属性不生效)
    if isinstance(history, dict):
        messages = history.get("messages", [])
        if owner.message_count != len(messages):
            request.app.state.session_store.update_message_count(session_id, len(messages))
    # 合并元数据与正文
    return {
        **metadata,
        "messages": history.get("messages", []) if isinstance(history, dict) else [],
        "reference": history.get("reference", {}) if isinstance(history, dict) else {},
    }


@router.post("/admin/sessions/{session_id}/retry-delete")
async def admin_retry_delete_session(session_id: str, request: Request, user=Depends(require_admin)):
    """管理员手动触发待重试会话的清理(对应 Slice 6 验收点 6)。

    流程:
      1. 校验 session 存在且 deleted_at 非空(必须是待重试状态)。
      2. 调 RAGFlow DELETE;成功 → 删门户记录(硬删除),记 session_delete 审计。
      3. RAGFlow 失败 → 透传 HTTPException(可能 404/500/502),门户侧记录保留(仍待重试)。
    """
    session_store = request.app.state.session_store
    owner = session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner.deleted_at is None:
        raise HTTPException(status_code=400, detail="该会话不在待重试状态(deleted_at 为空)")
    settings = request.app.state.settings
    try:
        await delete_session_via_ragflow(settings, owner.ragflow_resource_id, session_id)
    except HTTPException:
        # RAGFlow 失败:透传 HTTPException(可能 404/500/502),门户侧记录保留(仍待重试)
        raise
    # RAGFlow 成功 → 删门户记录(硬删除)
    session_store.delete(session_id)
    # Slice 6 审计:session_delete(管理员手动重试清理)
    _audit(
        request,
        user,
        "session_delete",
        "session",
        session_id,
        share_page_id=owner.share_page_id,
        owner_user_id=owner.portal_user_id,
        retry=True,
    )
    return {"session_id": session_id, "deleted": True}


@router.get("/admin/audit-logs")
async def admin_list_audit_logs(
    request: Request,
    actor_user_id: str | None = Query(None, description="按操作者用户 ID 过滤"),
    action: str | None = Query(None, description="按 action 过滤(8 类敏感操作之一)"),
    since: float | None = Query(None, description="起始时间(unix 时间戳)"),
    until: float | None = Query(None, description="截止时间(unix 时间戳)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数上限"),
    user=Depends(require_admin),
):
    """管理员查看审计日志(按 action/actor/时间过滤,对应 Slice 6 验收点 5)。

    返回最新的 limit 条(按 at 倒序)。审计日志永久保留,无 TTL(PR D8b)。
    """
    audit_store = request.app.state.audit_store
    logs = audit_store.list(
        actor_user_id=actor_user_id,
        action=action,
        since=since,
        until=until,
        limit=limit,
    )
    return {"audit_logs": [_audit_log_to_dict(log) for log in logs]}
