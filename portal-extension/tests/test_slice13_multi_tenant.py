"""Slice 13 — 多租户扩展(org_id 隔离)测试。

覆盖 ISSUES.md Issue 13 验收点:
  1. 所有 7 张表加 org_id 字段,迁移脚本把现有数据归入默认 org(org_id='default')
  2. 用户只能看到同 org 的分享页与会话(跨 org 访问 → 403)
  3. org_admin 能管理本 org 用户/组/分享页/授权,不能跨 org
  4. 平台管理员(is_admin=true)能跨 org 管理,能看到 org 维度列表
  5. 审计日志含 org_id 维度,可按 org 筛选
  6. 现有测试适配 org_id 后全部通过(由其他 slice 测试文件回归保证)

设计:
  - 单元测试用 SQLite in-memory,每测试独立 engine,完全隔离。
  - 端到端隔离测试用 client fixture(经 routes/gateway 全链路)。
  - 跨 org 场景构造两个 org('default' 与 'acme'),验证隔离。
"""

import time

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from portal.config import load_settings
from portal.db import (
    PORTAL_TABLES,
    PortalUserModel,
    SharePageModel,
    create_session_maker,
    init_db,
)
from portal.models import (
    AuditLog,
    AuditStore,
    ChatSessionOwner,
    PortalGroup,
    PortalUser,
    SeedData,
    SessionStore,
    SharePage,
    build_seed_data,
)

# ---------------------------------------------------------------------------
# 测试用 SQLite in-memory engine 工厂(每测试独立 DB,完全隔离)
# ---------------------------------------------------------------------------


def _make_sqlite_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest.fixture
def db_engine():
    engine = _make_sqlite_engine()
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_maker(db_engine):
    return create_session_maker(db_engine)


# 所有 7 张表名(含 org_id 后不变,仍为 PORTAL_TABLES 集合)
ALL_TABLES = PORTAL_TABLES


# ===========================================================================
# 验收点 1:7 张表加 org_id 字段 + portal_user 加 org_admin + 迁移 idempotent
# ===========================================================================


@pytest.mark.parametrize(
    "table",
    sorted(ALL_TABLES),
)
def test_all_tables_have_org_id_column(db_engine, table):
    """所有 7 张表都有 org_id 字段(对应验收点 1)。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns(table)}
    assert "org_id" in cols, f"{table} 缺 org_id 字段,实际: {cols}"


def test_portal_user_has_org_admin_column(db_engine):
    """portal_user 表有 org_admin 字段(角色:介于普通用户与平台 is_admin 之间)。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("portal_user")}
    assert "org_admin" in cols, f"portal_user 缺 org_admin 字段,实际: {cols}"


def test_org_id_column_default_is_default(db_session_maker):
    """新建行不指定 org_id 时,默认值为 'default'(现有数据归入默认 org)。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="u", email="e@e.com", password_hash="h")
    assert user.org_id == "default", "新建用户默认 org_id 应为 'default'"
    # 验证 DB 层默认值
    with db_session_maker() as session:
        row = session.get(PortalUserModel, user.id)
        assert row.org_id == "default"


def test_org_admin_default_false(db_session_maker):
    """新建用户 org_admin 默认 False。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="u", email="e@e.com", password_hash="h")
    assert user.org_admin is False


def test_migration_add_org_id_idempotent(db_engine):
    """init_db 重复执行不报错,org_id 列不重复添加(idempotent)。"""
    # db_engine fixture 已 init_db 一次,再调一次验证 idempotent
    init_db(db_engine)
    inspector = inspect(db_engine)
    # 所有表仍有 org_id(没丢也没重复)
    for table in ALL_TABLES:
        cols = {c["name"] for c in inspector.get_columns(table)}
        assert "org_id" in cols


def test_existing_data_migrated_to_default_org(db_session_maker):
    """迁移:现有数据(无 org_id)归入默认 org。

    模拟:直接 ORM 插入不含 org_id 的行(用 default 补),验证 org_id='default'。
    实际迁移脚本用 ALTER TABLE ADD COLUMN ... DEFAULT 'default',现有行自动获得默认值。
    """
    with db_session_maker() as session:
        session.add(
            PortalUserModel(
                id="u_legacy",
                username="legacy",
                password_hash="h",
                email="",
                is_admin=False,
                enabled=True,
                created_at=time.time(),
            )
        )
        session.commit()
    with db_session_maker() as session:
        row = session.get(PortalUserModel, "u_legacy")
        assert row.org_id == "default", "迁移后现有用户应归入 default org"


# ===========================================================================
# 配置:PORTAL_DEFAULT_ORG_ID 环境变量
# ===========================================================================


def test_config_default_org_id(monkeypatch):
    """Settings 含 portal_default_org_id 字段,默认 'default',可经 PORTAL_DEFAULT_ORG_ID 配置。"""
    monkeypatch.setenv("PORTAL_DEFAULT_ORG_ID", "acme")
    settings = load_settings()
    assert settings.portal_default_org_id == "acme"
    # 默认值
    monkeypatch.delenv("PORTAL_DEFAULT_ORG_ID", raising=False)
    settings = load_settings()
    assert settings.portal_default_org_id == "default"


# ===========================================================================
# dataclass 字段:PortalUser/PortalGroup/SharePage/ChatSessionOwner/AuditLog 含 org_id
# ===========================================================================


def test_portal_user_dataclass_has_org_id_and_org_admin():
    """PortalUser dataclass 含 org_id(默认 'default')与 org_admin(默认 False)。"""
    user = PortalUser(
        id="u1",
        username="u",
        password_hash="h",
    )
    assert user.org_id == "default"
    assert user.org_admin is False


def test_portal_group_dataclass_has_org_id():
    """PortalGroup dataclass 含 org_id(默认 'default')。"""
    group = PortalGroup(id="g1", name="g")
    assert group.org_id == "default"


def test_share_page_dataclass_has_org_id():
    """SharePage dataclass 含 org_id(默认 'default')。"""
    page = SharePage(id="sp1", name="p")
    assert page.org_id == "default"


def test_chat_session_owner_dataclass_has_org_id():
    """ChatSessionOwner dataclass 含 org_id(默认 'default')。"""
    owner = ChatSessionOwner(
        session_id="s1",
        share_page_id="sp1",
        portal_user_id="u1",
        ragflow_resource_id="d1",
    )
    assert owner.org_id == "default"


def test_audit_log_dataclass_has_org_id():
    """AuditLog dataclass 含 org_id(默认 'default')。"""
    log = AuditLog(
        id="al1",
        actor_user_id="u1",
        action="login_success",
        target_type="user",
        target_id="u1",
        at=time.time(),
        meta_json="",
    )
    assert log.org_id == "default"


# ===========================================================================
# 验收点 2:SeedData 隔离 — 用户只看同 org 的分享页;跨 org has_use_grant=False
# ===========================================================================


def test_seed_create_user_with_org_id(db_session_maker):
    """SeedData.create_user 接受 org_id 参数(默认 'default')。"""
    seed = SeedData(db_session_maker)
    user_acme = seed.create_user(username="u_acme", email="a@e.com", password_hash="h", org_id="acme")
    assert user_acme.org_id == "acme"
    user_default = seed.create_user(username="u_default", email="d@e.com", password_hash="h")
    assert user_default.org_id == "default"


def test_seed_create_user_with_org_admin(db_session_maker):
    """SeedData.create_user 接受 org_admin 参数(默认 False)。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="u_orgadmin", email="o@e.com", password_hash="h", org_admin=True)
    assert user.org_admin is True


def test_seed_list_users_filter_by_org(db_session_maker):
    """SeedData.list_users 支持 org_id 过滤(只返回该 org 用户)。"""
    seed = SeedData(db_session_maker)
    seed.create_user(username="u_d1", email="", password_hash="h", org_id="default")
    seed.create_user(username="u_d2", email="", password_hash="h", org_id="default")
    seed.create_user(username="u_a1", email="", password_hash="h", org_id="acme")
    # 全部(is_admin 视角)
    assert len(seed.list_users()) == 3
    # 按 org 过滤
    default_users = seed.list_users(org_id="default")
    assert len(default_users) == 2
    assert all(u.org_id == "default" for u in default_users)
    acme_users = seed.list_users(org_id="acme")
    assert len(acme_users) == 1
    assert acme_users[0].username == "u_a1"


def test_seed_list_groups_filter_by_org(db_session_maker):
    """SeedData.list_groups 支持 org_id 过滤。"""
    seed = SeedData(db_session_maker)
    seed.create_group(name="g_d", org_id="default")
    seed.create_group(name="g_a", org_id="acme")
    assert len(seed.list_groups()) == 2
    assert len(seed.list_groups(org_id="default")) == 1
    assert seed.list_groups(org_id="default")[0].name == "g_d"
    assert len(seed.list_groups(org_id="acme")) == 1


def test_seed_list_share_pages_filter_by_org(db_session_maker):
    """SeedData.list_share_pages 支持 org_id 过滤。"""
    seed = SeedData(db_session_maker)
    seed.create_share_page(name="p_d", ragflow_resource_id="d1", org_id="default")
    seed.create_share_page(name="p_a", ragflow_resource_id="d2", org_id="acme")
    assert len(seed.list_share_pages()) == 2
    assert len(seed.list_share_pages(org_id="default")) == 1
    assert seed.list_share_pages(org_id="default")[0].name == "p_d"


def test_seed_list_share_pages_for_user_only_same_org(db_session_maker):
    """list_share_pages_for_user 只返回同 org 的分享页(跨 org 不返回)。

    对应验收点 2:用户只能看到同 org 的分享页。
    """
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="u", email="", password_hash="h", org_id="default")
    page_same_org = seed.create_share_page(name="same", ragflow_resource_id="d1", org_id="default")
    page_other_org = seed.create_share_page(name="other", ragflow_resource_id="d2", org_id="acme")
    # 给用户授权两个分享页(同 org 与跨 org)
    seed.create_grant(page_same_org.id, "user", user.id, "use")
    seed.create_grant(page_other_org.id, "user", user.id, "use")
    pages = seed.list_share_pages_for_user(user.id)
    page_ids = [p.id for p in pages]
    assert page_same_org.id in page_ids, "同 org 分享页应可见"
    assert page_other_org.id not in page_ids, "跨 org 分享页应不可见"


def test_seed_has_use_grant_cross_org_returns_false(db_session_maker):
    """has_use_grant 跨 org 时返回 False(跨 org 访问拒绝)。

    对应验收点 2:跨 org 访问 → 403(网关 _assert_grant_exists 校验)。
    """
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="u", email="", password_hash="h", org_id="default")
    page_acme = seed.create_share_page(name="p", ragflow_resource_id="d1", org_id="acme")
    seed.create_grant(page_acme.id, "user", user.id, "use")
    # grant 存在但跨 org → False
    assert not seed.has_use_grant(page_acme.id, user.id), "跨 org 的 grant 应被隔离(返回 False)"
    # 同 org → True
    page_default = seed.create_share_page(name="p2", ragflow_resource_id="d2", org_id="default")
    seed.create_grant(page_default.id, "user", user.id, "use")
    assert seed.has_use_grant(page_default.id, user.id)


def test_seed_create_share_page_with_org_id(db_session_maker):
    """SeedData.create_share_page 接受 org_id 参数。"""
    seed = SeedData(db_session_maker)
    page = seed.create_share_page(name="p", ragflow_resource_id="d1", org_id="acme")
    assert page.org_id == "acme"
    # 验证 DB
    with db_session_maker() as session:
        row = session.get(SharePageModel, page.id)
        assert row.org_id == "acme"


def test_seed_create_group_with_org_id(db_session_maker):
    """SeedData.create_group 接受 org_id 参数。"""
    seed = SeedData(db_session_maker)
    group = seed.create_group(name="g", org_id="acme")
    assert group.org_id == "acme"


def test_build_seed_data_assigns_default_org(db_session_maker, monkeypatch):
    """build_seed_data 创建的 seed 数据 org_id='default'(现有数据归入默认 org)。"""
    monkeypatch.setenv("PORTAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("PORTAL_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("PORTAL_USER2_USERNAME", "user2")
    monkeypatch.setenv("PORTAL_USER2_PASSWORD", "testpass123")
    settings = load_settings()
    seed = build_seed_data(settings, db_session_maker)
    admin = seed.get_user_by_username("admin")
    assert admin.org_id == "default"
    user2 = seed.get_user_by_username("user2")
    assert user2.org_id == "default"
    page = seed.get_share_page("sp_default")
    assert page.org_id == "default"


# ===========================================================================
# 验收点 2/5:SessionStore / AuditStore org_id 支持
# ===========================================================================


def test_session_store_bind_with_org_id(db_session_maker):
    """SessionStore.bind 接受 org_id(默认 'default')。"""
    store = SessionStore(db_session_maker)
    owner = store.bind(
        session_id="s1",
        share_page_id="sp1",
        portal_user_id="u1",
        ragflow_resource_id="d1",
        org_id="acme",
    )
    assert owner.org_id == "acme"
    loaded = store.get("s1")
    assert loaded.org_id == "acme"


def test_session_store_bind_default_org(db_session_maker):
    """SessionStore.bind 不传 org_id 时默认 'default'。"""
    store = SessionStore(db_session_maker)
    owner = store.bind(
        session_id="s1",
        share_page_id="sp1",
        portal_user_id="u1",
        ragflow_resource_id="d1",
    )
    assert owner.org_id == "default"


def test_session_store_list_all_filter_by_org(db_session_maker):
    """SessionStore.list_all 支持 org_id 过滤(管理员视角按 org 筛选会话)。"""
    store = SessionStore(db_session_maker)
    store.bind(
        session_id="s_d",
        share_page_id="sp1",
        portal_user_id="u1",
        ragflow_resource_id="d1",
        org_id="default",
    )
    store.bind(
        session_id="s_a",
        share_page_id="sp2",
        portal_user_id="u2",
        ragflow_resource_id="d2",
        org_id="acme",
    )
    # 全部
    assert len(store.list_all()) == 2
    # 按 org 过滤
    assert len(store.list_all(org_id="default")) == 1
    assert store.list_all(org_id="default")[0].session_id == "s_d"
    assert len(store.list_all(org_id="acme")) == 1


def test_audit_store_record_with_org_id(db_session_maker):
    """AuditStore.record 接受 org_id(默认 'default')。"""
    store = AuditStore(db_session_maker)
    log = store.record(
        actor_user_id="u1",
        action="login_success",
        target_type="user",
        target_id="u1",
        org_id="acme",
    )
    assert log.org_id == "acme"
    logs = store.list(limit=10)
    assert logs[0].org_id == "acme"


def test_audit_store_record_default_org(db_session_maker):
    """AuditStore.record 不传 org_id 时默认 'default'。"""
    store = AuditStore(db_session_maker)
    log = store.record(
        actor_user_id="u1",
        action="login_success",
        target_type="user",
        target_id="u1",
    )
    assert log.org_id == "default"


def test_audit_store_list_filter_by_org(db_session_maker):
    """AuditStore.list 支持 org_id 过滤(对应验收点 5:按 org 筛选审计)。"""
    store = AuditStore(db_session_maker)
    store.record(
        actor_user_id="u1",
        action="login_success",
        target_type="user",
        target_id="u1",
        org_id="default",
    )
    store.record(
        actor_user_id="u2",
        action="login_success",
        target_type="user",
        target_id="u2",
        org_id="acme",
    )
    # 全部
    assert len(store.list(limit=100)) == 2
    # 按 org 过滤
    assert len(store.list(org_id="default")) == 1
    assert len(store.list(org_id="acme")) == 1


# ===========================================================================
# 端到端隔离测试:跨 org 访问 → 403(经 routes + gateway)
# ===========================================================================


@pytest.fixture
def app_multi_org(monkeypatch):
    """构造含两个 org(default + acme)的 app 实例。

    - admin(平台 is_admin,org=default)
    - user_d(普通用户,org=default)
    - orgadmin_d(org_admin,org=default)
    - user_a(普通用户,org=acme)
    - sp_default(分享页,org=default)
    - sp_acme(分享页,org=acme)
    - 给 user_d 与 user_a 各自授权同 org 分享页
    """
    # 用独立 engine 避免与其他测试共享 DB
    engine = _make_sqlite_engine()
    init_db(engine)
    sm = create_session_maker(engine)
    monkeypatch.setenv("PORTAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("PORTAL_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("PORTAL_USER2_USERNAME", "user2")
    monkeypatch.setenv("PORTAL_USER2_PASSWORD", "testpass123")
    settings = load_settings()
    seed = build_seed_data(settings, sm)
    # admin 已存在(build_seed_data),org=default,is_admin=True
    # user2 已存在,org=default,普通用户
    # 新增 org_admin 用户(org=default)
    from portal.password import hash_password

    seed.create_user(
        username="orgadmin",
        email="oa@e.com",
        password_hash=hash_password("testpass123"),
        org_id="default",
        org_admin=True,
    )
    # 新增 acme 普通用户
    user_a = seed.create_user(
        username="user_a",
        email="ua@e.com",
        password_hash=hash_password("testpass123"),
        org_id="acme",
    )
    # 新增 acme 分享页 + 授权给 user_a
    sp_acme = seed.create_share_page(name="acme 页", ragflow_resource_id="dialog-acme", org_id="acme")
    seed.create_grant(sp_acme.id, "user", user_a.id, "use")
    # 给 user2(default)对默认分享页已有 grant(build_seed_data 加的)
    # 给 user_a 对 sp_default 不授权(跨 org 不应能访问)

    from portal.main import create_app

    app = create_app()
    # 复用同一 engine,避免重新 init_db 丢数据
    app.state.db_engine = engine
    app.state.session_maker = sm
    app.state.seed = seed
    app.state.session_store = SessionStore(sm)
    app.state.audit_store = AuditStore(sm)
    yield app
    engine.dispose()


@pytest.fixture
async def client_multi_org(app_multi_org):
    import httpx

    transport = httpx.ASGITransport(app=app_multi_org)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _login(client, username: str, password: str = "testpass123"):
    """登录辅助:返回响应(含会话 cookie)。"""
    return await client.post("/login", json={"username": username, "password": password})


# ===========================================================================
# 验收点 2:用户只能看到同 org 的分享页与会话(跨 org 访问 → 403)
# ===========================================================================


async def test_user_only_sees_same_org_share_pages(client_multi_org):
    """普通用户 GET /share-pages 只返回同 org 的分享页。

    user_a(org=acme)登录后应只看到 sp_acme,看不到 sp_default。
    """
    await _login(client_multi_org, "user_a")
    resp = await client_multi_org.get("/share-pages")
    assert resp.status_code == 200
    pages = resp.json()["share_pages"]
    page_ids = [p["id"] for p in pages]
    # user_a 应能看到 sp_acme(org=acme)
    assert any(p["name"] == "acme 页" for p in pages), "user_a 应看到同 org 的 acme 页"
    # user_a 不应看到 sp_default(org=default)
    assert "sp_default" not in page_ids, "user_a 不应看到跨 org 的 sp_default"


async def test_user_cross_org_share_page_access_returns_403(client_multi_org):
    """跨 org 访问分享页 → 403(对应验收点 2)。

    user_a(org=acme)尝试访问 sp_default(org=default)的 embed-url → 403。
    """
    await _login(client_multi_org, "user_a")
    resp = await client_multi_org.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403, f"跨 org 访问应 403,实际: {resp.status_code} {resp.text}"


async def test_user_cross_org_session_access_returns_403(client_multi_org):
    """跨 org 访问他人会话 → 403。

    user2(default)预创建一个 session;user_a(acme)尝试访问 → 403。
    路径:GET /share-pages/sp_default/sessions/<sid>(跨 org 的分享页本身已 403)。
    """
    # user2 登录预创建 session
    await _login(client_multi_org, "user2")
    from unittest.mock import AsyncMock

    client_multi_org._app = client_multi_org._app if hasattr(client_multi_org, "_app") else None
    # mock 预创建(避免真实 RAGFlow 调用)
    import portal.routes

    portal.routes.precreate_session_via_ragflow = AsyncMock(return_value="sid_user2")
    resp = await client_multi_org.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    # user_a 登录后尝试访问该 session(跨 org)
    await _login(client_multi_org, "user_a")
    # sp_default 对 user_a 跨 org → 403(在 _check_share_page_access 拦截)
    resp = await client_multi_org.get(f"/share-pages/sp_default/sessions/{sid}")
    assert resp.status_code == 403


# ===========================================================================
# 验收点 3:org_admin 能管理本 org 资源,不能跨 org;普通用户 → 403
# ===========================================================================


async def test_org_admin_can_list_same_org_users(client_multi_org):
    """org_admin 能列出本 org 用户(GET /admin/users 只返回本 org)。

    orgadmin_d(org=default)登录后 GET /admin/users → 只看到 default org 用户。
    """
    await _login(client_multi_org, "orgadmin")
    resp = await client_multi_org.get("/admin/users")
    assert resp.status_code == 200, resp.text
    users = resp.json()["users"]
    # org_admin 只看本 org 用户(default):admin/user2/orgadmin,不含 user_a(acme)
    usernames = [u["username"] for u in users]
    assert "user_a" not in usernames, "org_admin 不应看到跨 org 用户"
    assert "admin" in usernames
    assert "user2" in usernames
    assert "orgadmin" in usernames


async def test_org_admin_can_disable_same_org_user(client_multi_org):
    """org_admin 能禁用本 org 用户。"""
    await _login(client_multi_org, "orgadmin")
    # user2 在 default org,orgadmin_d 可禁用
    resp = await client_multi_org.patch("/admin/users/u_user2", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False


async def test_org_admin_cannot_modify_cross_org_user(client_multi_org):
    """org_admin 不能跨 org 修改用户(403)。"""
    await _login(client_multi_org, "orgadmin")
    # 先创建 acme 用户 user_a,id 未知 → 用 list 找
    # 但 orgadmin 看不到 acme 用户,直接构造请求:用 user_a 的 id
    # user_a 的 id 在 app_multi_org fixture 里创建,需拿到
    seed = client_multi_org._transport.app.state.seed
    user_a = seed.get_user_by_username("user_a")
    resp = await client_multi_org.patch(f"/admin/users/{user_a.id}", json={"enabled": False})
    assert resp.status_code == 403, f"跨 org 修改用户应 403,实际: {resp.status_code} {resp.text}"


async def test_normal_user_admin_endpoints_403(client_multi_org):
    """普通用户(非 org_admin 非 is_admin)调管理端点 → 403。"""
    await _login(client_multi_org, "user2")
    resp = await client_multi_org.get("/admin/users")
    assert resp.status_code == 403
    resp = await client_multi_org.get("/admin/share-pages")
    assert resp.status_code == 403


async def test_org_admin_can_create_user_in_same_org(client_multi_org):
    """org_admin 创建用户时自动归入本 org(不能指定跨 org)。"""
    await _login(client_multi_org, "orgadmin")
    resp = await client_multi_org.post(
        "/admin/users",
        json={"username": "newuser", "email": "n@e.com", "password": "pass123"},
    )
    assert resp.status_code == 201, resp.text
    new_user = resp.json()
    assert new_user["org_id"] == "default", "org_admin 创建的用户应归入本 org"


async def test_org_admin_can_manage_same_org_share_pages(client_multi_org):
    """org_admin 能管理本 org 分享页(创建/列表/禁用)。"""
    await _login(client_multi_org, "orgadmin")
    # 列表只返回本 org
    resp = await client_multi_org.get("/admin/share-pages")
    assert resp.status_code == 200
    pages = resp.json()["share_pages"]
    for p in pages:
        assert p["org_id"] == "default", "org_admin 只应看到本 org 分享页"
    # 创建(归入本 org)
    resp = await client_multi_org.post(
        "/admin/share-pages",
        json={"name": "new page", "ragflow_resource_id": "dialog-new"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["org_id"] == "default"


async def test_org_admin_cannot_modify_cross_org_share_page(client_multi_org):
    """org_admin 不能跨 org 修改分享页(403)。"""
    await _login(client_multi_org, "orgadmin")
    seed = client_multi_org._transport.app.state.seed
    sp_acme = [p for p in seed.list_share_pages() if p.org_id == "acme"][0]
    resp = await client_multi_org.patch(f"/admin/share-pages/{sp_acme.id}", json={"enabled": False})
    assert resp.status_code == 403, f"跨 org 修改分享页应 403,实际: {resp.status_code} {resp.text}"


# ===========================================================================
# 验收点 4:平台管理员(is_admin)能跨 org 管理,能看到 org 维度列表
# ===========================================================================


async def test_platform_admin_sees_all_orgs_users(client_multi_org):
    """is_admin 能看到所有 org 用户(跨 org)。"""
    await _login(client_multi_org, "admin")
    resp = await client_multi_org.get("/admin/users")
    assert resp.status_code == 200
    usernames = [u["username"] for u in resp.json()["users"]]
    assert "admin" in usernames  # default
    assert "user2" in usernames  # default
    assert "orgadmin" in usernames  # default
    assert "user_a" in usernames  # acme


async def test_platform_admin_can_filter_users_by_org(client_multi_org):
    """is_admin 能按 org 筛选用户(GET /admin/users?org_id=acme)。"""
    await _login(client_multi_org, "admin")
    resp = await client_multi_org.get("/admin/users?org_id=acme")
    assert resp.status_code == 200
    users = resp.json()["users"]
    assert all(u["org_id"] == "acme" for u in users), "按 org 筛选应只返回 acme 用户"
    assert any(u["username"] == "user_a" for u in users)


async def test_platform_admin_can_modify_cross_org_user(client_multi_org):
    """is_admin 能跨 org 修改用户。"""
    await _login(client_multi_org, "admin")
    seed = client_multi_org._transport.app.state.seed
    user_a = seed.get_user_by_username("user_a")
    resp = await client_multi_org.patch(f"/admin/users/{user_a.id}", json={"enabled": False})
    assert resp.status_code == 200, f"is_admin 跨 org 修改用户应成功,实际: {resp.status_code} {resp.text}"


async def test_platform_admin_sees_all_orgs_share_pages(client_multi_org):
    """is_admin 能看到所有 org 分享页(跨 org)。"""
    await _login(client_multi_org, "admin")
    resp = await client_multi_org.get("/admin/share-pages")
    assert resp.status_code == 200
    pages = resp.json()["share_pages"]
    org_ids = {p["org_id"] for p in pages}
    assert "default" in org_ids
    assert "acme" in org_ids


async def test_platform_admin_can_filter_share_pages_by_org(client_multi_org):
    """is_admin 能按 org 筛选分享页。"""
    await _login(client_multi_org, "admin")
    resp = await client_multi_org.get("/admin/share-pages?org_id=acme")
    assert resp.status_code == 200
    pages = resp.json()["share_pages"]
    assert all(p["org_id"] == "acme" for p in pages)


# ===========================================================================
# 验收点 5:审计日志含 org_id 维度,可按 org 筛选;_audit 自动带 actor org_id
# ===========================================================================


async def test_audit_log_records_actor_org_id(client_multi_org):
    """_audit 自动带 actor 的 org_id(登录审计日志含 org_id 维度)。"""
    await _login(client_multi_org, "user_a")  # user_a org=acme
    # 用 admin 登录查看审计日志
    await _login(client_multi_org, "admin")
    resp = await client_multi_org.get("/admin/audit-logs?action=login_success")
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    # 找到 user_a 的登录日志
    seed = client_multi_org._transport.app.state.seed
    user_a = seed.get_user_by_username("user_a")
    user_a_login_logs = [log for log in logs if log["actor_user_id"] == user_a.id]
    assert len(user_a_login_logs) >= 1
    assert user_a_login_logs[0]["org_id"] == "acme", "审计日志应含 actor 的 org_id"


async def test_platform_admin_filter_audit_logs_by_org(client_multi_org):
    """is_admin 能按 org 筛选审计日志(GET /admin/audit-logs?org_id=acme)。"""
    # 触发一些审计事件:user_a(acme)登录失败/成功
    await _login(client_multi_org, "user_a")
    await _login(client_multi_org, "admin")
    resp = await client_multi_org.get("/admin/audit-logs?org_id=acme")
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    assert all(log["org_id"] == "acme" for log in logs), "按 org 筛选审计日志应只返回 acme"


async def test_org_admin_only_sees_same_org_audit_logs(client_multi_org):
    """org_admin 查审计日志只看本 org(跨 org 不可见)。"""
    # user_a(acme)登录触发审计
    await _login(client_multi_org, "user_a")
    # orgadmin(default)登录查审计
    await _login(client_multi_org, "orgadmin")
    resp = await client_multi_org.get("/admin/audit-logs")
    assert resp.status_code == 200
    logs = resp.json()["audit_logs"]
    # orgadmin 只看 default org 的审计日志
    for log in logs:
        assert log["org_id"] == "default", "org_admin 只应看到本 org 审计日志"


# ===========================================================================
# 验收点 2:网关 T_short 签发校验 user.org_id == share_page.org_id
# ===========================================================================


async def test_gateway_cross_org_t_short_signing_403(client_multi_org):
    """网关 embed-url 签发校验:user.org_id 必须匹配 share_page.org_id。

    user_a(acme)尝试请求 sp_default(default)的 embed-url → 403。
    (与 test_user_cross_org_share_page_access_returns_403 一致,但此测试强调网关侧)
    """
    await _login(client_multi_org, "user_a")
    resp = await client_multi_org.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403


async def test_gateway_same_org_t_short_signing_ok(client_multi_org, monkeypatch):
    """同 org 用户请求 embed-url → 200,iframe URL 含 T_short。"""
    await _login(client_multi_org, "user_a")
    seed = client_multi_org._transport.app.state.seed
    sp_acme = [p for p in seed.list_share_pages() if p.org_id == "acme"][0]
    resp = await client_multi_org.get(f"/share-pages/{sp_acme.id}/embed-url")
    assert resp.status_code == 200, resp.text
    assert "auth=" in resp.json()["iframe_url"]
