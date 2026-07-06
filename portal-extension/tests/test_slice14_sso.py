"""Slice 14 — OAuth/SSO 登录(OIDC)测试。

覆盖 ISSUES.md Issue 14 验收点:
  1. 至少实现一种 SSO(OIDC)端到端可登录
  2. SSO 用户首次登录自动创建本地用户记录(可配置是否允许自动创建)
  3. SSO 用户与自建账号用户权限模型一致(授权/会话/审计无差异)
  4. SSO 登录成功写 login_success 审计日志(meta 含 provider)
  5. 管理员能配置 SSO provider 参数(环境变量)
  6. SSO 失败(IdP 不可达 / 用户不存在且不允许自动创建)有明确错误提示

设计:
  - 测试用 mock IdP:在路由层 mock get_authorization_url / exchange_code_for_claims
    (与 mock_precreate / mock_fetch_history 一致),不依赖真实 IdP。
  - OIDC 启用的 app fixture:设置 OIDC_ENABLED=true + 完整 OIDC 环境变量。
  - SSO 回调测试需先调 /sso/login 设置 session(state+nonce),再调 /sso/callback
    (httpx.AsyncClient 自动维持同源 cookie)。
  - 数据模型扩展测试:sso_provider / sso_external_id 列存在 + 迁移 idempotent。
"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from portal.db import init_db
from portal.main import create_app
from portal.models import SeedData, SSOIdentity
from portal.oidc import OIDCConfig, clear_discovery_cache

# OIDC 测试配置(非真实 IdP,仅占位值)
_OIDC_ENV = {
    "OIDC_ENABLED": "true",
    "OIDC_ISSUER": "https://idp.test.example",
    "OIDC_CLIENT_ID": "test-client-id",
    "OIDC_CLIENT_SECRET": "test-client-secret",
    "OIDC_REDIRECT_URI": "http://portal.test.example/sso/callback",
    "SSO_AUTO_CREATE": "true",
}


class _FakeTokenGen:
    """可调用对象:交替返回固定 state / nonce(替代 secrets.token_urlsafe)。

    /sso/login 调用 secrets.token_urlsafe 两次(第一次 state,第二次 nonce),
    测试中用此对象返回可预测值,从而能在外部知道 session 中的 state 用于回调。
    """

    def __init__(self, state: str, nonce: str):
        self._values = [state, nonce]
        self._idx = 0

    def __call__(self, n):
        r = self._values[self._idx]
        self._idx += 1
        return r


@pytest.fixture
def oidc_app(monkeypatch):
    """OIDC 启用的 app 实例(设置完整 OIDC 环境变量后 create_app)。"""
    for key, val in _OIDC_ENV.items():
        monkeypatch.setenv(key, val)
    clear_discovery_cache()
    return create_app()


@pytest.fixture
async def oidc_client(oidc_app):
    """带 cookie 持久化的异步测试客户端(OIDC 启用)。"""
    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def mock_oidc_auth_url(monkeypatch):
    """工厂:mock portal.routes.get_authorization_url 返回指定 URL。"""

    def _setup(auth_url: str = "https://idp.test.example/auth?client_id=test"):
        monkeypatch.setattr(
            "portal.routes.get_authorization_url",
            AsyncMock(return_value=auth_url),
        )

    return _setup


@pytest.fixture
def mock_oidc_claims(monkeypatch):
    """工厂:mock portal.routes.exchange_code_for_claims 返回指定 claims。"""

    def _setup(claims: dict):
        monkeypatch.setattr(
            "portal.routes.exchange_code_for_claims",
            AsyncMock(return_value=claims),
        )

    return _setup


@pytest.fixture
def mock_oidc_claims_error(monkeypatch):
    """工厂:mock portal.routes.exchange_code_for_claims 抛 HTTPException(模拟 IdP 不可达)。"""
    from fastapi import HTTPException

    def _setup(status_code: int = 502, detail: str = "SSO IdP 不可达"):
        monkeypatch.setattr(
            "portal.routes.exchange_code_for_claims",
            AsyncMock(side_effect=HTTPException(status_code=status_code, detail=detail)),
        )

    return _setup


# ===========================================================================
# 验收点 5:管理员能配置 SSO provider 参数(环境变量)
# 验收点 1:OIDC 端到端可登录 — /sso/login 重定向到 IdP
# ===========================================================================


async def test_sso_login_redirects_to_idp(oidc_client, mock_oidc_auth_url):
    """OIDC 启用时,/sso/login 生成 state+nonce 存 session,302 重定向到 IdP 授权 URL。"""
    mock_oidc_auth_url("https://idp.test.example/auth?client_id=test-client-id")
    resp = await oidc_client.get("/sso/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "idp.test.example/auth" in location
    assert "client_id=test-client-id" in location


async def test_sso_login_returns_404_when_disabled(client, monkeypatch):
    """OIDC 未启用时,/sso/login 返回 404(默认 OIDC_ENABLED=false)。"""
    # conftest 的 client fixture 不设 OIDC_ENABLED,默认 false
    resp = await client.get("/sso/login", follow_redirects=False)
    assert resp.status_code == 404
    assert "未启用" in resp.json()["detail"]


async def test_sso_login_returns_500_when_config_incomplete(oidc_client, monkeypatch, mock_oidc_auth_url):
    """OIDC 启用但配置不完整时,/sso/login 返回 500 明确提示缺少哪些配置。"""
    # 覆盖 OIDC_ISSUER 为空(模拟配置不完整)
    monkeypatch.setenv("OIDC_ISSUER", "")
    # 需重新 create_app 读取新配置
    from portal.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.get("/sso/login", follow_redirects=False)
        assert resp.status_code == 500
        assert "OIDC_ISSUER" in resp.json()["detail"]


# ===========================================================================
# 验收点 1+3+4:SSO 回调成功 — 已有用户匹配,建立会话,写审计(meta 含 provider)
# ===========================================================================


async def test_sso_callback_existing_user_success(
    oidc_app, oidc_client, mock_oidc_auth_url, mock_oidc_claims, monkeypatch
):
    """SSO 回调:已有 SSO 用户匹配成功 → 建立会话 + 写 login_success 审计(meta 含 provider)。

    用固定 state/nonce(mock secrets.token_urlsafe)绕过 session 不透明问题:
    httpx.AsyncClient + ASGITransport 会自动维持 cookie(session 在 cookie 中签名存储,
    测试无法直接读取),故 mock secrets.token_urlsafe 返回可预测值用于回调校验。
    """
    fixed_state = "fixed-state-token-12345"
    fixed_nonce = "fixed-nonce-token-67890"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))

    # 预创建 SSO 用户(模拟之前已通过 SSO 登录过)
    seed = oidc_app.state.seed
    sso_user = seed.create_sso_user(
        SSOIdentity("oidc", "sub-existing-001"), username="sso_existing", email="existing@test.example"
    )
    # 给该用户授权默认分享页(验证权限模型一致)
    seed.create_grant("sp_default", "user", sso_user.id, "use")

    # 调 /sso/login 设置 session(state=fixed_state, nonce=fixed_nonce)
    mock_oidc_auth_url("https://idp.test.example/auth")
    login_resp = await oidc_client.get("/sso/login", follow_redirects=False)
    assert login_resp.status_code == 302

    # 调 /sso/callback,code + state 正确
    mock_oidc_claims(
        {"sub": "sub-existing-001", "email": "existing@test.example", "preferred_username": "sso_existing"}
    )
    resp = await oidc_client.get(
        "/sso/callback",
        params={"code": "fake-auth-code", "state": fixed_state},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"

    # 验证会话已建立:调 /me 应返回 SSO 用户信息
    me_resp = await oidc_client.get("/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "sso_existing"

    # 验证审计日志:login_success + meta 含 provider
    audit_store = oidc_app.state.audit_store
    logs = audit_store.list(action="login_success")
    sso_logs = [log for log in logs if log.actor_user_id == sso_user.id]
    assert len(sso_logs) >= 1

    meta = json.loads(sso_logs[-1].meta_json)
    assert meta["provider"] == "oidc"
    assert meta["sub"] == "sub-existing-001"


# ===========================================================================
# 验收点 2:SSO 用户首次登录自动创建本地用户(sso_auto_create=true)
# ===========================================================================


async def test_sso_callback_auto_create_new_user(
    oidc_app, oidc_client, mock_oidc_auth_url, mock_oidc_claims, monkeypatch
):
    """sso_auto_create=true 时,SSO 首次登录自动创建本地用户(is_admin=false, enabled=true)。"""
    fixed_state, fixed_nonce = "state-auto-create", "nonce-auto-create"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))

    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)

    # 新 SSO 用户(本地不存在)
    mock_oidc_claims({"sub": "sub-new-002", "email": "newuser@test.example", "preferred_username": "new_sso_user"})
    resp = await oidc_client.get(
        "/sso/callback",
        params={"code": "fake-code", "state": fixed_state},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # 验证用户已创建
    seed = oidc_app.state.seed
    user = seed.get_user_by_sso(SSOIdentity("oidc", "sub-new-002"))
    assert user is not None
    assert user.username == "new_sso_user"
    assert user.email == "newuser@test.example"
    assert user.is_admin is False
    assert user.enabled is True
    # TD9:SSO 字段捆成 SSOIdentity(user.sso.provider / .external_id)
    assert user.sso is not None
    assert user.sso.provider == "oidc"
    assert user.sso.external_id == "sub-new-002"


async def test_sso_callback_reject_new_user_when_auto_create_disabled(
    monkeypatch, mock_oidc_auth_url, mock_oidc_claims
):
    """sso_auto_create=false 时,SSO 用户不存在则拒绝(403 明确提示)。"""
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.test.example")
    monkeypatch.setenv("OIDC_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://portal.test.example/sso/callback")
    monkeypatch.setenv("SSO_AUTO_CREATE", "false")
    clear_discovery_cache()
    app = create_app()

    fixed_state, fixed_nonce = "state-reject", "nonce-reject"
    gen = _FakeTokenGen(fixed_state, fixed_nonce)
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", gen)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        mock_oidc_auth_url("https://idp.test.example/auth")
        await c.get("/sso/login", follow_redirects=False)

        mock_oidc_claims({"sub": "sub-rejected-003", "email": "rejected@test.example"})
        resp = await c.get(
            "/sso/callback",
            params={"code": "fake-code", "state": fixed_state},
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert "不允许自动创建" in resp.json()["detail"]

    # 验证用户未被创建
    assert app.state.seed.get_user_by_sso(SSOIdentity("oidc", "sub-rejected-003")) is None


# ===========================================================================
# 验收点 6:SSO 失败有明确错误提示
# ===========================================================================


async def test_sso_callback_idp_error_param(oidc_client, mock_oidc_auth_url, monkeypatch):
    """IdP 返回 error 参数(用户拒绝授权 / IdP 内部错误)→ 400 明确提示。"""
    fixed_state, fixed_nonce = "state-err", "nonce-err"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))
    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)

    resp = await oidc_client.get(
        "/sso/callback",
        params={"error": "access_denied", "error_description": "User cancelled"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "access_denied" in resp.json()["detail"]


async def test_sso_callback_missing_code(oidc_client, mock_oidc_auth_url, monkeypatch):
    """回调缺少 code 参数 → 400。"""
    fixed_state, fixed_nonce = "state-nocode", "nonce-nocode"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))
    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)

    resp = await oidc_client.get(
        "/sso/callback",
        params={"state": fixed_state},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "授权码" in resp.json()["detail"]


async def test_sso_callback_state_mismatch(oidc_client, mock_oidc_auth_url, monkeypatch):
    """state 不匹配(CSRF 攻击)→ 400。"""
    fixed_state, fixed_nonce = "state-correct", "nonce-correct"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))
    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)

    resp = await oidc_client.get(
        "/sso/callback",
        params={"code": "fake-code", "state": "wrong-state"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "state" in resp.json()["detail"].lower() or "CSRF" in resp.json()["detail"]


async def test_sso_callback_idp_unreachable(oidc_client, mock_oidc_auth_url, mock_oidc_claims_error, monkeypatch):
    """IdP 不可达(code 换 token 失败)→ 502 明确提示。"""
    fixed_state, fixed_nonce = "state-unreachable", "nonce-unreachable"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))
    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)

    mock_oidc_claims_error(status_code=502, detail="SSO IdP 不可达: connection refused")
    resp = await oidc_client.get(
        "/sso/callback",
        params={"code": "fake-code", "state": fixed_state},
        follow_redirects=False,
    )
    assert resp.status_code == 502
    assert "不可达" in resp.json()["detail"]


async def test_sso_callback_disabled_user(oidc_app, oidc_client, mock_oidc_auth_url, mock_oidc_claims, monkeypatch):
    """SSO 用户已禁用(enabled=false)→ 403(与自建账号登录一致)。"""
    fixed_state, fixed_nonce = "state-disabled", "nonce-disabled"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))

    # 预创建 SSO 用户并禁用
    seed = oidc_app.state.seed
    sso_user = seed.create_sso_user(
        SSOIdentity("oidc", "sub-disabled-004"), username="sso_disabled", email="disabled@test.example"
    )
    seed.set_user_enabled(sso_user.id, False)

    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)

    mock_oidc_claims({"sub": "sub-disabled-004"})
    resp = await oidc_client.get(
        "/sso/callback",
        params={"code": "fake-code", "state": fixed_state},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "禁用" in resp.json()["detail"]


# ===========================================================================
# 验收点 3:SSO 用户与自建账号用户权限模型一致
# ===========================================================================


async def test_sso_user_permissions_consistent_with_builtin(
    oidc_app, oidc_client, mock_oidc_auth_url, mock_oidc_claims, monkeypatch
):
    """SSO 用户权限模型与自建账号一致:能访问被授权的分享页,审计无差异。"""
    fixed_state, fixed_nonce = "state-perm", "nonce-perm"
    monkeypatch.setattr("portal.routes.secrets.token_urlsafe", _FakeTokenGen(fixed_state, fixed_nonce))

    # 创建 SSO 用户并授权
    seed = oidc_app.state.seed
    sso_user = seed.create_sso_user(SSOIdentity("oidc", "sub-perm-005"), username="sso_perm", email="perm@test.example")
    seed.create_grant("sp_default", "user", sso_user.id, "use")

    mock_oidc_auth_url("https://idp.test.example/auth")
    await oidc_client.get("/sso/login", follow_redirects=False)
    mock_oidc_claims({"sub": "sub-perm-005"})
    resp = await oidc_client.get(
        "/sso/callback",
        params={"code": "fake-code", "state": fixed_state},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # SSO 用户能访问被授权的分享页(与自建账号一致)
    pages_resp = await oidc_client.get("/share-pages")
    assert pages_resp.status_code == 200
    page_ids = [p["id"] for p in pages_resp.json()["share_pages"]]
    assert "sp_default" in page_ids

    # SSO 用户能预创建 session(权限模型一致,网关校验链不区分 SSO/自建)
    # 这里只验证 list_sessions 不报错(不实际调 RAGFlow)
    sessions_resp = await oidc_client.get("/share-pages/sp_default/sessions")
    assert sessions_resp.status_code == 200
    assert "sessions" in sessions_resp.json()


# ===========================================================================
# 数据模型扩展测试:sso_provider / sso_external_id 列 + 迁移 idempotent
# ===========================================================================


def _make_sqlite_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


def test_portal_user_has_sso_columns():
    """portal_user 表含 sso_provider / sso_external_id 列(Slice 14 扩展)。"""
    engine = _make_sqlite_engine()
    init_db(engine)
    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("portal_user")}
    assert "sso_provider" in cols
    assert "sso_external_id" in cols
    engine.dispose()


def test_sso_migration_idempotent():
    """init_db 重复执行不报错且 sso 列仍在(迁移 idempotent)。"""
    engine = _make_sqlite_engine()
    init_db(engine)
    init_db(engine)  # 重复执行
    init_db(engine)  # 第三次
    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("portal_user")}
    assert "sso_provider" in cols
    assert "sso_external_id" in cols
    engine.dispose()


def test_sso_migration_adds_columns_to_existing_table():
    """迁移能给不含 sso 列的旧 portal_user 表补列(模拟 Slice 8 → Slice 14 升级)。"""
    engine = _make_sqlite_engine()
    # 先只建旧版表(无 sso 列)— 用原始 SQL 建表模拟 Slice 8 的 DB
    with engine.begin() as conn:
        conn.execute(
            __import__("sqlalchemy").text(
                "CREATE TABLE portal_user (id VARCHAR(64) PRIMARY KEY, username VARCHAR(128) UNIQUE NOT NULL, "
                "password_hash VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL, "
                "is_admin BOOLEAN NOT NULL, enabled BOOLEAN NOT NULL, created_at FLOAT NOT NULL)"
            )
        )
    # 确认旧表无 sso 列
    inspector = inspect(engine)
    cols_before = {c["name"] for c in inspector.get_columns("portal_user")}
    assert "sso_provider" not in cols_before

    # 执行迁移(init_db 内部调 _migrate_add_sso_columns)
    init_db(engine)
    cols_after = {c["name"] for c in inspect(engine).get_columns("portal_user")}
    assert "sso_provider" in cols_after
    assert "sso_external_id" in cols_after
    engine.dispose()


def test_seed_data_get_user_by_sso():
    """SeedData.get_user_by_sso 按 SSOIdentity 查用户;自建账号不匹配。"""
    engine = _make_sqlite_engine()
    init_db(engine)
    from portal.config import load_settings
    from portal.db import create_session_maker
    from portal.models import build_seed_data

    sm = create_session_maker(engine)
    seed = build_seed_data(load_settings(), sm)  # 含 admin 自建账号用户
    # 创建 SSO 用户(TD9:用 SSOIdentity 替代两参数)
    sso_user = seed.create_sso_user(SSOIdentity("oidc", "sub-xyz"), username="sso_test", email="sso@test.example")
    # 查询匹配
    found = seed.get_user_by_sso(SSOIdentity("oidc", "sub-xyz"))
    assert found is not None
    assert found.id == sso_user.id
    # 不匹配的 sub
    assert seed.get_user_by_sso(SSOIdentity("oidc", "sub-other")) is None
    # 自建账号用户(admin)sso 为 None,不会被 SSO 查询匹配
    admin = seed.get_user_by_username("admin")
    assert admin is not None
    assert admin.sso is None
    engine.dispose()


def test_seed_data_create_sso_user_defaults():
    """create_sso_user 默认 is_admin=false, enabled=true(与自建普通账号一致)。"""
    engine = _make_sqlite_engine()
    init_db(engine)
    from portal.db import create_session_maker

    sm = create_session_maker(engine)
    seed = SeedData(sm)
    # TD9:用 SSOIdentity 替代 (provider, external_id) 两参数
    user = seed.create_sso_user(SSOIdentity("oidc", "sub-defaults"), username="sso_defaults", email="d@test.example")
    assert user.is_admin is False
    assert user.enabled is True
    # TD9:SSO 字段捆成 SSOIdentity(user.sso.provider / .external_id)
    assert user.sso is not None
    assert user.sso.provider == "oidc"
    assert user.sso.external_id == "sub-defaults"
    engine.dispose()


# ===========================================================================
# 不破坏现有自建账号登录(回归)
# ===========================================================================


async def test_builtin_login_still_works(client):
    """POST /login 自建账号登录仍正常工作(Slice 14 不破坏现有登录)。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"
    assert resp.json()["is_admin"] is True


# ===========================================================================
# OIDC 模块单元测试(用 httpx MockTransport 模拟 IdP HTTP)
# ===========================================================================


async def test_oidc_fetch_discovery_via_mock_transport(monkeypatch):
    """OIDC discovery:用 httpx.MockTransport 模拟 IdP discovery 端点。"""
    clear_discovery_cache()
    discovery_doc = {
        "issuer": "https://idp.test.example",
        "authorization_endpoint": "https://idp.test.example/auth",
        "token_endpoint": "https://idp.test.example/token",
        "jwks_uri": "https://idp.test.example/jwks",
    }

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda req: (
                    httpx.Response(200, json=discovery_doc)
                    if "openid-configuration" in str(req.url)
                    else httpx.Response(404)
                )
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.oidc.httpx.AsyncClient", _MockAsyncClient)
    from portal.oidc import _fetch_discovery

    config = OIDCConfig(issuer="https://idp.test.example", client_id="c", client_secret="s", redirect_uri="r")
    doc = await _fetch_discovery(config)
    assert doc["authorization_endpoint"] == "https://idp.test.example/auth"
    assert doc["token_endpoint"] == "https://idp.test.example/token"
    clear_discovery_cache()


async def test_oidc_discovery_unreachable_returns_502(monkeypatch):
    """IdP discovery 不可达 → HTTPException 502。"""
    clear_discovery_cache()

    def _raise_connect_error(request):
        raise httpx.ConnectError("connection refused")

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_raise_connect_error)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.oidc.httpx.AsyncClient", _MockAsyncClient)
    from fastapi import HTTPException

    from portal.oidc import _fetch_discovery

    config = OIDCConfig(issuer="https://idp.test.example", client_id="c", client_secret="s", redirect_uri="r")
    with pytest.raises(HTTPException) as exc_info:
        await _fetch_discovery(config)
    assert exc_info.value.status_code == 502
    assert "不可达" in exc_info.value.detail
    clear_discovery_cache()


async def test_oidc_get_authorization_url_builds_correct_url(monkeypatch):
    """get_authorization_url 构造正确的授权 URL(含 state/nonce/client_id/redirect_uri)。"""
    clear_discovery_cache()
    discovery_doc = {
        "issuer": "https://idp.test.example",
        "authorization_endpoint": "https://idp.test.example/auth",
        "token_endpoint": "https://idp.test.example/token",
        "jwks_uri": "https://idp.test.example/jwks",
    }

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, json=discovery_doc))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.oidc.httpx.AsyncClient", _MockAsyncClient)
    from portal.oidc import get_authorization_url

    config = OIDCConfig(
        issuer="https://idp.test.example",
        client_id="my-client-id",
        client_secret="secret",
        redirect_uri="https://portal.test/sso/callback",
    )
    url = await get_authorization_url(config, state="test-state", nonce="test-nonce")
    assert "idp.test.example/auth" in url
    assert "client_id=my-client-id" in url
    assert "state=test-state" in url
    assert "nonce=test-nonce" in url
    assert "redirect_uri=https" in url
    clear_discovery_cache()
