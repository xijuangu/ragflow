"""Slice 17 端到端测试 — Portal 会话 cookie 隔离(修复 B2 同源 cookie 踩踏)。

覆盖验收点(ISSUES.md Issue 17):
  - portal 会话 cookie 名为 `portal_session`(非默认 `session`)

背景:同源部署(172.16.10.180:80)下 portal 与 RAGFlow 共享 cookie jar,
若两端都用默认 cookie 名 `session`,RAGFlow 登录覆盖 portal session cookie,
portal 签名校验失败 → 会话丢失 → 刷新即登出(B2)。Slice 17 改 cookie 名隔离。
"""



async def test_login_sets_portal_session_cookie(client, app):
    """登录后 Set-Cookie 含 portal_session,不含默认 session cookie。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    # Set-Cookie 头应含 portal_session(非默认 session)
    set_cookie = resp.headers.get("set-cookie", "")
    assert "portal_session=" in set_cookie, f"应设置 portal_session cookie,实际: {set_cookie}"
    # 不应出现默认 session cookie(避免与 RAGFlow 同源踩踏)
    # portal_session= 替换后不应再含 session= 模式
    remaining = set_cookie.replace("portal_session=", "")
    assert "session=" not in remaining, f"不应设置默认 session cookie,实际: {set_cookie}"


async def test_portal_session_cookie_persists_across_requests(client, app):
    """portal_session cookie 在后续请求中持久化(登录态保持)。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    # 登录后调需登录端点,应返回 200(而非 403 未登录)
    resp = await client.get("/share-pages")
    assert resp.status_code == 200, f"登录态丢失: {resp.status_code} {resp.text}"
