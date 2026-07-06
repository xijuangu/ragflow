"""Slice 5 端到端测试 — 会话重命名/删除 + 双删 + 用户硬删除级联。

覆盖验收点(ISSUES.md Issue 5):
  1. 用户重命名自己的会话 → title 更新;RAGFlow name 也更新(mock PATCH 成功)。
  2. 用户重命名他人会话 → 403。
  3. 用户删除自己的会话 → chat_session_owner 删除;RAGFlow DELETE 调用(mock 成功)。
  4. 双删 RAGFlow 失败 → chat_session_owner.deleted_at 标记;记录仍在;返回 200。
  5. 管理员删除任意用户会话(双删,mock 成功)。
  6. 用户硬删除 → 级联删 chat_session_owner + RAGFlow API4Conversation(mock);禁用用户不删会话。
  7. RAGFlow PATCH/DELETE 端点存在(单元测试 mock 调用 URL 与方法)。
"""

import json
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException


def _extract_iframe_params(url: str) -> dict:
    """从 iframe URL 提取 query 参数。"""
    return parse_qs(urlparse(url).query)


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


def _mock_precreate(monkeypatch, session_id: str):
    """辅助:mock 网关的 precreate_session_via_ragflow 返回给定 session_id。"""
    monkeypatch.setattr(
        "portal.routes.precreate_session_via_ragflow",
        AsyncMock(return_value=session_id),
    )


async def _precreate_session(client, monkeypatch, session_id: str):
    """辅助:登录 + 预创建 session,返回响应体。"""
    await _login(client)
    _mock_precreate(monkeypatch, session_id)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    return resp.json()


async def _create_user_and_grant(client, username="alice"):
    """辅助:管理员创建普通用户并授予 sp_default use 权限,返回用户 dict。"""
    resp = await client.post(
        "/admin/users",
        json={"username": username, "email": f"{username}@example.com", "password": "alicepass123"},
    )
    assert resp.status_code == 201, f"创建用户失败: {resp.text}"
    user = resp.json()
    resp = await client.post(
        "/admin/share-pages/sp_default/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )
    assert resp.status_code == 201
    return user


# ---------------------------------------------------------------------------
# 验收点 1:用户重命名自己的会话 → title 更新;RAGFlow name 也更新。
# ---------------------------------------------------------------------------


async def test_rename_own_session_updates_title_and_calls_ragflow(client, app, monkeypatch):
    """用户重命名自己的会话:chat_session_owner.title 更新;RAGFlow PATCH 被调用。"""
    fake_session_id = "slice5-rename-001"
    await _precreate_session(client, monkeypatch, fake_session_id)

    # mock RAGFlow PATCH 成功
    mock_rename = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.routes.rename_session_via_ragflow", mock_rename)

    resp = await client.patch(
        f"/share-pages/sp_default/sessions/{fake_session_id}",
        json={"title": "我的新标题"},
    )
    assert resp.status_code == 200, f"重命名失败: {resp.text}"

    # 门户 title 更新
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None
    assert owner.title == "我的新标题"

    # RAGFlow PATCH 被调用
    mock_rename.assert_awaited_once()
    call_args = mock_rename.call_args
    all_args = list(call_args.args) + list(call_args.kwargs.values())
    assert fake_session_id in all_args, "rename_session_via_ragflow 应传入 session_id"


async def test_rename_session_ragflow_failure_returns_502_and_keeps_title(client, app, monkeypatch):
    """RAGFlow PATCH 失败 → 返回 502,门户 title 不更新(同步策略:不吞异常,两侧同步)。

    对应 ISSUES.md L213「RAGFlow 侧 name 与门户 title 同步更新」:
    RAGFlow 失败时若仍更新门户 title,会导致两侧不同步(门户显示新标题,RAGFlow 仍是旧名)。
    改为同步策略:RAGFlow 成功才更新门户 title;失败则 502,两侧都不变。
    """
    fake_session_id = "slice5-rename-001b"
    await _precreate_session(client, monkeypatch, fake_session_id)

    # 记录原标题,验证失败后不变
    original_title = app.state.session_store.get(fake_session_id).title

    # mock RAGFlow PATCH 失败(抛 502)
    monkeypatch.setattr(
        "portal.routes.rename_session_via_ragflow",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="上游失败")),
    )

    resp = await client.patch(
        f"/share-pages/sp_default/sessions/{fake_session_id}",
        json={"title": "新标题"},
    )
    # 同步策略:RAGFlow 失败 → 502(不吞异常,让用户感知失败)
    assert resp.status_code == 502, f"RAGFlow 失败应返回 502(同步策略): {resp.text}"

    # 门户 title 未更新(保持原标题,两侧同步)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None
    assert owner.title == original_title, "RAGFlow 失败时门户 title 不应更新(同步策略)"


# ---------------------------------------------------------------------------
# 验收点 2:用户重命名他人会话 → 403。
# ---------------------------------------------------------------------------


async def test_rename_other_users_session_returns_403(client, app, monkeypatch):
    """用户重命名他人会话 → 403(归属校验失败)。"""
    fake_session_id = "slice5-rename-002"
    # admin 预创建 session(归属 admin)
    await _precreate_session(client, monkeypatch, fake_session_id)

    # user2 登录并尝试重命名 admin 的 session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "user2", "password": "testpass123"})
        assert resp.status_code == 200
        resp = await client_b.patch(
            f"/share-pages/sp_default/sessions/{fake_session_id}",
            json={"title": "越权重命名"},
        )
        assert resp.status_code == 403, f"用户不应能重命名他人会话: {resp.text}"


# ---------------------------------------------------------------------------
# 验收点 3:用户删除自己的会话(双删成功)。
# ---------------------------------------------------------------------------


async def test_delete_own_session_dual_delete_success(client, app, monkeypatch):
    """用户删除自己的会话:chat_session_owner 删除;RAGFlow DELETE 被调用。"""
    fake_session_id = "slice5-delete-003"
    await _precreate_session(client, monkeypatch, fake_session_id)

    # mock RAGFlow DELETE 成功
    mock_delete = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.routes.delete_session_via_ragflow", mock_delete)

    resp = await client.delete(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200, f"删除失败: {resp.text}"

    # chat_session_owner 记录删除(硬删除)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is None, "chat_session_owner 应被删除"

    # RAGFlow DELETE 被调用
    mock_delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# 验收点 4:双删 RAGFlow 失败 → 标记 deleted_at,返回 200。
# ---------------------------------------------------------------------------


async def test_delete_own_session_ragflow_failure_marks_deleted_at(client, app, monkeypatch):
    """双删 RAGFlow 失败:chat_session_owner.deleted_at 标记;记录仍在;返回 200(不暴露失败)。"""
    fake_session_id = "slice5-delete-004"
    await _precreate_session(client, monkeypatch, fake_session_id)

    # mock RAGFlow DELETE 失败
    monkeypatch.setattr(
        "portal.routes.delete_session_via_ragflow",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="上游失败")),
    )

    resp = await client.delete(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200, f"RAGFlow 失败应返回 200 不暴露错误: {resp.text}"

    # chat_session_owner 记录仍在,但 deleted_at 被标记
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None, "记录应保留待重试"
    assert owner.deleted_at is not None, "deleted_at 应被标记"


async def test_deleted_session_not_in_user_list(client, app, monkeypatch):
    """deleted_at 标记的会话不在用户列表显示(但记录保留供重试)。"""
    fake_session_id = "slice5-delete-004b"
    await _precreate_session(client, monkeypatch, fake_session_id)

    # 标记 deleted_at
    app.state.session_store.mark_deleted(fake_session_id)

    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert not any(s["session_id"] == fake_session_id for s in sessions), "标记 deleted_at 的会话不应出现在列表"

    # 但记录仍在(供重试)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None


# ---------------------------------------------------------------------------
# 验收点 5:管理员删除任意用户会话(双删)。
# ---------------------------------------------------------------------------


async def test_admin_delete_any_session(client, app, monkeypatch):
    """管理员删除任意用户的会话(双删,不校验归属)。"""
    fake_session_id = "slice5-admin-delete-005"
    # admin 预创建 session(归属 admin)
    await _precreate_session(client, monkeypatch, fake_session_id)

    # mock RAGFlow DELETE 成功
    mock_delete = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.routes.delete_session_via_ragflow", mock_delete)

    # admin 删除该 session(管理员专用端点,不校验归属)
    resp = await client.delete(f"/admin/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200, f"管理员删除失败: {resp.text}"

    # chat_session_owner 记录删除
    owner = app.state.session_store.get(fake_session_id)
    assert owner is None


async def test_non_admin_cannot_delete_any_session(client, app, monkeypatch):
    """普通用户调管理员删除会话端点 → 403。"""
    fake_session_id = "slice5-acl-005b"
    await _precreate_session(client, monkeypatch, fake_session_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "user2", "password": "testpass123"})
        assert resp.status_code == 200
        resp = await client_b.delete(f"/admin/share-pages/sp_default/sessions/{fake_session_id}")
        assert resp.status_code == 403


async def test_admin_delete_session_rejects_share_page_mismatch(client, app, monkeypatch):
    """管理员删除会话时校验 share_page_id 一致性:不匹配 → 403。

    对应 review 修复项 3:admin_delete_session 与 rename_session/delete_session
    校验链一致(session 必须属于路径中的 share_page)。
    """
    await _login(client)
    # admin 预创建 session(归属 sp_default)
    fake_session_id = "slice5-admin-mismatch"
    await _precreate_session(client, monkeypatch, fake_session_id)

    # 管理员创建另一个分享页,然后用它的 id 去 delete sp_default 的 session
    resp = await client.post(
        "/admin/share-pages",
        json={"name": "另一分享页", "ragflow_resource_id": "dialog-other"},
    )
    assert resp.status_code == 201
    other_sp_id = resp.json()["id"]

    # 用 other_sp_id 路径删除 sp_default 的 session → 403(share_page_id 不匹配)
    resp = await client.delete(f"/admin/share-pages/{other_sp_id}/sessions/{fake_session_id}")
    assert resp.status_code == 403, f"share_page_id 不匹配应返回 403: {resp.text}"

    # session 仍在(未删除)
    assert app.state.session_store.get(fake_session_id) is not None


# ---------------------------------------------------------------------------
# 验收点 6:用户硬删除级联 + 禁用不删会话。
# ---------------------------------------------------------------------------


async def test_hard_delete_user_cascades_sessions(client, app, monkeypatch):
    """用户硬删除:级联删 chat_session_owner + RAGFlow API4Conversation(无孤儿)。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice")

    # alice 登录并预创建 session
    fake_session_id = "slice5-cascade-006"
    _mock_precreate(monkeypatch, fake_session_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        assert resp.status_code == 200
        resp = await alice_client.post("/share-pages/sp_default/sessions")
        assert resp.status_code == 200

    # 确认 session 存在且归属 alice
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None
    assert owner.portal_user_id == alice["id"]

    # mock RAGFlow DELETE 成功
    # 注:cascade_delete_for_user 在 models.py 内延迟导入 portal.gateway.delete_session_via_ragflow,
    # 故 mock gateway 模块的绑定(而非 routes 模块)
    mock_delete = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.gateway.delete_session_via_ragflow", mock_delete)

    # 管理员硬删除 alice
    resp = await client.delete(f"/admin/users/{alice['id']}")
    assert resp.status_code == 200, f"硬删除失败: {resp.text}"

    # alice 已删除
    assert app.state.seed.get_user(alice["id"]) is None
    assert app.state.seed.get_user_by_username("alice") is None

    # session 也被级联删除(无孤儿)
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after is None, "硬删除用户应级联删除其会话"

    # RAGFlow DELETE 被调用(对应 session)
    mock_delete.assert_awaited()


async def test_admin_delete_user_cleans_all_sessions_even_if_ragflow_fails(client, app, monkeypatch):
    """用户硬删除时 RAGFlow DELETE 失败的会话也硬删除门户侧记录(无孤儿)。

    对应 ISSUES.md L217「无孤儿」:用户硬删除后,所有会话(含 pending deletion 即
    deleted_at 非空的)都硬删除门户侧记录;RAGFlow 失败记日志供审计。
    理由:用户已不存在,保留 orphan 会话无法后续重试(无用户上下文,portal_user_id
    指向不存在的用户);RAGFlow 侧残留由管理员后续手动清理。
    """
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice")

    # alice 预创建两个 session,其中一个先标记 deleted_at(pending deletion)
    fake_session_ok = "slice5-cascade-ok"
    fake_session_fail = "slice5-cascade-fail"
    _mock_precreate(monkeypatch, fake_session_ok)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        await alice_client.post("/share-pages/sp_default/sessions")
    # 第二个 session(也归属 alice)
    _mock_precreate(monkeypatch, fake_session_fail)
    transport_b = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport_b, base_url="http://testserver") as alice_client_b:
        await alice_client_b.post("/login", json={"username": "alice", "password": "alicepass123"})
        await alice_client_b.post("/share-pages/sp_default/sessions")

    # 把第二个 session 标记为 pending deletion(deleted_at 非空),验证级联也清理它
    app.state.session_store.mark_deleted(fake_session_fail)
    assert app.state.session_store.get(fake_session_fail).deleted_at is not None

    # mock RAGFlow DELETE:对 fail session 抛 502,对 ok session 成功
    # 注:cascade_delete_for_user 在 models.py 内延迟导入 portal.gateway.delete_session_via_ragflow,
    # 故 mock gateway 模块的绑定(而非 routes 模块)
    async def _fake_delete(settings, dialog_id, session_id):
        if session_id == fake_session_fail:
            raise HTTPException(status_code=502, detail="上游失败")

    monkeypatch.setattr("portal.gateway.delete_session_via_ragflow", AsyncMock(side_effect=_fake_delete))

    # 硬删除 alice(RAGFlow 部分失败)
    resp = await client.delete(f"/admin/users/{alice['id']}")
    assert resp.status_code == 200, f"硬删除失败: {resp.text}"

    # alice 已删除
    assert app.state.seed.get_user(alice["id"]) is None
    assert app.state.seed.get_user_by_username("alice") is None

    # 所有会话门户侧记录都硬删除(无孤儿)— 包括 RAGFlow 失败的和 deleted_at 非空的
    assert app.state.session_store.get(fake_session_ok) is None, "RAGFlow 成功的会话应硬删除"
    assert app.state.session_store.get(fake_session_fail) is None, (
        "RAGFlow 失败的会话也应硬删除(无孤儿,用户已不存在无法重试)"
    )

    # 验证无孤儿:session_store 中无任何 portal_user_id 指向 alice 的记录
    # Slice 8:经公开 API list_all_for_user 查询(含 deleted_at 非空的记录,跨分享页)
    alice_sessions = app.state.session_store.list_all_for_user(alice["id"])
    assert alice_sessions == [], "硬删除用户后不应残留任何会话记录(无孤儿)"


async def test_disable_user_preserves_sessions(client, app, monkeypatch):
    """禁用用户不删会话(保留)— 禁用走 PATCH,不删会话。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice")

    fake_session_id = "slice5-disable-007"
    _mock_precreate(monkeypatch, fake_session_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        await alice_client.post("/share-pages/sp_default/sessions")

    # 禁用 alice(PATCH,不是 DELETE)
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": False})
    assert resp.status_code == 200

    # session 仍在(禁用不删会话)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None, "禁用用户不应删除会话"
    assert owner.deleted_at is None


async def test_hard_delete_unknown_user_returns_404(client):
    """硬删除不存在的用户 → 404。"""
    await _login(client)
    resp = await client.delete("/admin/users/u_nonexistent")
    assert resp.status_code == 404


async def test_non_admin_cannot_hard_delete_user(client, app):
    """普通用户调硬删除用户端点 → 403。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice", "password": "alicepass123"})
        resp = await alice_client.delete(f"/admin/users/{alice['id']}")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 验收点 7:RAGFlow PATCH/DELETE 端点存在(单元测试 mock 调用 URL 与方法)。
# ---------------------------------------------------------------------------


def _make_dummy_settings():
    """构造测试用 Settings(非真实凭据)。"""
    from portal.config import Settings

    return Settings(
        admin_username="a",
        admin_password="p",
        user2_username="u",
        user2_password="p",
        session_secret="s",
        ragflow_host="http://ragflow-mock.invalid",
        ragflow_beta_token="fake-beta-token",
        ragflow_dialog_id="d1",
        t_short_ttl_seconds=300,
        portal_db_url="sqlite://",  # Slice 8:dummy settings 仍需提供 DB URL 字段
    )


async def test_rename_session_via_ragflow_calls_patch(monkeypatch):
    """rename_session_via_ragflow 调 RAGFlow PATCH 端点(验证 URL 与方法)。"""
    from portal.gateway import rename_session_via_ragflow

    captured_requests = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req):
                captured_requests.append(req)
                return httpx.Response(200, json={"code": 0, "data": {"id": "s1", "name": "新名"}})

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    settings = _make_dummy_settings()
    await rename_session_via_ragflow(settings, "dialog-1", "session-1", "新标题")

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "PATCH"
    assert "/chatbots/dialog-1/sessions/session-1" in str(req.url)
    # body 含 name 字段
    body = json.loads(req.content)
    assert body["name"] == "新标题"


async def test_delete_session_via_ragflow_calls_delete(monkeypatch):
    """delete_session_via_ragflow 调 RAGFlow DELETE 端点(验证 URL 与方法)。"""
    from portal.gateway import delete_session_via_ragflow

    captured_requests = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req):
                captured_requests.append(req)
                return httpx.Response(200, json={"code": 0})

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    settings = _make_dummy_settings()
    await delete_session_via_ragflow(settings, "dialog-1", "session-1")

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "DELETE"
    assert "/chatbots/dialog-1/sessions/session-1" in str(req.url)


async def test_rename_session_via_ragflow_raises_on_failure(monkeypatch):
    """RAGFlow PATCH 非 200 → 抛 HTTPException(502)。"""
    from portal.gateway import rename_session_via_ragflow

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(500, json={"code": 500}))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    settings = _make_dummy_settings()
    with pytest.raises(HTTPException) as exc_info:
        await rename_session_via_ragflow(settings, "dialog-1", "session-1", "新标题")
    assert exc_info.value.status_code == 502


async def test_delete_session_via_ragflow_raises_on_failure(monkeypatch):
    """RAGFlow DELETE 非 200 → 抛 HTTPException(502)。"""
    from portal.gateway import delete_session_via_ragflow

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(500, json={"code": 500}))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    settings = _make_dummy_settings()
    with pytest.raises(HTTPException) as exc_info:
        await delete_session_via_ragflow(settings, "dialog-1", "session-1")
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# 边界:重命名/删除不存在的会话 → 404。
# ---------------------------------------------------------------------------


async def test_rename_unknown_session_returns_404(client):
    """重命名不存在的会话 → 404。"""
    await _login(client)
    resp = await client.patch(
        "/share-pages/sp_default/sessions/nonexistent",
        json={"title": "x"},
    )
    assert resp.status_code == 404


async def test_delete_unknown_session_returns_404(client):
    """删除不存在的会话 → 404。"""
    await _login(client)
    resp = await client.delete("/share-pages/sp_default/sessions/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 边界:SessionStore 单元测试(rename / delete / mark_deleted / list_pending_deletion)。
# ---------------------------------------------------------------------------


def test_session_store_rename_updates_title(app):
    """SessionStore.rename 更新 title。"""
    store = app.state.session_store
    store.bind("s1", "sp", "u1", "d1", "原标题")
    assert store.rename("s1", "新标题") is True
    assert store.get("s1").title == "新标题"
    # 不存在的 session
    assert store.rename("nonexistent", "x") is False


def test_session_store_delete_removes_record(app):
    """SessionStore.delete 硬删除记录(从存储移除)。"""
    store = app.state.session_store
    store.bind("s1", "sp", "u1", "d1", "t1")
    assert store.delete("s1") is True
    assert store.get("s1") is None
    # 不存在的 session
    assert store.delete("nonexistent") is False


def test_session_store_mark_deleted_sets_deleted_at(app):
    """SessionStore.mark_deleted 设置 deleted_at(记录保留)。"""
    store = app.state.session_store
    store.bind("s1", "sp", "u1", "d1", "t1")
    assert store.mark_deleted("s1") is True
    owner = store.get("s1")
    assert owner is not None
    assert owner.deleted_at is not None
    # 不存在的 session
    assert store.mark_deleted("nonexistent") is False


def test_session_store_list_for_user_excludes_deleted(app):
    """list_for_user 排除 deleted_at 非空的记录。"""
    store = app.state.session_store
    store.bind("s1", "sp", "u1", "d1", "t1")
    store.bind("s2", "sp", "u1", "d1", "t2")
    store.mark_deleted("s1")

    sessions = store.list_for_user("u1", "sp")
    ids = [s.session_id for s in sessions]
    assert "s1" not in ids
    assert "s2" in ids


def test_session_store_list_pending_deletion(app):
    """list_pending_deletion 返回 deleted_at 非空的会话。"""
    store = app.state.session_store
    store.bind("s1", "sp", "u1", "d1", "t1")
    store.bind("s2", "sp", "u1", "d1", "t2")
    store.mark_deleted("s1")

    pending = store.list_pending_deletion()
    pending_ids = [s.session_id for s in pending]
    assert "s1" in pending_ids
    assert "s2" not in pending_ids


def test_session_store_list_all_for_user(app):
    """list_all_for_user 返回用户的所有会话(跨分享页,用于硬删除级联)。"""
    store = app.state.session_store
    store.bind("s1", "sp1", "u1", "d1", "t1")
    store.bind("s2", "sp2", "u1", "d2", "t2")
    store.bind("s3", "sp1", "u2", "d1", "t3")

    user1_sessions = store.list_all_for_user("u1")
    ids = [s.session_id for s in user1_sessions]
    assert set(ids) == {"s1", "s2"}


def test_seed_data_delete_user_removes_user_and_group_memberships(app):
    """SeedData.delete_user 删除用户 + 清理组成员关系。"""
    seed = app.state.seed
    user = seed.create_user(username="delme", email="d@example.com", password_hash="h")
    group = seed.create_group(name="g")
    seed.add_group_member(group.id, user.id)
    assert user.id in seed.list_group_members(group.id)

    assert seed.delete_user(user.id) is True
    assert seed.get_user(user.id) is None
    assert seed.get_user_by_username("delme") is None
    # 组成员关系清理
    assert user.id not in seed.list_group_members(group.id)
    # 不存在的用户
    assert seed.delete_user("u_nonexistent") is False


# ---------------------------------------------------------------------------
# integration — 用真实 RAGFlow 验证 PATCH/DELETE 端点。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_rename_and_delete_with_real_ragflow(client, real_ragflow):
    """[integration] 用真实 RAGFlow 验证:预创建 → 重命名 → 删除。"""
    await _login(client)

    # 1. 预创建 session(调真实 RAGFlow)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    session_id = resp.json()["session_id"]
    assert session_id, "预创建未返回 session_id"

    # 2. 重命名(调真实 RAGFlow PATCH 端点 — 需服务器已部署 Slice 5 代码)
    resp = await client.patch(
        f"/share-pages/sp_default/sessions/{session_id}",
        json={"title": "集成测试标题"},
    )
    if resp.status_code != 200:
        pytest.skip(f"RAGFlow 服务器未部署 PATCH 端点(HTTP {resp.status_code}),需部署 Slice 5 代码")

    # 3. 删除(调真实 RAGFlow DELETE 端点)
    resp = await client.delete(f"/share-pages/sp_default/sessions/{session_id}")
    assert resp.status_code == 200, f"删除失败: {resp.text}"
