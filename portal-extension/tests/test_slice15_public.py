"""Slice 15 — 公开分享(is_public + IP 限流)测试。

覆盖 ISSUES.md Issue 15 验收点 1-7:
  1. 管理员能把分享页标记为 is_public=true,公开 URL 可免登录访问
  2. 公开分享页能正常对话(iframe 加载 + SSE 代理工作)
  3. 公开分享页按 IP 限流,超限返回 429
  4. 管理员关闭 is_public 后,公开 URL 立即返回 403
  5. 公开会话不绑定到具体 portal_user(匿名或特殊用户)
  6. 公开分享页的会话不进入普通用户的「我的会话」列表
  7. 公开访问也写审计(可选,由实现决定)

设计:
  - 用 SQLite in-memory + StaticPool(与 Slice 8 测试一致)。
  - 公开访问测试用「先 admin 标记 is_public → 清 cookie → 匿名访问」模式。
  - 限流测试用独立 app fixture(PUBLIC_RATE_LIMIT_PER_MIN=3,易触发 429)。
  - SSE 测试复用 conftest 的 mock_precreate / mock_ragflow_sse fixture。
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from portal.config import load_settings
from portal.db import create_session_maker, init_db
from portal.main import create_app
from portal.models import SharePage, build_seed_data

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _login_admin(client):
    """admin 登录并断言成功。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def _mark_public(client, share_page_id="sp_default", is_public=True):
    """管理员 PATCH is_public(允许同时传 enabled),返回更新后的分享页 dict。"""
    resp = await client.patch(
        f"/admin/share-pages/{share_page_id}",
        json={"is_public": is_public},
    )
    assert resp.status_code == 200, f"PATCH is_public 失败: {resp.text}"
    return resp.json()


def _extract_params(url: str) -> dict:
    """从 iframe URL 提取 query 参数。"""
    return parse_qs(urlparse(url).query)


def _make_sqlite_engine():
    """构造 SQLite in-memory engine(StaticPool 共享连接)。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


# ===========================================================================
# DB 迁移测试 — is_public 列
# ===========================================================================


@pytest.fixture
def db_engine():
    engine = _make_sqlite_engine()
    init_db(engine)
    yield engine
    engine.dispose()


def test_share_page_has_is_public_column(db_engine):
    """share_page 表有 is_public 列(Slice 15 迁移)。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("share_page")}
    assert "is_public" in cols, f"share_page 缺 is_public 列,实际: {cols}"


def test_migration_is_public_idempotent(db_engine):
    """init_db 重复执行不报错(is_public 列已存在时跳过,idempotent)。"""
    init_db(db_engine)  # 重复执行
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("share_page")}
    assert "is_public" in cols


def test_share_page_dataclass_has_is_public():
    """SharePage dataclass 有 is_public 字段(默认 False)。"""
    page = SharePage(id="sp_test", name="test")
    assert page.is_public is False


def test_seed_data_set_share_page_public(db_engine):
    """SeedData.set_share_page_public 能切换 is_public;不存在返回 False。"""
    sm = create_session_maker(db_engine)
    settings = load_settings()
    seed = build_seed_data(settings, sm)

    # 默认 False
    page = seed.get_share_page("sp_default")
    assert page.is_public is False

    # 设置为 True
    assert seed.set_share_page_public("sp_default", True) is True
    page = seed.get_share_page("sp_default")
    assert page.is_public is True

    # 设置回 False
    assert seed.set_share_page_public("sp_default", False) is True
    page = seed.get_share_page("sp_default")
    assert page.is_public is False

    # 不存在的分享页
    assert seed.set_share_page_public("nonexistent", True) is False


def test_build_seed_data_creates_anonymous_user(db_engine):
    """build_seed_data 创建 u_anonymous 用户(公开会话归属锚点)。"""
    sm = create_session_maker(db_engine)
    settings = load_settings()
    seed = build_seed_data(settings, sm)

    anonymous = seed.get_user("u_anonymous")
    assert anonymous is not None
    assert anonymous.username == "anonymous"
    assert anonymous.is_admin is False
    assert anonymous.enabled is True


# ===========================================================================
# 验收点 1:管理员能标记 is_public=true,公开 URL 免登录访问
# ===========================================================================


async def test_admin_can_set_is_public(client):
    """验收点 1:管理员能把分享页标记为 is_public=true。"""
    await _login_admin(client)
    page = await _mark_public(client, "sp_default", is_public=True)
    assert page["is_public"] is True


async def test_admin_can_unset_is_public(client):
    """验收点 1:管理员能关闭 is_public。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    page = await _mark_public(client, "sp_default", is_public=False)
    assert page["is_public"] is False


async def test_admin_patch_supports_enabled_and_is_public_together(client):
    """管理员 PATCH 可同时更新 enabled 与 is_public(向后兼容只传 enabled)。"""
    await _login_admin(client)
    resp = await client.patch(
        "/admin/share-pages/sp_default",
        json={"enabled": True, "is_public": True},
    )
    assert resp.status_code == 200
    page = resp.json()
    assert page["enabled"] is True
    assert page["is_public"] is True


async def test_admin_patch_enabled_only_still_works(client):
    """向后兼容:仅传 enabled 不传 is_public 时,is_public 保持原值。"""
    await _login_admin(client)
    # 先设 is_public=True
    await _mark_public(client, "sp_default", is_public=True)
    # 只传 enabled(不传 is_public)
    resp = await client.patch("/admin/share-pages/sp_default", json={"enabled": False})
    assert resp.status_code == 200
    page = resp.json()
    assert page["enabled"] is False
    assert page["is_public"] is True  # 保持原值


async def test_public_page_accessible_without_login(client):
    """验收点 1:公开分享页可免登录访问(GET /public/<id> 返回 200 HTML)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()  # 登出,模拟匿名访问

    resp = await client.get("/public/sp_default")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


async def test_public_page_403_for_non_public(client):
    """验收点 1:非公开分享页 → 403。"""
    # sp_default 默认 is_public=False
    resp = await client.get("/public/sp_default")
    assert resp.status_code == 403


async def test_public_page_404_for_unknown(client):
    """公开页不存在 → 404。"""
    resp = await client.get("/public/nonexistent")
    assert resp.status_code == 404


async def test_public_page_403_for_disabled_share_page(client):
    """公开页但分享页已禁用 → 403(enabled=false 视为不可用)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    # 禁用分享页
    await client.patch("/admin/share-pages/sp_default", json={"enabled": False})
    client.cookies.clear()

    resp = await client.get("/public/sp_default")
    assert resp.status_code == 403


# ===========================================================================
# 验收点 2:公开分享页能正常对话(iframe 加载 + SSE 代理工作)
# ===========================================================================


async def test_public_embed_url_issues_t_short(client):
    """验收点 2:GET /public/<id>/embed-url 签发公开 T_short(不含真实 beta Token)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    resp = await client.get("/public/sp_default/embed-url")
    assert resp.status_code == 200
    data = resp.json()
    assert "iframe_url" in data
    params = _extract_params(data["iframe_url"])
    assert "auth" in params  # T_short
    assert "shared_id" in params  # dialog_id
    # 不含真实 beta Token
    assert "fake-beta-token-for-unit-tests" not in data["iframe_url"]


async def test_public_embed_url_403_for_non_public(client):
    """非公开分享页的 embed-url → 403。"""
    resp = await client.get("/public/sp_default/embed-url")
    assert resp.status_code == 403


async def test_public_precreate_session(client, mock_precreate):
    """验收点 2:POST /public/<id>/sessions 预创建匿名会话(返回 iframe_url)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-public-session-id")
    resp = await client.post("/public/sp_default/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "fake-public-session-id"
    assert "iframe_url" in data
    # iframe URL 含 session_id 参数
    params = _extract_params(data["iframe_url"])
    assert params.get("session_id") == ["fake-public-session-id"]


async def test_public_session_bound_to_anonymous(client, app, mock_precreate):
    """验收点 5:公开会话绑定到 u_anonymous(不绑定到具体 portal_user)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-public-session-id")
    await client.post("/public/sp_default/sessions")

    owner = app.state.session_store.get("fake-public-session-id")
    assert owner is not None
    assert owner.portal_user_id == "u_anonymous"


async def test_public_chat_sse_works(client, mock_precreate, mock_ragflow_sse):
    """验收点 2:公开 SSE 代理工作(POST /public/<id>/sessions/<sid>/chat)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-public-session-id")
    await client.post("/public/sp_default/sessions")

    # 获取公开 T_short
    resp = await client.get("/public/sp_default/embed-url")
    t_short = _extract_params(resp.json()["iframe_url"])["auth"][0]

    mock_ragflow_sse(session_id="fake-public-session-id", message_id="msg-1")

    resp = await client.post(
        "/public/sp_default/sessions/fake-public-session-id/chat",
        headers={"Authorization": f"Bearer {t_short}"},
        json={"question": "你好", "stream": True, "session_id": "fake-public-session-id"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert b"answer" in resp.content


async def test_public_chat_401_without_t_short(client, mock_precreate):
    """公开 SSE 端点缺 T_short → 401。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-public-session-id")
    await client.post("/public/sp_default/sessions")

    resp = await client.post(
        "/public/sp_default/sessions/fake-public-session-id/chat",
        json={"question": "你好", "stream": True, "session_id": "fake-public-session-id"},
    )
    assert resp.status_code == 401


async def test_standard_path_works_with_public_t_short(client, mock_precreate, mock_ragflow_sse):
    """验收点 2:标准 SSE 路径也支持公开 T_short(iframe 同源加载兼容)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-public-session-id")
    await client.post("/public/sp_default/sessions")

    resp = await client.get("/public/sp_default/embed-url")
    iframe_url = resp.json()["iframe_url"]
    t_short = _extract_params(iframe_url)["auth"][0]
    dialog_id = _extract_params(iframe_url)["shared_id"][0]

    mock_ragflow_sse(session_id="fake-public-session-id", message_id="msg-1")

    # 调用标准 SSE 路径(无需登录 cookie)— iframe 内 RAGFlow 前端发起的请求
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        headers={"Authorization": f"Bearer {t_short}"},
        json={"question": "你好", "stream": True, "session_id": "fake-public-session-id"},
    )
    assert resp.status_code == 200
    assert b"answer" in resp.content


# ===========================================================================
# 验收点 3:公开分享页按 IP 限流,超限返回 429
# ===========================================================================


@pytest.fixture
def limited_app(monkeypatch):
    """限流=3/min 的 app(PUBLIC_RATE_LIMIT_PER_MIN=3,易触发 429)。"""
    monkeypatch.setenv("PUBLIC_RATE_LIMIT_PER_MIN", "3")
    return create_app()


@pytest.fixture
async def limited_client(limited_app):
    transport = httpx.ASGITransport(app=limited_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_public_rate_limit_returns_429(limited_client, mock_precreate, mock_ragflow_sse):
    """验收点 3:公开 SSE 按 IP 限流,超限返回 429。"""
    await _login_admin(limited_client)
    await _mark_public(limited_client, "sp_default", is_public=True)
    limited_client.cookies.clear()

    mock_precreate("fake-session")
    await limited_client.post("/public/sp_default/sessions")

    resp = await limited_client.get("/public/sp_default/embed-url")
    t_short = _extract_params(resp.json()["iframe_url"])["auth"][0]

    mock_ragflow_sse(session_id="fake-session", message_id="msg-1")

    # 3 次请求(限流内)
    for i in range(3):
        resp = await limited_client.post(
            "/public/sp_default/sessions/fake-session/chat",
            headers={"Authorization": f"Bearer {t_short}"},
            json={"question": f"问题{i}", "stream": True, "session_id": "fake-session"},
        )
        assert resp.status_code == 200, f"第 {i+1} 次请求应成功: {resp.status_code} {resp.text}"

    # 第 4 次超限 → 429
    resp = await limited_client.post(
        "/public/sp_default/sessions/fake-session/chat",
        headers={"Authorization": f"Bearer {t_short}"},
        json={"question": "超限", "stream": True, "session_id": "fake-session"},
    )
    assert resp.status_code == 429


async def test_login_user_not_rate_limited(limited_client, mock_precreate, mock_ragflow_sse):
    """验收点 3:登录用户不限流(限流只对公开端点生效)。"""
    await _login_admin(limited_client)
    await _mark_public(limited_client, "sp_default", is_public=True)
    # 不 clear cookies,保持登录状态

    mock_precreate("fake-session")
    await limited_client.post("/share-pages/sp_default/sessions")

    resp = await limited_client.get("/share-pages/sp_default/embed-url")
    t_short = _extract_params(resp.json()["iframe_url"])["auth"][0]
    dialog_id = _extract_params(resp.json()["iframe_url"])["shared_id"][0]

    mock_ragflow_sse(session_id="fake-session", message_id="msg-1")

    # 5 次请求(超过公开限流 3,但登录用户不限流)
    for i in range(5):
        resp = await limited_client.post(
            f"/api/v1/chatbots/{dialog_id}/completions",
            headers={"Authorization": f"Bearer {t_short}"},
            json={"question": f"问题{i}", "stream": True, "session_id": "fake-session"},
        )
        assert resp.status_code == 200, f"登录用户第 {i+1} 次请求应成功: {resp.status_code}"


# ===========================================================================
# 验收点 4:管理员关闭 is_public 后,公开 URL 立即返回 403
# ===========================================================================


async def test_close_is_public_returns_403(client, mock_precreate):
    """验收点 4:管理员关闭 is_public 后,公开 URL 立即返回 403。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    # 验证公开时可访问
    resp = await client.get("/public/sp_default")
    assert resp.status_code == 200

    # 管理员关闭 is_public
    client.cookies.clear()
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=False)
    client.cookies.clear()

    # 立即返回 403
    resp = await client.get("/public/sp_default")
    assert resp.status_code == 403

    resp = await client.get("/public/sp_default/embed-url")
    assert resp.status_code == 403


async def test_close_is_public_invalidates_existing_t_short(client, mock_precreate, mock_ragflow_sse):
    """验收点 4:关闭 is_public 后,已签发的公开 T_short 也立即失效(403)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-session")
    await client.post("/public/sp_default/sessions")

    resp = await client.get("/public/sp_default/embed-url")
    t_short = _extract_params(resp.json()["iframe_url"])["auth"][0]

    # 管理员关闭 is_public
    client.cookies.clear()
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=False)
    client.cookies.clear()

    mock_ragflow_sse(session_id="fake-session", message_id="msg-1")

    # 已签发的 T_short 也失效(403,不是 401)
    resp = await client.post(
        "/public/sp_default/sessions/fake-session/chat",
        headers={"Authorization": f"Bearer {t_short}"},
        json={"question": "你好", "stream": True, "session_id": "fake-session"},
    )
    assert resp.status_code == 403


# ===========================================================================
# 验收点 6:公开分享页的会话不进入普通用户的「我的会话」列表
# ===========================================================================


async def test_public_sessions_not_in_user_list(client, mock_precreate):
    """验收点 6:公开会话不进入普通用户的「我的会话」列表。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    # 公开用户创建会话(绑定到 u_anonymous)
    mock_precreate("public-session-1")
    await client.post("/public/sp_default/sessions")

    # admin 登录查看自己的会话列表
    client.cookies.clear()
    await _login_admin(client)
    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    session_ids = [s["session_id"] for s in sessions]
    assert "public-session-1" not in session_ids


async def test_public_sessions_visible_to_admin_all_sessions(client, mock_precreate):
    """公开会话对管理员全量会话列表可见(按 portal_user_id=u_anonymous 过滤)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("public-session-1")
    await client.post("/public/sp_default/sessions")

    # admin 查全量会话(可按 portal_user_id=u_anonymous 过滤看到公开会话)
    client.cookies.clear()
    await _login_admin(client)
    resp = await client.get("/admin/sessions?user_id=u_anonymous")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    session_ids = [s["session_id"] for s in sessions]
    assert "public-session-1" in session_ids


# ===========================================================================
# 验收点 7:公开访问也写审计
# ===========================================================================


async def test_public_chat_writes_audit(client, mock_precreate, mock_ragflow_sse):
    """验收点 7:公开 SSE 请求写审计(actor=u_anonymous, action=public_chat)。"""
    await _login_admin(client)
    await _mark_public(client, "sp_default", is_public=True)
    client.cookies.clear()

    mock_precreate("fake-session")
    await client.post("/public/sp_default/sessions")

    resp = await client.get("/public/sp_default/embed-url")
    t_short = _extract_params(resp.json()["iframe_url"])["auth"][0]

    mock_ragflow_sse(session_id="fake-session", message_id="msg-1")
    await client.post(
        "/public/sp_default/sessions/fake-session/chat",
        headers={"Authorization": f"Bearer {t_short}"},
        json={"question": "你好", "stream": True, "session_id": "fake-session"},
    )

    # admin 查审计日志
    client.cookies.clear()
    await _login_admin(client)
    resp = await client.get("/admin/audit-logs?action=public_chat")
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    assert len(logs) >= 1
    assert logs[0]["action"] == "public_chat"
    assert logs[0]["actor_user_id"] == "u_anonymous"
    assert logs[0]["target_type"] == "session"


async def test_public_audit_can_be_disabled(monkeypatch, mock_precreate, mock_ragflow_sse):
    """验收点 7:公开审计可配置关闭(PUBLIC_AUDIT_ENABLED=false 时不写审计)。"""
    monkeypatch.setenv("PUBLIC_AUDIT_ENABLED", "false")
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        await _login_admin(c)
        await _mark_public(c, "sp_default", is_public=True)
        c.cookies.clear()

        mock_precreate("fake-session")
        await c.post("/public/sp_default/sessions")

        resp = await c.get("/public/sp_default/embed-url")
        t_short = _extract_params(resp.json()["iframe_url"])["auth"][0]

        mock_ragflow_sse(session_id="fake-session", message_id="msg-1")
        await c.post(
            "/public/sp_default/sessions/fake-session/chat",
            headers={"Authorization": f"Bearer {t_short}"},
            json={"question": "你好", "stream": True, "session_id": "fake-session"},
        )

        # admin 查审计:无 public_chat 记录
        c.cookies.clear()
        await _login_admin(c)
        resp = await c.get("/admin/audit-logs?action=public_chat")
        assert resp.status_code == 200
        logs = resp.json()["audit_logs"]
        assert len(logs) == 0
