"""Slice 4 端到端测试 — 用户/用户组/分享页/ACL CRUD。

覆盖验收点(ISSUES.md Issue 4):
  1. 管理员创建用户 → 返回用户详情;重复用户名 → 400。
  2. 管理员创建用户组 → 返回组详情;添加成员;移除成员。
  3. 管理员创建分享页(关联 dialog_id)→ 返回分享页详情;embed_type/ragflow_type 固定值。
  4. 管理员把分享页授权给用户 → 返回 grant;授权给用户组 → 返回 grant。
  5. 普通用户登录后查询分享页列表 → 只看到被授权的(直接或组继承)。
  6. 用户组成员继承组授权(用户仅通过组授权能访问分享页)。
  7. 普通用户调管理 API(create user/group/share_page/grant)→ 403。
  8. 禁用用户无法登录(明确错误);会话保留(chat_session_owner 记录仍在)。
  9. 登录失败:邮箱未注册 / 密码错误 / 账号已禁用 三种明确错误。
  10. 网关 ACL:用户仅通过组授权时,调 embed-url / SSE 代理成功(组 grant 生效)。
"""

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx

from portal.models import ChatSessionOwner


def _extract_iframe_params(url: str) -> dict:
    """从 iframe URL 提取 query 参数。"""
    return parse_qs(urlparse(url).query)


async def _login_admin(client):
    """辅助:admin 登录。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200, f"admin 登录失败: {resp.text}"


async def _create_user(client, username="alice", email="alice@example.com", password="alicepass123"):
    """辅助:管理员创建普通用户,返回响应体。"""
    resp = await client.post(
        "/admin/users",
        json={"username": username, "email": email, "password": password},
    )
    assert resp.status_code == 201, f"创建用户失败: {resp.text}"
    return resp.json()


async def _create_group(client, name="engineering"):
    """辅助:管理员创建用户组,返回响应体。"""
    resp = await client.post("/admin/groups", json={"name": name})
    assert resp.status_code == 201, f"创建用户组失败: {resp.text}"
    return resp.json()


async def _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001"):
    """辅助:管理员创建分享页,返回响应体。"""
    resp = await client.post(
        "/admin/share-pages",
        json={"name": name, "ragflow_resource_id": ragflow_resource_id},
    )
    assert resp.status_code == 201, f"创建分享页失败: {resp.text}"
    return resp.json()


def _mock_ragflow_sse_success(monkeypatch, sse_body: bytes = b'data: {"code":0}\n\n'):
    """辅助:mock 网关 httpx.AsyncClient 让上游 SSE 返回成功流。"""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


# ---------------------------------------------------------------------------
# 验收点 1:管理员创建用户 → 返回用户详情;重复用户名 → 400。
# ---------------------------------------------------------------------------


async def test_admin_create_user_returns_details(client):
    """管理员创建用户 → 201,返回用户详情(含 email/is_admin/enabled)。"""
    await _login_admin(client)
    body = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    assert body["username"] == "alice"
    assert body["email"] == "alice@example.com"
    assert body["is_admin"] is False
    assert body["enabled"] is True
    assert "id" in body and body["id"]
    assert "created_at" in body
    # 响应不应回传密码或密码哈希
    assert "password" not in str(body).lower()


async def test_admin_create_user_duplicate_username_returns_400(client):
    """重复用户名 → 400。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    resp = await client.post(
        "/admin/users",
        json={"username": "alice", "email": "alice2@example.com", "password": "anotherpass"},
    )
    assert resp.status_code == 400


async def test_admin_list_users(client):
    """管理员列出用户:至少含 seed admin + user2 + 新建用户。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    resp = await client.get("/admin/users")
    assert resp.status_code == 200
    users = resp.json()["users"]
    usernames = [u["username"] for u in users]
    assert "admin" in usernames
    assert "user2" in usernames
    assert "alice" in usernames


async def test_admin_get_user_detail(client):
    """管理员按 id 查用户详情。"""
    await _login_admin(client)
    created = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    resp = await client.get(f"/admin/users/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"


async def test_admin_get_unknown_user_returns_404(client):
    """查询不存在用户 → 404。"""
    await _login_admin(client)
    resp = await client.get("/admin/users/u_nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 2:管理员创建用户组 → 返回组详情;添加成员;移除成员。
# ---------------------------------------------------------------------------


async def test_admin_create_group_returns_details(client):
    """管理员创建用户组 → 201,返回组详情(含 member_count/members,与列表端点一致)。

    Slice 43:补 member_count/members 断言,防止后端 admin_create_group 漏字段导致
    前端 GroupsAdminPage 渲染崩溃(原 bug:只返回 id/name/created_at/org_id)。
    """
    await _login_admin(client)
    body = await _create_group(client, name="engineering")
    assert body["name"] == "engineering"
    assert body["id"]
    assert "created_at" in body
    # Slice 43:新建组无成员,member_count=0,members=[](与 admin_list_groups 一致)
    assert body["member_count"] == 0, f"新组 member_count 应为 0,实际: {body.get('member_count')}"
    assert body["members"] == [], f"新组 members 应为 [],实际: {body.get('members')}"


async def test_admin_list_groups(client):
    """管理员列出用户组。"""
    await _login_admin(client)
    await _create_group(client, name="engineering")
    resp = await client.get("/admin/groups")
    assert resp.status_code == 200
    names = [g["name"] for g in resp.json()["groups"]]
    assert "engineering" in names


async def test_admin_add_and_remove_group_member(client):
    """管理员添加/移除用户组成员。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    group = await _create_group(client, name="engineering")

    # 添加成员
    resp = await client.post(f"/admin/groups/{group['id']}/members", json={"user_id": user["id"]})
    assert resp.status_code == 200, f"添加成员失败: {resp.text}"
    # 重复添加 → 幂等或 400(一期允许 200 幂等,测试只断言不报 5xx)
    resp = await client.post(f"/admin/groups/{group['id']}/members", json={"user_id": user["id"]})
    assert resp.status_code < 500

    # 移除成员
    resp = await client.delete(f"/admin/groups/{group['id']}/members/{user['id']}")
    assert resp.status_code == 200, f"移除成员失败: {resp.text}"
    # 再次移除 → 404(已不在组内)
    resp = await client.delete(f"/admin/groups/{group['id']}/members/{user['id']}")
    assert resp.status_code == 404


async def test_admin_add_member_unknown_group_returns_404(client):
    """添加成员到不存在的组 → 404。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    resp = await client.post("/admin/groups/g_nonexistent/members", json={"user_id": user["id"]})
    assert resp.status_code == 404


async def test_admin_add_member_unknown_user_returns_404(client):
    """添加不存在的用户到组 → 404。"""
    await _login_admin(client)
    group = await _create_group(client, name="engineering")
    resp = await client.post(f"/admin/groups/{group['id']}/members", json={"user_id": "u_nonexistent"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 3:管理员创建分享页(关联 dialog_id)→ 返回分享页详情;固定值字段。
# ---------------------------------------------------------------------------


async def test_admin_create_share_page_returns_details(client):
    """管理员创建分享页 → 201,embed_type/ragflow_type 为固定值。"""
    await _login_admin(client)
    body = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    assert body["name"] == "财务对话"
    assert body["ragflow_resource_id"] == "dialog-finance-001"
    # 一期固定值(D9)
    assert body["ragflow_type"] == "chat"
    assert body["embed_type"] == "fullscreen"
    assert body["enabled"] is True
    assert body["id"]


async def test_admin_list_share_pages(client):
    """管理员列出所有分享页(含 seed 默认分享页)。"""
    await _login_admin(client)
    await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    resp = await client.get("/admin/share-pages")
    assert resp.status_code == 200
    pages = resp.json()["share_pages"]
    # 至少含 seed 默认分享页 + 新建
    assert any(p["id"] == "sp_default" for p in pages)
    assert any(p["name"] == "财务对话" for p in pages)


async def test_admin_disable_share_page(client):
    """管理员禁用分享页 → enabled=False。"""
    await _login_admin(client)
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    resp = await client.patch(f"/admin/share-pages/{page['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


async def test_admin_disable_unknown_share_page_returns_404(client):
    """禁用不存在的分享页 → 404。"""
    await _login_admin(client)
    resp = await client.patch("/admin/share-pages/sp_nonexistent", json={"enabled": False})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 4:管理员把分享页授权给用户或用户组 → 返回 grant。
# ---------------------------------------------------------------------------


async def test_admin_grant_share_page_to_user(client):
    """管理员把分享页授权给用户 → 返回 grant。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    resp = await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )
    assert resp.status_code == 201, f"授权失败: {resp.text}"
    body = resp.json()
    assert body["share_page_id"] == page["id"]
    assert body["subject_type"] == "user"
    assert body["subject_id"] == user["id"]
    assert body["permission"] == "use"


async def test_admin_grant_share_page_to_group(client):
    """管理员把分享页授权给用户组 → 返回 grant。"""
    await _login_admin(client)
    group = await _create_group(client, name="engineering")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    resp = await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["subject_type"] == "group"
    assert body["subject_id"] == group["id"]


async def test_admin_list_grants(client):
    """管理员列出某分享页的所有 grant。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )
    resp = await client.get(f"/admin/share-pages/{page['id']}/grants")
    assert resp.status_code == 200
    grants = resp.json()["grants"]
    assert any(g["subject_id"] == user["id"] and g["subject_type"] == "user" for g in grants)


async def test_admin_revoke_group_grant(client):
    """管理员撤销用户组授权(Slice 4 启用 group subject_type)。"""
    await _login_admin(client)
    group = await _create_group(client, name="engineering")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )
    # 撤销路由保留 Slice 3 的 /share-pages/... 前缀(向后兼容)
    resp = await client.delete(f"/share-pages/{page['id']}/grants/group/{group['id']}")
    assert resp.status_code == 200, f"撤销组授权失败: {resp.text}"
    # 撤销后列表无此 grant
    resp = await client.get(f"/admin/share-pages/{page['id']}/grants")
    grants = resp.json()["grants"]
    assert not any(g["subject_id"] == group["id"] and g["subject_type"] == "group" for g in grants)


async def test_admin_grant_unknown_share_page_returns_404(client):
    """对不存在的分享页授权 → 404。"""
    await _login_admin(client)
    group = await _create_group(client, name="engineering")
    resp = await client.post(
        "/admin/share-pages/sp_nonexistent/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )
    assert resp.status_code == 404


async def test_admin_grant_unknown_subject_returns_404(client):
    """把分享页授权给不存在的 subject → 404。"""
    await _login_admin(client)
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    resp = await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "user", "subject_id": "u_nonexistent", "permission": "use"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 5:普通用户登录后查询分享页列表 → 只看到被授权的(直接或组继承)。
# ---------------------------------------------------------------------------


async def test_user_lists_only_authorized_share_pages(client, app):
    """普通用户只看到被直接授权的分享页(不含未授权的)。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    authorized_page = await _create_share_page(client, name="授权对话", ragflow_resource_id="dialog-a")
    unauthorized_page = await _create_share_page(client, name="未授权对话", ragflow_resource_id="dialog-b")
    await client.post(
        f"/admin/share-pages/{authorized_page['id']}/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )

    # alice 登录后查询可见分享页
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        assert resp.status_code == 200
        resp = await alice_client.get("/share-pages")
        assert resp.status_code == 200
        pages = resp.json()["share_pages"]
        page_ids = [p["id"] for p in pages]
        assert authorized_page["id"] in page_ids
        assert unauthorized_page["id"] not in page_ids


# ---------------------------------------------------------------------------
# 验收点 6:用户组成员继承组授权(用户仅通过组授权能访问分享页)。
# ---------------------------------------------------------------------------


async def test_group_member_inherits_grant(client, app):
    """用户仅通过组授权能访问分享页(组继承)。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    group = await _create_group(client, name="engineering")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    # 把用户加入组
    await client.post(f"/admin/groups/{group['id']}/members", json={"user_id": user["id"]})
    # 把分享页授权给组(不给用户直接授权)
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        assert resp.status_code == 200
        # alice 在可见分享页列表中能看到该分享页(组继承)
        resp = await alice_client.get("/share-pages")
        assert resp.status_code == 200
        page_ids = [p["id"] for p in resp.json()["share_pages"]]
        assert page["id"] in page_ids, "用户组成员未继承组授权"


async def test_non_member_does_not_inherit_group_grant(client, app):
    """非用户组成员不继承组授权。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    group = await _create_group(client, name="engineering")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    # 不把 alice 加入组
    # 把分享页授权给组
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        assert resp.status_code == 200
        resp = await alice_client.get("/share-pages")
        page_ids = [p["id"] for p in resp.json()["share_pages"]]
        assert page["id"] not in page_ids, "非组成员不应继承组授权"


# ---------------------------------------------------------------------------
# 验收点 7:普通用户调管理 API → 403。
# ---------------------------------------------------------------------------


async def test_non_admin_cannot_create_user(client, app):
    """普通用户调创建用户 API → 403。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        resp = await alice_client.post(
            "/admin/users",
            json={"username": "bob", "email": "bob@example.com", "password": "bobpass123"},
        )
        assert resp.status_code == 403


async def test_non_admin_cannot_list_users(client, app):
    """普通用户调列出用户 API → 403。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        resp = await alice_client.get("/admin/users")
        assert resp.status_code == 403


async def test_non_admin_cannot_create_group(client, app):
    """普通用户调创建用户组 API → 403。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        resp = await alice_client.post("/admin/groups", json={"name": "engineering"})
        assert resp.status_code == 403


async def test_non_admin_cannot_create_share_page(client, app):
    """普通用户调创建分享页 API → 403。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        resp = await alice_client.post(
            "/admin/share-pages",
            json={"name": "x", "ragflow_resource_id": "y"},
        )
        assert resp.status_code == 403


async def test_non_admin_cannot_grant(client, app):
    """普通用户调授权 API → 403。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        resp = await alice_client.post(
            f"/admin/share-pages/{page['id']}/grants",
            json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
        )
        assert resp.status_code == 403


async def test_admin_routes_require_login(client):
    """未登录调任意 admin 路由 → 403。"""
    resp = await client.get("/admin/users")
    assert resp.status_code == 403
    resp = await client.post("/admin/users", json={"username": "x", "email": "y@z", "password": "z"})
    assert resp.status_code == 403
    resp = await client.get("/admin/groups")
    assert resp.status_code == 403
    resp = await client.get("/admin/share-pages")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 验收点 8:禁用用户无法登录(明确错误);会话保留。
# ---------------------------------------------------------------------------


async def test_disable_user_blocks_login_and_preserves_session(client, app, monkeypatch):
    """禁用用户无法登录(明确错误提示);其历史会话保留(chat_session_owner 记录仍在)。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")

    # alice 登录并预创建一个 session(模拟历史会话)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        assert resp.status_code == 200
        # 给 alice 直接授权 sp_default 以便能预创建 session
        # 注:由 admin 给 alice grant sp_default
    resp = await client.post(
        "/admin/share-pages/sp_default/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )
    assert resp.status_code == 201

    # alice 预创建 session
    fake_session_id = "slice4-disabled-user-session-001"
    monkeypatch.setattr(
        "portal.routes.precreate_session_via_ragflow",
        AsyncMock(return_value=fake_session_id),
    )
    async with httpx.ASGITransport(app=app) as transport2:
        async with httpx.AsyncClient(transport=transport2, base_url="http://testserver") as alice_client:
            resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
            assert resp.status_code == 200
            resp = await alice_client.post("/share-pages/sp_default/sessions")
            assert resp.status_code == 200, f"预创建失败: {resp.text}"

    # 确认 session 已绑定到 alice
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None
    assert owner.portal_user_id == user["id"]

    # 管理员禁用 alice
    resp = await client.patch(f"/admin/users/{user['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # alice 再次登录 → 403 账号已禁用
    async with httpx.ASGITransport(app=app) as transport3:
        async with httpx.AsyncClient(transport=transport3, base_url="http://testserver") as alice_client:
            resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
            assert resp.status_code == 403
            assert "禁用" in resp.json()["detail"]

    # 历史会话保留
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after is not None, "禁用用户后历史会话应保留"
    assert owner_after.portal_user_id == user["id"]


# ---------------------------------------------------------------------------
# 验收点 9:登录失败明确错误(用户未注册 / 密码错误 / 账号已禁用)。
# ---------------------------------------------------------------------------


async def test_login_unknown_user_returns_401_with_clear_message(client):
    """未注册用户登录 → 401 用户未注册。"""
    resp = await client.post("/login", json={"username": "ghost", "password": "whatever"})
    assert resp.status_code == 401
    assert "未注册" in resp.json()["detail"]


async def test_login_wrong_password_returns_401_with_clear_message(client):
    """密码错误 → 401 密码错误。"""
    resp = await client.post("/login", json={"username": "admin", "password": "wrong-pass"})
    assert resp.status_code == 401
    assert "密码错误" in resp.json()["detail"]


async def test_login_disabled_user_returns_403_with_clear_message(client):
    """禁用用户登录 → 403 账号已禁用。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    resp = await client.patch(f"/admin/users/{user['id']}", json={"enabled": False})
    assert resp.status_code == 200

    resp = await client.post("/login", json={"username": "alice", "password": "alicepass123"})
    assert resp.status_code == 403
    assert "禁用" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 验收点 10:网关 ACL — 用户仅通过组授权时,调 embed-url / SSE 代理成功。
# ---------------------------------------------------------------------------


async def test_gateway_acl_group_grant_allows_embed_url(client, app):
    """用户仅通过组授权 → embed-url 签发成功(组 grant 生效)。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    group = await _create_group(client, name="engineering")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    await client.post(f"/admin/groups/{group['id']}/members", json={"user_id": user["id"]})
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )

    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
            resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
            assert resp.status_code == 200
            # alice 仅通过组授权访问分享页 → 200(组 grant 生效)
            resp = await alice_client.get(f"/share-pages/{page['id']}/embed-url")
            assert resp.status_code == 200, f"组授权应允许签发 embed-url: {resp.text}"


async def test_gateway_acl_group_grant_allows_sse_proxy(client, app, monkeypatch):
    """用户仅通过组授权 → SSE 代理校验通过(组 grant 生效,网关 ACL 解析 user+group)。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    group = await _create_group(client, name="engineering")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    await client.post(f"/admin/groups/{group['id']}/members", json={"user_id": user["id"]})
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "group", "subject_id": group["id"], "permission": "use"},
    )

    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
            resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
            assert resp.status_code == 200
            resp = await alice_client.get(f"/share-pages/{page['id']}/embed-url")
            assert resp.status_code == 200
            t_short = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
            dialog_id = page["ragflow_resource_id"]
            # mock 上游 SSE 返回成功流(在创建 alice_client 之后应用,避免影响登录与 embed-url)
            _mock_ragflow_sse_success(monkeypatch)
            # alice 用 T_short 调 SSE(组 grant 生效,通过网关 ACL 校验)
            resp = await alice_client.post(
                f"/api/v1/chatbots/{dialog_id}/completions",
                json={"question": "测试", "stream": True},
                headers={"Authorization": f"Bearer {t_short}"},
            )
            assert resp.status_code == 200, f"组授权应允许 SSE 代理: {resp.text}"
            await resp.aread()


async def test_gateway_acl_no_grant_rejects_embed_url(client, app):
    """用户无任何 grant → embed-url → 403。"""
    await _login_admin(client)
    await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    # 不给 alice 任何 grant

    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
            resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
            assert resp.status_code == 200
            resp = await alice_client.get(f"/share-pages/{page['id']}/embed-url")
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 禁用用户调网关请求被拒(网关层校验 enabled)。
# ---------------------------------------------------------------------------


async def test_disabled_user_gateway_request_rejected(client, app):
    """禁用用户的同源 cookie 调网关 → 403(get_current_user 校验 enabled=False)。"""
    await _login_admin(client)
    user = await _create_user(client, username="alice", email="alice@example.com", password="alicepass123")
    page = await _create_share_page(client, name="财务对话", ragflow_resource_id="dialog-finance-001")
    await client.post(
        f"/admin/share-pages/{page['id']}/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )

    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
            resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
            assert resp.status_code == 200
            # 拿到 T_short
            resp = await alice_client.get(f"/share-pages/{page['id']}/embed-url")
            assert resp.status_code == 200
            t_short = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
            dialog_id = page["ragflow_resource_id"]
            # 管理员禁用 alice
            resp = await client.patch(f"/admin/users/{user['id']}", json={"enabled": False})
            assert resp.status_code == 200
            # alice 同源 cookie 还在,但用户已禁用 → 调网关被拒 403
            resp = await alice_client.post(
                f"/api/v1/chatbots/{dialog_id}/completions",
                json={"question": "测试", "stream": True},
                headers={"Authorization": f"Bearer {t_short}"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 验证 has_use_grant 直接调用(Slice 3 兼容 + Slice 4 升级)
# ---------------------------------------------------------------------------


def test_seed_data_has_use_grant_supports_user_and_group(app):
    """SeedData.has_use_grant 同时识别 user 与 group subject_type(用户组继承)。"""
    seed = app.state.seed
    # 构造:user3 不在组内 + group_x 含 user3 + 分享页授权给 group_x
    user3 = seed.create_user(username="user3", email="user3@example.com", password_hash="hash")
    group_x = seed.create_group(name="group_x")
    page = seed.create_share_page(name="test", ragflow_resource_id="dialog-x")
    # 不直接授权给 user3,只授权给 group_x
    seed.create_grant(page.id, "group", group_x.id, "use")
    # user3 不在组内 → False
    assert seed.has_use_grant(page.id, user3.id) is False
    # 把 user3 加入组 → True(组继承)
    seed.add_group_member(group_x.id, user3.id)
    assert seed.has_use_grant(page.id, user3.id) is True
    # 移除成员 → False
    seed.remove_group_member(group_x.id, user3.id)
    assert seed.has_use_grant(page.id, user3.id) is False


def test_seed_data_has_use_grant_user_direct(app):
    """has_use_grant 仍识别 user subject_type(Slice 3 兼容)。"""
    seed = app.state.seed
    user = seed.create_user(username="user_direct", email="ud@example.com", password_hash="hash")
    page = seed.create_share_page(name="p", ragflow_resource_id="d")
    seed.create_grant(page.id, "user", user.id, "use")
    assert seed.has_use_grant(page.id, user.id) is True


def test_seed_data_revoke_grant_supports_subject_type(app):
    """revoke_grant 按 (share_page_id, subject_type, subject_id) 撤销(Slice 4 升级)。"""
    seed = app.state.seed
    user = seed.create_user(username="user_rev", email="ur@example.com", password_hash="hash")
    group = seed.create_group(name="g_rev")
    page = seed.create_share_page(name="p", ragflow_resource_id="d")
    seed.create_grant(page.id, "user", user.id, "use")
    seed.create_grant(page.id, "group", group.id, "use")
    # 撤销 user grant
    assert seed.revoke_grant(page.id, "user", user.id) is True
    assert seed.has_use_grant(page.id, user.id) is False
    # 组 grant 仍在
    # Slice 8:经公开 API list_grants 查询(原直接访问 seed.grants,现 DB 后端)
    assert any(
        g.share_page_id == page.id and g.subject_type == "group" and g.subject_id == group.id
        for g in seed.list_grants(page.id)
    )
    # 撤销 group grant
    assert seed.revoke_grant(page.id, "group", group.id) is True
    # 再次撤销 user grant → False(已删)
    assert seed.revoke_grant(page.id, "user", user.id) is False


def test_create_grant_idempotent(app):
    """create_grant 幂等:重复创建相同 (share_page_id, subject_type, subject_id, permission) 只产生一条记录。

    安全场景:管理员误创建两次,撤销一次后 has_use_grant=False(无残留 grant)。
    """
    seed = app.state.seed
    user = seed.create_user(username="user_idem", email="ui@example.com", password_hash="hash")
    page = seed.create_share_page(name="p_idem", ragflow_resource_id="d_idem")

    # 第一次创建
    grant1 = seed.create_grant(page.id, "user", user.id, "use")
    # 第二次创建相同四元组 → 返回现有 grant,不新增
    grant2 = seed.create_grant(page.id, "user", user.id, "use")
    # Slice 8:DB 后端每次返回新的 dataclass 实例(值相等而非引用相同);
    # 幂等语义由「不重复插入 + 撤销一次即彻底」保证,而非对象身份。
    assert grant1 == grant2, "幂等创建应返回值相等的 grant(不重复插入)"
    # grants 列表只有一条该四元组的记录
    matching = [
        g
        for g in seed.list_grants(page.id)
        if g.share_page_id == page.id and g.subject_type == "user" and g.subject_id == user.id and g.permission == "use"
    ]
    assert len(matching) == 1, f"幂等创建应只产生一条 grant,实际 {len(matching)} 条"

    # 撤销一次后 has_use_grant=False(无残留重复 grant 使撤销不彻底)
    assert seed.revoke_grant(page.id, "user", user.id) is True
    assert seed.has_use_grant(page.id, user.id) is False, "撤销一次后应彻底失效(幂等创建保证无残留)"


def test_create_grant_different_permission_not_idempotent(app):
    """create_grant 对不同 permission 不幂等:use 与 manage 视为不同 grant。"""
    seed = app.state.seed
    user = seed.create_user(username="user_perm", email="up@example.com", password_hash="hash")
    page = seed.create_share_page(name="p_perm", ragflow_resource_id="d_perm")

    grant_use = seed.create_grant(page.id, "user", user.id, "use")
    grant_manage = seed.create_grant(page.id, "user", user.id, "manage")
    assert grant_use != grant_manage, "不同 permission 应为不同 grant"
    matching = [
        g
        for g in seed.list_grants(page.id)
        if g.share_page_id == page.id and g.subject_type == "user" and g.subject_id == user.id
    ]
    assert len(matching) == 2


def test_seed_data_list_share_pages_for_user(app):
    """list_share_pages_for_user 返回直接授权 + 组继承的分享页。"""
    seed = app.state.seed
    user = seed.create_user(username="u_list", email="ul@example.com", password_hash="hash")
    group = seed.create_group(name="g_list")
    page_direct = seed.create_share_page(name="direct", ragflow_resource_id="d1")
    page_group = seed.create_share_page(name="group", ragflow_resource_id="d2")
    page_none = seed.create_share_page(name="none", ragflow_resource_id="d3")
    seed.create_grant(page_direct.id, "user", user.id, "use")
    seed.create_grant(page_group.id, "group", group.id, "use")
    seed.add_group_member(group.id, user.id)

    pages = seed.list_share_pages_for_user(user.id)
    page_ids = {p.id for p in pages}
    assert page_direct.id in page_ids
    assert page_group.id in page_ids
    assert page_none.id not in page_ids


def test_chat_session_owner_dataclass_untouched():
    """Slice 4 不破坏 chat_session_owner 数据模型(Slice 2 兼容)。"""
    owner = ChatSessionOwner(
        session_id="s1",
        share_page_id="sp",
        portal_user_id="u",
        ragflow_resource_id="d",
    )
    assert owner.session_id == "s1"
    # dataclass 默认 title 为空字符串;SessionStore.bind 用 `title or "新会话"` 兜底
    assert owner.title == ""
    assert owner.created_at > 0
    assert owner.last_active_at > 0
