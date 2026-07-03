"""认证模块 — 密码哈希与同源会话校验。

密码哈希用 bcrypt(直接调用,避免 passlib 与新版 bcrypt 的兼容问题)。
会话用 Starlette SessionMiddleware(HTTP-only 签名 cookie,同源)。
"""

from fastapi import HTTPException, Request

from portal.models import PortalUser


async def get_current_user(request: Request) -> PortalUser:
    """会话校验依赖:从同源 session cookie 解出当前用户。

    未登录或用户已禁用 → 403(对应验收点 5:未登录用户请求分享页 → 403)。
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=403, detail="未登录")
    users_by_id = request.app.state.seed.users_by_id
    user = users_by_id.get(user_id)
    if not user or not user.enabled:
        # 会话存在但用户已禁用/删除 → 清除会话并拒绝
        request.session.clear()
        raise HTTPException(status_code=403, detail="用户无效或已禁用")
    return user
