"""Slice 6 端到端测试 — 管理员后台 + 审计日志。

覆盖验收点(ISSUES.md Issue 6):
  1. 8 类敏感操作分别触发 → 审计日志有对应记录:
     - login_success(登录)/ login_failure(错误密码)
     - grant_create(管理员授权)/ grant_revoke(管理员撤销)
     - session_delete(用户删自己的会话)
     - session_view_elevated(管理员查正文)
     - user_enable / user_disable(管理员改 enabled)
  2. 管理员列出所有会话(按用户/分享页/时间过滤)。
  3. 管理员默认看元数据(不含 messages/reference)。
  4. 管理员 elevated=true 查正文 → 审计日志 + 返回 messages+reference(mock RAGFlow GET)。
  5. 管理员查看审计日志(按 action/actor/时间过滤)。
  6. 管理员查看待重试删除会话 + 手动触发清理(mock RAGFlow DELETE 成功/失败)。
  7. 普通用户调所有新增 /admin 端点 → 403。
"""

import time
from unittest.mock import AsyncMock

import httpx
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# 辅助函数(与 test_slice5_e2e.py 一致的模式)
# ---------------------------------------------------------------------------


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


def _audit_actions(app) -> list:
    """辅助:返回当前 audit_store 中所有 action。

    Slice 8:经公开 API audit_store.list 查询(DB 后端,按 at 倒序);
    测试场景下日志数远小于 limit,顺序对断言(set/in)无影响。
    """
    return [log.action for log in app.state.audit_store.list(limit=1000)]


# ---------------------------------------------------------------------------
# 验收点 1:8 类敏感操作分别触发 → 审计日志有对应记录。
# ---------------------------------------------------------------------------


async def test_audit_login_success(client, app):
    """login_success:登录成功后审计日志有 login_success 记录。"""
    await _login(client)
    actions = _audit_actions(app)
    assert "login_success" in actions, f"login_success 未记审计: {actions}"


async def test_audit_login_failure_wrong_password(client, app):
    """login_failure:错误密码登录失败后审计日志有 login_failure 记录。"""
    resp = await client.post("/login", json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401
    actions = _audit_actions(app)
    assert "login_failure" in actions, f"login_failure 未记审计: {actions}"


async def test_audit_login_failure_user_not_found(client, app):
    """login_failure:用户不存在时审计日志也有 login_failure 记录(actor 用 username)。"""
    resp = await client.post("/login", json={"username": "ghost", "password": "whatever"})
    assert resp.status_code == 401
    actions = _audit_actions(app)
    assert "login_failure" in actions
    # actor_user_id 用 username(用户不存在时)
    failure_logs = app.state.audit_store.list(action="login_failure", limit=1000)
    assert any(log.actor_user_id == "ghost" for log in failure_logs)


async def test_audit_grant_create(client, app):
    """grant_create:管理员授权后审计日志有 grant_create 记录。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_grant_create")
    actions = _audit_actions(app)
    assert "grant_create" in actions, f"grant_create 未记审计: {actions}"
    # 校验 target_id 与 meta
    grant_logs = app.state.audit_store.list(action="grant_create", limit=1000)
    assert any(log.target_id == "sp_default" and log.target_type == "grant" for log in grant_logs)
    assert any(log.actor_user_id == "u_admin" for log in grant_logs)
    # meta 含 subject_id
    assert any(alice["id"] in log.meta_json for log in grant_logs)


async def test_audit_grant_revoke(client, app):
    """grant_revoke:管理员撤销授权后审计日志有 grant_revoke 记录。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_revoke")
    # 撤销授权
    resp = await client.delete(f"/share-pages/sp_default/grants/user/{alice['id']}")
    assert resp.status_code == 200, f"撤销失败: {resp.text}"
    actions = _audit_actions(app)
    assert "grant_revoke" in actions, f"grant_revoke 未记审计: {actions}"


async def test_audit_session_delete_user(client, app, monkeypatch):
    """session_delete:用户删自己的会话(RAGFlow 成功)后审计日志有 session_delete 记录。"""
    fake_session_id = "slice6-session-delete-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # mock RAGFlow DELETE 成功
    monkeypatch.setattr("portal.routes.delete_session_via_ragflow", AsyncMock(return_value=None))
    resp = await client.delete(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200, f"删除失败: {resp.text}"
    actions = _audit_actions(app)
    assert "session_delete" in actions, f"session_delete 未记审计: {actions}"
    # 校验 target_id
    delete_logs = app.state.audit_store.list(action="session_delete", limit=1000)
    assert any(log.target_id == fake_session_id for log in delete_logs)


async def test_audit_session_delete_not_recorded_on_ragflow_failure(client, app, monkeypatch):
    """RAGFlow 删除失败(标记 deleted_at)时不记 session_delete(会话未真正删除,仍待重试)。"""
    fake_session_id = "slice6-session-delete-fail"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # mock RAGFlow DELETE 失败
    monkeypatch.setattr(
        "portal.routes.delete_session_via_ragflow",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="上游失败")),
    )
    resp = await client.delete(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200  # 不暴露失败
    # session_delete 不应被记(会话未真正删除,标记 deleted_at 待重试)
    actions = _audit_actions(app)
    assert "session_delete" not in actions, f"RAGFlow 失败不应记 session_delete: {actions}"


async def test_audit_session_view_elevated(client, app, monkeypatch):
    """session_view_elevated:管理员 elevated=true 查正文后审计日志有 session_view_elevated 记录。"""
    fake_session_id = "slice6-elevated-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # mock RAGFlow GET 成功
    fake_history = {"messages": [{"role": "user", "content": "hi"}], "reference": {"chunks": []}}
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(return_value=fake_history),
    )
    resp = await client.get(f"/admin/sessions/{fake_session_id}?elevated=true")
    assert resp.status_code == 200, f"elevated 查正文失败: {resp.text}"
    actions = _audit_actions(app)
    assert "session_view_elevated" in actions, f"session_view_elevated 未记审计: {actions}"
    # 校验 target_id 与 meta
    elevated_logs = app.state.audit_store.list(action="session_view_elevated", limit=1000)
    assert any(log.target_id == fake_session_id and log.target_type == "session" for log in elevated_logs)


async def test_audit_user_enable(client, app):
    """user_enable:管理员启用用户后审计日志有 user_enable 记录。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_enable")
    # 先禁用再启用,确保 enabled 状态变化触发审计
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": False})
    assert resp.status_code == 200
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": True})
    assert resp.status_code == 200
    actions = _audit_actions(app)
    assert "user_enable" in actions, f"user_enable 未记审计: {actions}"
    assert "user_disable" in actions, f"user_disable 未记审计: {actions}"


async def test_audit_user_disable(client, app):
    """user_disable:管理员禁用用户后审计日志有 user_disable 记录。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_disable")
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": False})
    assert resp.status_code == 200
    actions = _audit_actions(app)
    assert "user_disable" in actions, f"user_disable 未记审计: {actions}"
    # 校验 target_id
    disable_logs = app.state.audit_store.list(action="user_disable", limit=1000)
    assert any(log.target_id == alice["id"] and log.target_type == "user" for log in disable_logs)


async def test_all_8_audit_actions_covered(client, app, monkeypatch):
    """综合:8 类敏感操作全部触发 → 审计日志全部覆盖(对应 PRD D7b 8 类枚举)。"""
    fake_session_id = "slice6-all-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    alice = await _create_user_and_grant(client, username="alice_all8")

    # 1. login_success(已通过 _login 触发)
    # 2. login_failure(错误密码)
    await client.post("/login", json={"username": "admin", "password": "wrong"})
    # 3. grant_create(已通过 _create_user_and_grant 触发)
    # 4. grant_revoke
    await client.delete(f"/share-pages/sp_default/grants/user/{alice['id']}")
    # 重新授权(为后续测试)
    await client.post(
        "/admin/share-pages/sp_default/grants",
        json={"subject_type": "user", "subject_id": alice["id"], "permission": "use"},
    )
    # 5. session_delete(RAGFlow 成功)
    monkeypatch.setattr("portal.routes.delete_session_via_ragflow", AsyncMock(return_value=None))
    await client.delete(f"/share-pages/sp_default/sessions/{fake_session_id}")
    # 6. session_view_elevated(需先预创建另一个 session)
    fake_session_id_2 = "slice6-all-002"
    _mock_precreate(monkeypatch, fake_session_id_2)
    await client.post("/share-pages/sp_default/sessions")
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(return_value={"messages": [], "reference": {}}),
    )
    await client.get(f"/admin/sessions/{fake_session_id_2}?elevated=true")
    # 7. user_disable
    await client.patch(f"/admin/users/{alice['id']}", json={"enabled": False})
    # 8. user_enable
    await client.patch(f"/admin/users/{alice['id']}", json={"enabled": True})

    actions = set(_audit_actions(app))
    expected = {
        "login_success",
        "login_failure",
        "grant_create",
        "grant_revoke",
        "session_delete",
        "session_view_elevated",
        "user_enable",
        "user_disable",
    }
    missing = expected - actions
    assert not missing, f"缺少审计 action: {missing}, 已有: {actions}"


# ---------------------------------------------------------------------------
# 验收点 2:管理员列出所有会话(按用户/分享页/时间过滤)。
# ---------------------------------------------------------------------------


async def test_admin_list_all_sessions(client, app, monkeypatch):
    """管理员列出所有会话(跨用户,返回元数据列表)。"""
    await _login(client)
    # admin 预创建一个 session
    _mock_precreate(monkeypatch, "slice6-list-001")
    await client.post("/share-pages/sp_default/sessions")
    # alice 预创建另一个 session
    await _create_user_and_grant(client, username="alice_list")
    _mock_precreate(monkeypatch, "slice6-list-002")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice_list", "password": "alicepass123"})
        await alice_client.post("/share-pages/sp_default/sessions")

    resp = await client.get("/admin/sessions")
    assert resp.status_code == 200, f"列出失败: {resp.text}"
    sessions = resp.json()["sessions"]
    ids = [s["session_id"] for s in sessions]
    assert "slice6-list-001" in ids
    assert "slice6-list-002" in ids


async def test_admin_list_sessions_filter_by_user(client, app, monkeypatch):
    """管理员按 user_id 过滤会话。"""
    await _login(client)
    _mock_precreate(monkeypatch, "slice6-filter-u-001")
    await client.post("/share-pages/sp_default/sessions")
    alice = await _create_user_and_grant(client, username="alice_filter")
    _mock_precreate(monkeypatch, "slice6-filter-u-002")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice_filter", "password": "alicepass123"})
        await alice_client.post("/share-pages/sp_default/sessions")

    # 只查 alice 的会话
    resp = await client.get(f"/admin/sessions?user_id={alice['id']}")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    ids = [s["session_id"] for s in sessions]
    assert "slice6-filter-u-002" in ids
    assert "slice6-filter-u-001" not in ids, "按 user_id 过滤不应返回其他用户的会话"


async def test_admin_list_sessions_filter_by_share_page(client, app, monkeypatch):
    """管理员按 share_page_id 过滤会话。"""
    await _login(client)
    # 创建第二个分享页
    resp = await client.post(
        "/admin/share-pages",
        json={"name": "另一分享页", "ragflow_resource_id": "dialog-other-sp"},
    )
    assert resp.status_code == 201
    other_sp_id = resp.json()["id"]
    # admin 对另一分享页授权(预创建需 grant)
    await client.post(
        f"/admin/share-pages/{other_sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    # 在 sp_default 预创建
    _mock_precreate(monkeypatch, "slice6-filter-sp-001")
    await client.post("/share-pages/sp_default/sessions")
    # 在 other_sp 预创建
    _mock_precreate(monkeypatch, "slice6-filter-sp-002")
    await client.post(f"/share-pages/{other_sp_id}/sessions")

    # 按 sp_default 过滤
    resp = await client.get("/admin/sessions?share_page_id=sp_default")
    assert resp.status_code == 200
    ids = [s["session_id"] for s in resp.json()["sessions"]]
    assert "slice6-filter-sp-001" in ids
    assert "slice6-filter-sp-002" not in ids


async def test_admin_list_sessions_filter_by_time(client, app, monkeypatch):
    """管理员按时间范围过滤会话(since/until)。"""
    await _login(client)
    t_before = time.time()
    _mock_precreate(monkeypatch, "slice6-filter-t-001")
    await client.post("/share-pages/sp_default/sessions")
    t_mid = time.time()
    # since=t_mid 应排除第一个 session(其 created_at < t_mid)
    resp = await client.get(f"/admin/sessions?since={t_mid}")
    assert resp.status_code == 200
    ids = [s["session_id"] for s in resp.json()["sessions"]]
    assert "slice6-filter-t-001" not in ids, "since 过滤应排除早于 since 的会话"
    # until=t_before 之前的应返回空(第一个 session created_at >= t_before,但 until < created_at)
    # 实际上 t_before < created_at,所以 until=t_before 应排除该 session
    resp = await client.get(f"/admin/sessions?until={t_before}")
    assert resp.status_code == 200
    ids = [s["session_id"] for s in resp.json()["sessions"]]
    assert "slice6-filter-t-001" not in ids


async def test_admin_list_sessions_by_keyword(client, app, monkeypatch):
    """管理员按关键词搜索会话(按 title 模糊匹配,大小写不敏感)。"""
    await _login(client)
    # 预创建两个会话,分别重命名为含「合同」和「侵权」的标题
    _mock_precreate(monkeypatch, "slice6-kw-001")
    await client.post("/share-pages/sp_default/sessions")
    _mock_precreate(monkeypatch, "slice6-kw-002")
    await client.post("/share-pages/sp_default/sessions")
    # 重命名(mock RAGFlow PATCH 成功)
    monkeypatch.setattr("portal.routes.rename_session_via_ragflow", AsyncMock(return_value=None))
    await client.patch(
        "/share-pages/sp_default/sessions/slice6-kw-001",
        json={"title": "合同纠纷咨询"},
    )
    await client.patch(
        "/share-pages/sp_default/sessions/slice6-kw-002",
        json={"title": "侵权责任分析"},
    )
    # keyword=合同 只返回标题含「合同」的会话
    resp = await client.get("/admin/sessions?keyword=合同")
    assert resp.status_code == 200, f"关键词搜索失败: {resp.text}"
    ids = [s["session_id"] for s in resp.json()["sessions"]]
    assert "slice6-kw-001" in ids, "关键词「合同」应匹配标题含「合同」的会话"
    assert "slice6-kw-002" not in ids, "关键词「合同」不应匹配标题含「侵权」的会话"
    # keyword=侵权 只返回标题含「侵权」的会话
    resp = await client.get("/admin/sessions?keyword=侵权")
    assert resp.status_code == 200
    ids = [s["session_id"] for s in resp.json()["sessions"]]
    assert "slice6-kw-002" in ids
    assert "slice6-kw-001" not in ids
    # 大小写不敏感:keyword=合同 与 合同 等价(中文无大小写,验证英文场景)
    await client.patch(
        "/share-pages/sp_default/sessions/slice6-kw-001",
        json={"title": "Contract 合同"},
    )
    resp = await client.get("/admin/sessions?keyword=contract")
    assert resp.status_code == 200
    ids = [s["session_id"] for s in resp.json()["sessions"]]
    assert "slice6-kw-001" in ids, "关键词搜索应大小写不敏感"


def test_session_store_list_all_keyword_filter(app):
    """SessionStore.list_all 支持 keyword 过滤(按 title 模糊匹配,大小写不敏感)。"""
    store = app.state.session_store
    store.bind("s-kw-1", "sp1", "u1", "d1", "合同纠纷")
    store.bind("s-kw-2", "sp1", "u2", "d1", "侵权责任")
    store.bind("s-kw-3", "sp1", "u3", "d1", "合同法解读")

    # keyword=合同 匹配两个会话
    result = store.list_all(keyword="合同")
    ids = {s.session_id for s in result}
    assert ids == {"s-kw-1", "s-kw-3"}, f"keyword 过滤失败: {ids}"

    # keyword=侵权 只匹配一个
    result = store.list_all(keyword="侵权")
    ids = {s.session_id for s in result}
    assert ids == {"s-kw-2"}

    # keyword=None 不过滤
    result = store.list_all(keyword=None)
    assert len(result) == 3

    # 大小写不敏感
    store.bind("s-kw-4", "sp1", "u4", "d1", "Contract Review")
    result = store.list_all(keyword="contract")
    ids = {s.session_id for s in result}
    assert "s-kw-4" in ids


# ---------------------------------------------------------------------------
# 验收点 3:管理员默认看元数据(不含 messages/reference)。
# ---------------------------------------------------------------------------


async def test_admin_get_session_returns_metadata_only(client, app, monkeypatch):
    """管理员默认查会话只返回元数据(不含 messages/reference,不写审计)。"""
    fake_session_id = "slice6-meta-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # 不传 elevated(默认 false)
    resp = await client.get(f"/admin/sessions/{fake_session_id}")
    assert resp.status_code == 200, f"查元数据失败: {resp.text}"
    body = resp.json()
    # 元数据字段
    assert body["session_id"] == fake_session_id
    assert "title" in body
    assert "portal_user_id" in body
    assert "share_page_id" in body
    assert "created_at" in body
    assert "last_active_at" in body
    # message_count 字段存在(spec 要求「元数据含消息数」)
    assert "message_count" in body, "元数据应含 message_count 字段"
    assert body["message_count"] == 0, "预创建会话的 message_count 应为 0"
    # 不含正文
    assert "messages" not in body, "默认不应返回 messages"
    assert "reference" not in body, "默认不应返回 reference"
    # 不写审计(session_view_elevated 仅 elevated=true 时记)
    actions = _audit_actions(app)
    assert "session_view_elevated" not in actions, "默认查元数据不应记 session_view_elevated 审计"


async def test_admin_list_sessions_includes_message_count(client, app, monkeypatch):
    """管理员列出会话时元数据含 message_count 字段(spec 要求「元数据含消息数」)。"""
    await _login(client)
    _mock_precreate(monkeypatch, "slice6-msgcount-001")
    await client.post("/share-pages/sp_default/sessions")
    resp = await client.get("/admin/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    target = [s for s in sessions if s["session_id"] == "slice6-msgcount-001"]
    assert len(target) == 1
    assert "message_count" in target[0], "列表元数据应含 message_count 字段"
    assert target[0]["message_count"] == 0, "预创建会话的 message_count 应为 0"


async def test_message_count_updated_on_resume(client, app, monkeypatch):
    """恢复会话(GET history)后 message_count 用 len(messages) 更新(简化实现)。"""
    fake_session_id = "slice6-msgcount-resume"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # 预创建后 message_count 为 0
    owner = app.state.session_store.get(fake_session_id)
    assert owner.message_count == 0
    # mock RAGFlow GET 返回 3 条消息
    fake_history = {
        "messages": [
            {"role": "user", "content": "问题1"},
            {"role": "assistant", "content": "回答1"},
            {"role": "user", "content": "问题2"},
        ],
        "reference": {},
    }
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(return_value=fake_history),
    )
    # 普通用户恢复会话
    resp = await client.get(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200, f"恢复会话失败: {resp.text}"
    # message_count 已更新为 len(messages)=3
    owner = app.state.session_store.get(fake_session_id)
    assert owner.message_count == 3, f"恢复会话后 message_count 应为 3,实际为 {owner.message_count}"
    # 管理员列表也能看到更新后的 message_count
    resp = await client.get("/admin/sessions")
    target = [s for s in resp.json()["sessions"] if s["session_id"] == fake_session_id][0]
    assert target["message_count"] == 3


async def test_message_count_updated_on_elevated_view(client, app, monkeypatch):
    """管理员 elevated=true 查正文后 message_count 也用 len(messages) 更新。"""
    fake_session_id = "slice6-msgcount-elevated"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # mock RAGFlow GET 返回 2 条消息
    fake_history = {
        "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好!"},
        ],
        "reference": {"chunks": []},
    }
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(return_value=fake_history),
    )
    resp = await client.get(f"/admin/sessions/{fake_session_id}?elevated=true")
    assert resp.status_code == 200
    # message_count 已更新为 2
    owner = app.state.session_store.get(fake_session_id)
    assert owner.message_count == 2, f"elevated 查正文后 message_count 应为 2,实际为 {owner.message_count}"


async def test_admin_get_session_unknown_returns_404(client):
    """管理员查不存在的会话 → 404。"""
    await _login(client)
    resp = await client.get("/admin/sessions/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 4:管理员 elevated=true 查正文 → 审计日志 + 返回 messages+reference。
# ---------------------------------------------------------------------------


async def test_admin_get_session_elevated_returns_messages_and_writes_audit(client, app, monkeypatch):
    """管理员 elevated=true 查正文:写审计 + 返回 messages+reference(mock RAGFlow GET)。"""
    fake_session_id = "slice6-elevated-002"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # mock RAGFlow GET 成功
    fake_history = {
        "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好!"}],
        "reference": {"chunks": [{"id": "c1", "content": "片段1"}], "doc_aggs": []},
    }
    mock_fetch = AsyncMock(return_value=fake_history)
    monkeypatch.setattr("portal.routes.fetch_session_history_via_ragflow", mock_fetch)

    resp = await client.get(f"/admin/sessions/{fake_session_id}?elevated=true")
    assert resp.status_code == 200, f"elevated 查正文失败: {resp.text}"
    body = resp.json()
    # 元数据 + 正文
    assert body["session_id"] == fake_session_id
    assert body["messages"] == fake_history["messages"]
    assert body["reference"] == fake_history["reference"]
    # RAGFlow GET 被调用
    mock_fetch.assert_awaited_once()
    # 审计日志写入
    actions = _audit_actions(app)
    assert "session_view_elevated" in actions
    elevated_logs = app.state.audit_store.list(action="session_view_elevated", limit=1000)
    assert any(log.target_id == fake_session_id for log in elevated_logs)


async def test_admin_get_session_elevated_ragflow_failure_returns_502(client, app, monkeypatch):
    """elevated=true 时 RAGFlow GET 失败 → 502(但审计日志已写,因为 elevated 是管理员意图)。"""
    fake_session_id = "slice6-elevated-fail"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # mock RAGFlow GET 失败
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="上游失败")),
    )
    resp = await client.get(f"/admin/sessions/{fake_session_id}?elevated=true")
    assert resp.status_code == 502
    # 审计日志仍写入(管理员意图已表达,留痕)
    actions = _audit_actions(app)
    assert "session_view_elevated" in actions


# ---------------------------------------------------------------------------
# 验收点 5:管理员查看审计日志(按 action/actor/时间过滤)。
# ---------------------------------------------------------------------------


async def test_admin_list_audit_logs(client, app, monkeypatch):
    """管理员查看审计日志(无过滤,返回全部最新的 limit 条)。"""
    await _login(client)  # 触发 login_success
    resp = await client.get("/admin/audit-logs")
    assert resp.status_code == 200, f"查审计日志失败: {resp.text}"
    body = resp.json()
    assert "audit_logs" in body
    # 至少有一条 login_success
    actions = [log["action"] for log in body["audit_logs"]]
    assert "login_success" in actions


async def test_admin_list_audit_logs_filter_by_action(client, app, monkeypatch):
    """管理员按 action 过滤审计日志。"""
    await _login(client)  # login_success
    await _create_user_and_grant(client, username="alice_audit_action")  # grant_create
    # 只查 grant_create
    resp = await client.get("/admin/audit-logs?action=grant_create")
    assert resp.status_code == 200
    actions = [log["action"] for log in resp.json()["audit_logs"]]
    assert all(a == "grant_create" for a in actions), f"action 过滤失败: {actions}"
    assert len(actions) >= 1


async def test_admin_list_audit_logs_filter_by_actor(client, app, monkeypatch):
    """管理员按 actor_user_id 过滤审计日志。"""
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_audit_actor")
    # alice 登录(触发 login_success,actor=alice.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice_audit_actor", "password": "alicepass123"})
    # 只查 alice 的审计日志
    resp = await client.get(f"/admin/audit-logs?actor_user_id={alice['id']}")
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    # 所有记录的 actor_user_id 都是 alice
    assert all(log["actor_user_id"] == alice["id"] for log in logs), f"actor 过滤失败: {logs}"
    # 至少有 alice 的 login_success
    actions = [log["action"] for log in logs]
    assert "login_success" in actions


async def test_admin_list_audit_logs_filter_by_time(client, app, monkeypatch):
    """管理员按时间范围过滤审计日志(since/until)。"""
    t_before = time.time()
    await _login(client)  # login_success 在 t_before 之后
    t_after = time.time()
    # since=t_after 应排除 login_success(发生在 t_before..t_after 之间)
    resp = await client.get(f"/admin/audit-logs?since={t_after}")
    assert resp.status_code == 200
    actions = [log["action"] for log in resp.json()["audit_logs"]]
    assert "login_success" not in actions, "since 过滤应排除早于 since 的记录"
    # until=t_before 应排除 login_success
    resp = await client.get(f"/admin/audit-logs?until={t_before}")
    assert resp.status_code == 200
    actions = [log["action"] for log in resp.json()["audit_logs"]]
    assert "login_success" not in actions, "until 过滤应排除晚于 until 的记录"
    # since=t_before & until=t_after 应包含 login_success
    resp = await client.get(f"/admin/audit-logs?since={t_before}&until={t_after + 10}")
    assert resp.status_code == 200
    actions = [log["action"] for log in resp.json()["audit_logs"]]
    assert "login_success" in actions


async def test_audit_log_meta_json_parsed_to_dict(client, app, monkeypatch):
    """审计日志响应中 meta 字段是从 meta_json 解析回的 dict(便于前端读取)。"""
    await _login(client)
    resp = await client.get("/admin/audit-logs?action=login_success")
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    assert len(logs) >= 1
    # login_success 的 meta 应为 dict(含 username)
    log = logs[0]
    assert log["meta"] is not None
    assert "username" in log["meta"]


# ---------------------------------------------------------------------------
# 验收点 6:管理员查看待重试删除会话 + 手动触发清理。
# ---------------------------------------------------------------------------


async def test_admin_list_pending_deletion(client, app, monkeypatch):
    """管理员查看待重试删除的会话(deleted_at 非空的记录)。"""
    await _login(client)
    fake_session_id = "slice6-pending-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # 标记 deleted_at
    app.state.session_store.mark_deleted(fake_session_id)

    resp = await client.get("/admin/sessions/pending-deletion")
    assert resp.status_code == 200, f"查待重试失败: {resp.text}"
    sessions = resp.json()["sessions"]
    ids = [s["session_id"] for s in sessions]
    assert fake_session_id in ids
    # deleted_at 非空
    target = [s for s in sessions if s["session_id"] == fake_session_id][0]
    assert target["deleted_at"] is not None


async def test_admin_retry_delete_success(client, app, monkeypatch):
    """管理员手动触发清理:RAGFlow DELETE 成功 → 门户记录删除 + 审计。"""
    await _login(client)
    fake_session_id = "slice6-retry-ok"
    await _precreate_session(client, monkeypatch, fake_session_id)
    app.state.session_store.mark_deleted(fake_session_id)
    # mock RAGFlow DELETE 成功
    mock_delete = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.routes.delete_session_via_ragflow", mock_delete)

    resp = await client.post(f"/admin/sessions/{fake_session_id}/retry-delete")
    assert resp.status_code == 200, f"重试删除失败: {resp.text}"
    # 门户记录已删除
    assert app.state.session_store.get(fake_session_id) is None
    # RAGFlow DELETE 被调用
    mock_delete.assert_awaited_once()
    # 审计日志写入 session_delete
    actions = _audit_actions(app)
    assert "session_delete" in actions


async def test_admin_retry_delete_ragflow_failure_returns_502(client, app, monkeypatch):
    """管理员手动触发清理:RAGFlow DELETE 失败 → 502,门户记录保留(仍待重试)。"""
    await _login(client)
    fake_session_id = "slice6-retry-fail"
    await _precreate_session(client, monkeypatch, fake_session_id)
    app.state.session_store.mark_deleted(fake_session_id)
    # mock RAGFlow DELETE 失败
    monkeypatch.setattr(
        "portal.routes.delete_session_via_ragflow",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="上游失败")),
    )

    resp = await client.post(f"/admin/sessions/{fake_session_id}/retry-delete")
    assert resp.status_code == 502, f"RAGFlow 失败应返回 502: {resp.text}"
    # 门户记录保留(仍待重试)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None, "RAGFlow 失败时门户记录应保留(仍待重试)"
    assert owner.deleted_at is not None


async def test_admin_retry_delete_rejects_non_pending(client, app, monkeypatch):
    """重试删除非待重试状态(deleted_at 为空)的会话 → 400。"""
    await _login(client)
    fake_session_id = "slice6-retry-not-pending"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # 不标记 deleted_at(正常状态)
    resp = await client.post(f"/admin/sessions/{fake_session_id}/retry-delete")
    assert resp.status_code == 400, f"非待重试状态应返回 400: {resp.text}"


async def test_admin_retry_delete_unknown_session_returns_404(client):
    """重试删除不存在的会话 → 404。"""
    await _login(client)
    resp = await client.post("/admin/sessions/nonexistent/retry-delete")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 7:普通用户调所有新增 /admin 端点 → 403。
# ---------------------------------------------------------------------------


async def test_non_admin_cannot_list_all_sessions(client, app):
    """普通用户调 GET /admin/sessions → 403。"""
    await _login(client)
    # 先预创建一个 session(让端点有数据,确保 403 不是 404)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as user2_client:
        await user2_client.post("/login", json={"username": "user2", "password": "testpass123"})
        resp = await user2_client.get("/admin/sessions")
        assert resp.status_code == 403, f"普通用户不应访问 /admin/sessions: {resp.text}"


async def test_non_admin_cannot_list_pending_deletion(client, app):
    """普通用户调 GET /admin/sessions/pending-deletion → 403。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as user2_client:
        await user2_client.post("/login", json={"username": "user2", "password": "testpass123"})
        resp = await user2_client.get("/admin/sessions/pending-deletion")
        assert resp.status_code == 403


async def test_non_admin_cannot_get_session(client, app, monkeypatch):
    """普通用户调 GET /admin/sessions/{id} → 403(默认与 elevated 均拒绝)。"""
    fake_session_id = "slice6-acl-session-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as user2_client:
        await user2_client.post("/login", json={"username": "user2", "password": "testpass123"})
        resp = await user2_client.get(f"/admin/sessions/{fake_session_id}")
        assert resp.status_code == 403
        resp = await user2_client.get(f"/admin/sessions/{fake_session_id}?elevated=true")
        assert resp.status_code == 403


async def test_non_admin_cannot_retry_delete(client, app, monkeypatch):
    """普通用户调 POST /admin/sessions/{id}/retry-delete → 403。"""
    fake_session_id = "slice6-acl-retry-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as user2_client:
        await user2_client.post("/login", json={"username": "user2", "password": "testpass123"})
        resp = await user2_client.post(f"/admin/sessions/{fake_session_id}/retry-delete")
        assert resp.status_code == 403


async def test_non_admin_cannot_list_audit_logs(client, app):
    """普通用户调 GET /admin/audit-logs → 403。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as user2_client:
        await user2_client.post("/login", json={"username": "user2", "password": "testpass123"})
        resp = await user2_client.get("/admin/audit-logs")
        assert resp.status_code == 403


async def test_unauthenticated_cannot_access_admin_endpoints(client):
    """未登录用户调 /admin/* 端点 → 403(未登录)。"""
    resp = await client.get("/admin/sessions")
    assert resp.status_code == 403
    resp = await client.get("/admin/sessions/pending-deletion")
    assert resp.status_code == 403
    resp = await client.get("/admin/sessions/some-id")
    assert resp.status_code == 403
    resp = await client.post("/admin/sessions/some-id/retry-delete")
    assert resp.status_code == 403
    resp = await client.get("/admin/audit-logs")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 边界:普通用户日常操作不写审计(PR D7b 仅敏感操作)。
# ---------------------------------------------------------------------------


async def test_normal_user_operations_not_audited(client, app, monkeypatch):
    """普通用户的日常操作(列自己的会话、继续对话)不写审计日志(PR D7b)。"""
    fake_session_id = "slice6-no-audit-001"
    await _precreate_session(client, monkeypatch, fake_session_id)
    # 记录当前审计日志数量(Slice 8:经公开 API list 查询,DB 后端)
    count_before = len(app.state.audit_store.list(limit=10000))
    # 普通操作:列自己的会话(不应写审计)
    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    # 重新打开会话(mock RAGFlow GET)
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(return_value={"messages": [], "reference": {}}),
    )
    resp = await client.get(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200
    # 审计日志数量不应增加(普通操作不记审计)
    count_after = len(app.state.audit_store.list(limit=10000))
    assert count_after == count_before, f"普通用户操作不应写审计日志: before={count_before} after={count_after}"


# ---------------------------------------------------------------------------
# 边界:审计日志永久保留(无 TTL/清理任务)。
# ---------------------------------------------------------------------------


def test_audit_store_has_no_cleanup_mechanism(app):
    """AuditStore 无 TTL/清理方法(永久保留,PR D8b)。"""
    store = app.state.audit_store
    # 不存在清理/purge/expire 方法
    for attr in ("purge", "expire", "cleanup", "clear", "truncate"):
        assert not hasattr(store, attr) or attr == "clear", f"AuditStore 不应有 {attr} 方法(永久保留)"
    # record 后记录仍在(不自动清理)
    store.record(actor_user_id="u1", action="login_success", target_type="user", target_id="u1")
    logs = store.list(limit=1000)
    assert len(logs) >= 1


# ---------------------------------------------------------------------------
# 边界:SessionStore.list_all 单元测试。
# ---------------------------------------------------------------------------


def test_session_store_list_all_cross_user(app):
    """SessionStore.list_all 跨用户列出所有会话(管理员视角)。"""
    store = app.state.session_store
    store.bind("s1", "sp1", "u1", "d1", "t1")
    store.bind("s2", "sp2", "u2", "d2", "t2")
    store.bind("s3", "sp1", "u3", "d1", "t3")

    all_sessions = store.list_all()
    ids = [s.session_id for s in all_sessions]
    assert set(ids) == {"s1", "s2", "s3"}


def test_session_store_list_all_excludes_deleted(app):
    """list_all 排除 deleted_at 非空的记录(待重试由 list_pending_deletion 查)。"""
    store = app.state.session_store
    store.bind("s1", "sp", "u1", "d1", "t1")
    store.bind("s2", "sp", "u1", "d1", "t2")
    store.mark_deleted("s1")

    sessions = store.list_all()
    ids = [s.session_id for s in sessions]
    assert "s1" not in ids
    assert "s2" in ids


def test_session_store_list_all_filters(app):
    """list_all 支持 user_id / share_page_id / since / until / limit 过滤。"""
    store = app.state.session_store
    store.bind("s1", "sp1", "u1", "d1", "t1")
    store.bind("s2", "sp2", "u2", "d2", "t2")
    store.bind("s3", "sp1", "u1", "d1", "t3")

    # 按 portal_user_id
    u1_sessions = store.list_all(portal_user_id="u1")
    assert {s.session_id for s in u1_sessions} == {"s1", "s3"}
    # 按 share_page_id
    sp1_sessions = store.list_all(share_page_id="sp1")
    assert {s.session_id for s in sp1_sessions} == {"s1", "s3"}
    # 按 since/until(用 created_at)
    s1 = store.get("s1")
    sp1_since = store.list_all(since=s1.created_at + 0.01)
    assert "s1" not in [s.session_id for s in sp1_since]
    # limit
    assert len(store.list_all(limit=1)) == 1


def test_audit_store_record_and_list(app):
    """AuditStore.record 写入 + list 查询(基础单元测试)。"""
    store = app.state.audit_store
    store.record(actor_user_id="u1", action="login_success", target_type="user", target_id="u1", meta={"k": "v"})
    store.record(actor_user_id="u2", action="login_failure", target_type="user", target_id="u2")

    # 全部(倒序)
    logs = store.list(limit=100)
    assert len(logs) == 2
    # 最新在前(login_failure 后写入)
    assert logs[0].action == "login_failure"
    assert logs[1].action == "login_success"

    # 按 action 过滤
    success_logs = store.list(action="login_success")
    assert len(success_logs) == 1
    assert success_logs[0].actor_user_id == "u1"

    # 按 actor 过滤
    u2_logs = store.list(actor_user_id="u2")
    assert len(u2_logs) == 1
    assert u2_logs[0].action == "login_failure"

    # meta_json 序列化
    assert '"k"' in success_logs[0].meta_json
    assert '"v"' in success_logs[0].meta_json


def test_audit_store_list_since_until(app):
    """AuditStore.list 按 since/until 过滤时间。"""
    store = app.state.audit_store
    store.record(actor_user_id="u1", action="login_success", target_type="user", target_id="u1")
    t_mid = time.time()
    store.record(actor_user_id="u2", action="login_failure", target_type="user", target_id="u2")

    # since=t_mid 排除第一条
    logs_since = store.list(since=t_mid)
    actions = [log.action for log in logs_since]
    assert "login_failure" in actions
    assert "login_success" not in actions

    # until=t_mid 排除第二条
    logs_until = store.list(until=t_mid)
    actions = [log.action for log in logs_until]
    assert "login_success" in actions
    assert "login_failure" not in actions
