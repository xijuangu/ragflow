"""认证模块 — 密码哈希与同源会话校验。

密码哈希用 bcrypt(直接调用,避免 passlib 与新版 bcrypt 的兼容问题)。
会话用 Starlette SessionMiddleware(HTTP-only 签名 cookie,同源)。

Slice 4 改进:
  - 登录失败明确错误(用户未注册 / 密码错误 / 账号已禁用)。
  - 禁用用户(enabled=false)无法登录(403 账号已禁用)。
  - 网关请求校验同源 cookie 时,若用户已禁用 → 403(会话保留但拒绝代理)。
"""

from fastapi import HTTPException, Request

from portal.models import PortalUser
from portal.password import verify_password


class LoginError(HTTPException):
    """登录失败异常 — 携带明确的中文错误 detail(对应验收点 9)。"""


async def authenticate(seed, username: str, password: str) -> PortalUser:
    """登录校验:返回 PortalUser 或抛 LoginError(明确错误)。

    错误语义(对应 PRD 用户故事 2):
      - 用户不存在 → 401 用户未注册(中性措辞,涵盖 username/email 登录)
      - 密码错误 → 401 密码错误
      - 账号禁用 → 403 账号已禁用
    """
    user = seed.get_user_by_username(username)
    if user is None:
        raise LoginError(status_code=401, detail="用户未注册")
    if not verify_password(password, user.password_hash):
        raise LoginError(status_code=401, detail="密码错误")
    if not user.enabled:
        raise LoginError(status_code=403, detail="账号已禁用")
    return user


async def get_current_user(request: Request) -> PortalUser:
    """会话校验依赖:从同源 session cookie 解出当前用户。

    未登录或用户已禁用 → 403(对应验收点 5:未登录用户请求分享页 → 403)。
    禁用用户的同源 cookie 仍可能存在(被禁用前已登录),网关每次请求都校验 enabled,
    禁用后调网关 → 403(会话保留但拒绝代理)。
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=403, detail="未登录")
    # Slice 8:经 SeedData 公开 API 查用户(原直接访问 users_by_id dict,现 DB 后端)
    user = request.app.state.seed.get_user(user_id)
    if not user or not user.enabled:
        # 会话存在但用户已禁用/删除 → 清除会话并拒绝
        request.session.clear()
        raise HTTPException(status_code=403, detail="用户无效或已禁用")
    return user


async def require_admin(request: Request) -> PortalUser:
    """管理员校验依赖:校验当前用户 is_admin == True,否则 → 403。

    所有 /admin/* 路由用 Depends(require_admin)(对应验收点 7:普通用户调管理 API → 403)。

    Slice 13:新增 require_org_admin 接受 is_admin 或 org_admin;本依赖仍只接受 is_admin
    (平台级操作,如跨 org 全量查看)。大多数 /admin/* 端点改用 require_org_admin,
    本依赖保留给未来仅平台管理员的端点。
    """
    user = await get_current_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user


async def require_org_admin(request: Request) -> PortalUser:
    """org 级管理员或平台管理员校验依赖(Slice 13)。

    接受 is_admin=True(平台管理员,可跨 org)或 org_admin=True(org 级管理员,仅本 org)。
    普通用户(两者皆 False)→ 403(对应验收点 3:普通用户调管理端点 → 403)。

    org 隔离逻辑由路由层各自实现(is_admin 跨 org;org_admin 强制 user.org_id)。
    """
    user = await get_current_user(request)
    if not user.is_admin and not user.org_admin:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user
