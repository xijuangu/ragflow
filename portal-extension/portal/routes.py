"""API 路由 — 登录、分享页 embed-url、SSE 代理、session 预创建/列表/恢复/重命名/删除、CRUD。

Slice 4 新增:
  - 管理员 CRUD(/admin/users、/admin/groups、/admin/share-pages、grants)。
  - 普通用户 GET /share-pages(只看自己被授权的)。
  - 登录失败明确错误(用户未注册 / 密码错误 / 账号已禁用)。
  - 撤销授权支持 user 与 group 两种 subject_type。

Slice 5 新增:
  - PATCH /share-pages/{id}/sessions/{sid} — 用户重命名自己的会话(双写:RAGFlow 失败不阻塞)。
  - DELETE /share-pages/{id}/sessions/{sid} — 用户删除自己的会话(双删:RAGFlow 失败标记 deleted_at)。
  - DELETE /admin/share-pages/{id}/sessions/{sid} — 管理员删除任意会话(双删,不校验归属)。
  - DELETE /admin/users/{id} — 管理员硬删除用户(级联双删 chat_session_owner + RAGFlow)。
"""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portal.auth import authenticate, get_current_user, require_admin
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


# ---------------------------------------------------------------------------
# 登录(Slice 1,Slice 4 改进明确错误)
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    """登录端点:校验凭据,建立同源会话 cookie(对应验收点 1)。

    Slice 4 改进:登录失败明确错误(用户未注册 / 密码错误 / 账号已禁用)。
    """
    seed = request.app.state.seed
    user = await authenticate(seed, body.username, body.password)
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

    双写策略:RAGFlow PATCH 失败不阻塞门户 title 更新(失败不暴露给用户)。
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
    # 双写:RAGFlow PATCH(best effort,失败不阻塞门户 title 更新)
    settings = request.app.state.settings
    try:
        await rename_session_via_ragflow(settings, share_page.ragflow_resource_id, session_id, body.title)
    except HTTPException:
        # RAGFlow 失败不阻塞门户 title 更新(双写策略:重命名不阻塞)
        pass
    # 更新门户 title(无论 RAGFlow 是否成功)
    request.app.state.session_store.rename(session_id, body.title)
    return {"session_id": session_id, "title": body.title}


@router.delete("/share-pages/{share_page_id}/sessions/{session_id}")
async def delete_session(share_page_id: str, session_id: str, request: Request, user=Depends(get_current_user)):
    """删除会话(对应 Slice 5 验收点 3-4:用户删除自己的会话,双删协调)。

    双删事务策略:
      - 先调 RAGFlow DELETE;成功 → 门户侧硬删除 chat_session_owner。
      - RAGFlow 失败 → 标记 deleted_at(记录保留待重试),返回 200(不暴露失败)。
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
    # 双删:RAGFlow DELETE
    settings = request.app.state.settings
    session_store = request.app.state.session_store
    try:
        await delete_session_via_ragflow(settings, share_page.ragflow_resource_id, session_id)
        # RAGFlow 成功 → 门户侧硬删除
        session_store.delete(session_id)
    except HTTPException:
        # RAGFlow 失败 → 标记 deleted_at(记录保留待重试),返回 200(不暴露失败)
        session_store.mark_deleted(session_id)
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
    """
    seed = request.app.state.seed
    if not seed.set_user_enabled(user_id, body.enabled):
        raise HTTPException(status_code=404, detail="用户不存在")
    target = seed.get_user(user_id)
    return _user_to_dict(target)


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request, user=Depends(require_admin)):
    """管理员硬删除用户(对应 Slice 5 验收点 6:级联双删 chat_session_owner + RAGFlow API4Conversation)。

    级联策略:
      - 遍历用户的所有 chat_session_owner(含 deleted_at 标记的,跨分享页)。
      - 每条调 RAGFlow DELETE:成功 → 门户硬删除;失败 → 标记 deleted_at(记录保留待重试)。
      - 最后删 portal_user(无论 RAGFlow 是否失败,用户都删除)。
    禁用用户不删会话(走 PATCH /admin/users/{id});硬删除才级联删会话。
    """
    seed = request.app.state.seed
    if seed.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    settings = request.app.state.settings
    session_store = request.app.state.session_store
    # 级联双删用户的所有会话(含 deleted_at 标记的,跨分享页)
    for owner in session_store.list_all_for_user(user_id):
        try:
            await delete_session_via_ragflow(settings, owner.ragflow_resource_id, owner.session_id)
            # RAGFlow 成功 → 门户侧硬删除
            session_store.delete(owner.session_id)
        except HTTPException:
            # RAGFlow 失败 → 标记 deleted_at(记录保留待重试),不阻塞用户删除
            session_store.mark_deleted(owner.session_id)
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
        d["member_count"] = len(seed.group_members.get(g.id, set()))
        d["members"] = list(seed.group_members.get(g.id, set()))
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

    双删策略与普通用户删除一致:RAGFlow 成功 → 门户硬删除;失败 → 标记 deleted_at。
    """
    seed = request.app.state.seed
    if seed.get_share_page(share_page_id) is None:
        raise HTTPException(status_code=404, detail="分享页不存在")
    owner = request.app.state.session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 双删:RAGFlow DELETE(管理员不校验归属,可删任意用户的会话)
    settings = request.app.state.settings
    session_store = request.app.state.session_store
    try:
        await delete_session_via_ragflow(settings, owner.ragflow_resource_id, session_id)
        session_store.delete(session_id)
    except HTTPException:
        session_store.mark_deleted(session_id)
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
    """
    seed = request.app.state.seed
    if seed.get_share_page(share_page_id) is None:
        raise HTTPException(status_code=404, detail="分享页不存在")
    _validate_subject_exists(seed, body.subject_type, body.subject_id)
    grant = seed.create_grant(share_page_id, body.subject_type, body.subject_id, body.permission)
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
    # 分享页必须存在
    if share_page_id not in seed.share_pages_by_id:
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
    return {
        "revoked": True,
        "share_page_id": share_page_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "tokens_revoked": revoked_count,
    }
