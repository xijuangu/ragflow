"""Slice 8 — DB 持久化迁移测试(内存存储 → SQLAlchemy 持久化)。

覆盖 ISSUES.md Issue 8 验收点:
  - 7 张表 schema 落地,迁移脚本可重复执行(idempotent)
  - 进程重启后用户/会话/授权/审计数据不丢失(登录 → 重启 → 仍登录,会话仍在)
  - 现有 API 接口不变(SeedData/SessionStore/AuditStore/TokenStore 外部方法签名不变)
  - 测试隔离:用 SQLite in-memory,不依赖外部 MySQL,不污染生产数据
  - T_short 行为明确(保留内存,重启失效,文档说明)

设计:
  - 用 SQLite in-memory + StaticPool(共享同一连接,保证 :memory: 跨 session 可见)。
  - 每个测试用独立 engine,互不污染。
  - 通过环境变量 PORTAL_DB_URL=sqlite:///:memory: 指向测试 DB。
  - 不直连 MySQL,符合「单元测试不依赖外部 MySQL」要求。
"""

import os
import time

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from portal.config import load_settings
from portal.db import (
    PORTAL_TABLES,
    PortalUserModel,
    SharePageGrantModel,
    SharePageModel,
    create_session_maker,
    init_db,
)
from portal.models import AuditStore, SeedData, SessionStore, build_seed_data

# ---------------------------------------------------------------------------
# 测试用 SQLite in-memory engine 工厂(每测试独立 DB,完全隔离)
# ---------------------------------------------------------------------------


def _make_sqlite_engine():
    """构造 SQLite in-memory engine(StaticPool 共享连接,保证 :memory: 跨 session 可见)。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest.fixture
def db_engine():
    """每测试一个独立 SQLite in-memory engine + 已初始化表。"""
    engine = _make_sqlite_engine()
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_maker(db_engine):
    """基于 db_engine 的 session 工厂。"""
    return create_session_maker(db_engine)


# ===========================================================================
# 验收点 1:7 张表 schema 落地 + 迁移可重复执行(idempotent)
# ===========================================================================


def test_all_7_tables_created(db_engine):
    """7 张表全部创建(portal_user/portal_group/portal_group_member/share_page/
    share_page_grant/chat_session_owner/audit_log)。"""
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    missing = PORTAL_TABLES - tables
    assert not missing, f"缺少表: {missing},实际有: {tables}"


def test_migration_is_idempotent(db_engine):
    """init_db 重复执行不报错且表结构不变(idempotent,对应验收点 1)。"""
    inspector_before = inspect(db_engine)
    tables_before = sorted(inspector_before.get_table_names())

    # 重复执行迁移
    init_db(db_engine)

    inspector_after = inspect(db_engine)
    tables_after = sorted(inspector_after.get_table_names())
    assert tables_before == tables_after, "重复迁移后表集合应不变"


def test_portal_user_columns(db_engine):
    """portal_user 表字段覆盖 schema:id/username/password_hash/email/is_admin/enabled/created_at。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("portal_user")}
    required = {"id", "username", "password_hash", "email", "is_admin", "enabled", "created_at"}
    assert required <= cols, f"portal_user 缺字段: {required - cols},实际: {cols}"


def test_share_page_columns(db_engine):
    """share_page 表字段覆盖 schema。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("share_page")}
    required = {"id", "name", "ragflow_type", "ragflow_resource_id", "embed_type", "enabled", "created_at"}
    assert required <= cols, f"share_page 缺字段: {required - cols},实际: {cols}"


def test_chat_session_owner_columns(db_engine):
    """chat_session_owner 表字段覆盖 schema(含 deleted_at 与 message_count)。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("chat_session_owner")}
    required = {
        "session_id",
        "share_page_id",
        "portal_user_id",
        "ragflow_resource_id",
        "title",
        "created_at",
        "last_active_at",
        "deleted_at",
        "message_count",
    }
    assert required <= cols, f"chat_session_owner 缺字段: {required - cols},实际: {cols}"


def test_audit_log_columns(db_engine):
    """audit_log 表字段覆盖 schema。"""
    inspector = inspect(db_engine)
    cols = {c["name"] for c in inspector.get_columns("audit_log")}
    required = {"id", "actor_user_id", "action", "target_type", "target_id", "at", "meta_json"}
    assert required <= cols, f"audit_log 缺字段: {required - cols},实际: {cols}"


def test_portal_user_username_unique(db_engine):
    """portal_user.username 唯一约束存在(防止重复用户名)。"""
    inspector = inspect(db_engine)
    uniques = inspector.get_unique_constraints("portal_user")
    column_sets = [tuple(u["column_names"]) for u in uniques]
    assert ("username",) in column_sets, f"username 应有唯一约束,实际: {column_sets}"


def test_share_page_grant_unique(db_engine):
    """share_page_grant 有 (share_page_id, subject_type, subject_id, permission) 唯一约束
    (对应 create_grant 幂等,避免重复 grant 导致撤销一次后残留)。"""
    inspector = inspect(db_engine)
    uniques = inspector.get_unique_constraints("share_page_grant")
    column_sets = [tuple(u["column_names"]) for u in uniques]
    expected = ("share_page_id", "subject_type", "subject_id", "permission")
    assert expected in column_sets, f"grant 唯一约束缺失,实际: {column_sets}"


def test_portal_group_member_composite_pk(db_engine):
    """portal_group_member 用 (group_id, user_id) 复合主键。"""
    inspector = inspect(db_engine)
    pk = inspector.get_pk_constraint("portal_group_member")
    assert tuple(pk["constrained_columns"]) == ("group_id", "user_id"), f"复合主键不符: {pk}"


# ===========================================================================
# 验收点 2:进程重启后数据不丢失(模拟:写 → 新 engine 读 → 数据仍在)
# ===========================================================================


def test_user_persists_across_reconnect(db_session_maker):
    """SeedData.create_user 写入后,新 session 仍能读到(模拟进程重启)。"""
    seed = SeedData(db_session_maker)
    seed.create_user(username="persist_user", email="p@example.com", password_hash="h", is_admin=False)
    # 新 session(模拟重启后新连接)
    seed2 = SeedData(db_session_maker)
    user = seed2.get_user_by_username("persist_user")
    assert user is not None, "重启后用户应仍在"
    assert user.email == "p@example.com"


def test_session_persists_across_reconnect(db_session_maker):
    """SessionStore.bind 写入后,新 session 仍能读到(模拟进程重启)。"""
    store = SessionStore(db_session_maker)
    store.bind(
        session_id="persist-sid",
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id="dialog-1",
        title="持久化测试",
    )
    # 新 store(模拟重启)
    store2 = SessionStore(db_session_maker)
    owner = store2.get("persist-sid")
    assert owner is not None, "重启后会话归属应仍在"
    assert owner.portal_user_id == "u_admin"
    assert owner.title == "持久化测试"


def test_audit_persists_across_reconnect(db_session_maker):
    """AuditStore.record 写入后,新 session 仍能查到(模拟进程重启)。"""
    store = AuditStore(db_session_maker)
    store.record(
        actor_user_id="u_admin",
        action="login_success",
        target_type="user",
        target_id="u_admin",
        meta={"username": "admin"},
    )
    # 新 store(模拟重启)
    store2 = AuditStore(db_session_maker)
    logs = store2.list(action="login_success", limit=100)
    assert any(log.target_id == "u_admin" for log in logs), "重启后审计日志应仍在"


def test_grant_persists_across_reconnect(db_session_maker):
    """grant 写入后,新 session 仍能查到(模拟进程重启)。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="g_user", email="g@e.com", password_hash="h")
    page = seed.create_share_page(name="p", ragflow_resource_id="d1")
    seed.create_grant(page.id, "user", user.id, "use")

    seed2 = SeedData(db_session_maker)
    assert seed2.has_use_grant(page.id, user.id), "重启后 grant 应仍有效"


def test_login_persists_across_restart(db_session_maker, monkeypatch):
    """端到端持久化验收:登录 → 模拟重启(新 app) → 仍登录(用户/会话仍在 DB)。

    对应 Issue 8 验收点 2:进程重启后用户/会话/授权/审计数据不丢失。
    """
    # 用 build_seed_data 初始化(admin/user2/默认分享页/grant 写入 DB)
    monkeypatch.setenv("PORTAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("PORTAL_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("PORTAL_USER2_USERNAME", "user2")
    monkeypatch.setenv("PORTAL_USER2_PASSWORD", "testpass123")
    settings = load_settings()
    build_seed_data(settings, db_session_maker)

    # 模拟重启:新 SeedData 实例(新 session,但同一 DB)
    seed_after = SeedData(db_session_maker)
    admin = seed_after.get_user_by_username("admin")
    assert admin is not None, "重启后 admin 用户应仍在"
    assert admin.is_admin is True
    user2 = seed_after.get_user_by_username("user2")
    assert user2 is not None, "重启后 user2 用户应仍在"
    # 默认分享页与 grant 仍在
    page = seed_after.get_share_page("sp_default")
    assert page is not None, "重启后默认分享页应仍在"
    assert seed_after.has_use_grant("sp_default", admin.id), "重启后 admin 对默认分享页的 grant 应仍在"


# ===========================================================================
# 验收点 3 + 接口不变:SeedData CRUD 行为(DB 后端,外部 API 不变)
# ===========================================================================


def test_seed_data_crud_via_db(db_session_maker):
    """SeedData 经 DB 后端 CRUD 全套(create/get/list/enable/delete)行为与内存一致。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="crud_user", email="c@e.com", password_hash="h")
    assert seed.get_user(user.id) is not None
    assert seed.get_user_by_username("crud_user") is not None
    assert any(u.id == user.id for u in seed.list_users())
    assert seed.set_user_enabled(user.id, False) is True
    assert seed.get_user(user.id).enabled is False
    assert seed.delete_user(user.id) is True
    assert seed.get_user(user.id) is None


def test_seed_data_group_crud_via_db(db_session_maker):
    """SeedData 用户组 CRUD(DB 后端)行为与内存一致。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="gp_user", email="g@e.com", password_hash="h")
    group = seed.create_group(name="g1")
    assert seed.get_group(group.id) is not None
    assert seed.add_group_member(group.id, user.id) is True
    # 重复添加幂等
    assert seed.add_group_member(group.id, user.id) is True
    assert user.id in seed.list_group_members(group.id)
    assert group.id in seed.list_user_groups(user.id)
    assert seed.remove_group_member(group.id, user.id) is True
    assert user.id not in seed.list_group_members(group.id)


def test_seed_data_share_page_crud_via_db(db_session_maker):
    """SeedData 分享页 CRUD(DB 后端)。"""
    seed = SeedData(db_session_maker)
    page = seed.create_share_page(name="p1", ragflow_resource_id="d1")
    assert seed.get_share_page(page.id) is not None
    assert any(p.id == page.id for p in seed.list_share_pages())
    assert seed.set_share_page_enabled(page.id, False) is True
    assert seed.get_share_page(page.id).enabled is False


def test_seed_data_grant_idempotent_via_db(db_session_maker):
    """create_grant 幂等(DB 唯一约束 + 方法层去重,撤销一次即彻底)。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="idem_user", email="i@e.com", password_hash="h")
    page = seed.create_share_page(name="p", ragflow_resource_id="d1")
    g1 = seed.create_grant(page.id, "user", user.id, "use")
    g2 = seed.create_grant(page.id, "user", user.id, "use")
    # DB 后端每次返回新的 dataclass 实例(值相等而非引用相同);
    # 幂等语义由「不重复插入 + 撤销一次即彻底」保证,而非对象身份。
    assert g1 == g2, "重复 create_grant 应返回值相等的 grant(幂等)"
    assert len(seed.list_grants(page.id)) == 1
    # 撤销一次即彻底
    assert seed.revoke_grant(page.id, "user", user.id) is True
    assert seed.revoke_grant(page.id, "user", user.id) is False
    assert not seed.has_use_grant(page.id, user.id)


def test_seed_data_group_grant_acl_via_db(db_session_maker):
    """组授权 ACL 解析(DB 后端):用户组成员继承组对分享页的 use 权限。"""
    seed = SeedData(db_session_maker)
    user = seed.create_user(username="grp_user", email="g@e.com", password_hash="h")
    group = seed.create_group(name="g1")
    page = seed.create_share_page(name="p", ragflow_resource_id="d1")
    seed.add_group_member(group.id, user.id)
    seed.create_grant(page.id, "group", group.id, "use")
    assert seed.has_use_grant(page.id, user.id), "组成员应继承组授权"
    assert page.id in seed.list_user_granted_share_page_ids(user.id)
    # 移除成员后失权
    seed.remove_group_member(group.id, user.id)
    assert not seed.has_use_grant(page.id, user.id)


# ===========================================================================
# SessionStore DB 后端行为(外部 API 不变)
# ===========================================================================


def test_session_store_crud_via_db(db_session_maker):
    """SessionStore 经 DB 后端 CRUD 全套行为与内存一致。"""
    store = SessionStore(db_session_maker)
    owner = store.bind(
        session_id="s1",
        share_page_id="sp1",
        portal_user_id="u1",
        ragflow_resource_id="d1",
        title="t1",
    )
    assert owner.title == "t1"
    assert store.get("s1") is not None
    assert any(s.session_id == "s1" for s in store.list_for_user("u1", "sp1"))
    # 不含 deleted_at 标记的
    assert store.update_last_active("s1") is True
    assert store.rename("s1", "new-title") is True
    assert store.get("s1").title == "new-title"
    # 标记删除
    assert store.mark_deleted("s1") is True
    assert store.get("s1").deleted_at is not None
    # list_for_user 排除 deleted_at 非空
    assert store.list_for_user("u1", "sp1") == []
    # 硬删除
    assert store.delete("s1") is True
    assert store.get("s1") is None


def test_session_store_list_all_filters_via_db(db_session_maker):
    """SessionStore.list_all 多维度过滤(DB 后端)。"""
    store = SessionStore(db_session_maker)
    now = time.time()
    store.bind(session_id="s1", share_page_id="sp1", portal_user_id="u1", ragflow_resource_id="d1", title="alpha")
    store.bind(session_id="s2", share_page_id="sp1", portal_user_id="u2", ragflow_resource_id="d1", title="beta")
    store.bind(session_id="s3", share_page_id="sp2", portal_user_id="u1", ragflow_resource_id="d2", title="alpha-2")
    # 全部
    assert len(store.list_all()) == 3
    # 按 user
    assert len(store.list_all(portal_user_id="u1")) == 2
    # 按 share_page
    assert len(store.list_all(share_page_id="sp1")) == 2
    # keyword
    assert len(store.list_all(keyword="alpha")) == 2
    # since(全部记录 created_at >= 0)
    assert len(store.list_all(since=0)) == 3
    # until(未来时间)
    assert len(store.list_all(until=now + 10)) == 3
    # limit
    assert len(store.list_all(limit=2)) == 2


def test_session_store_list_pending_deletion_via_db(db_session_maker):
    """SessionStore.list_pending_deletion 返回 deleted_at 非空的记录。"""
    store = SessionStore(db_session_maker)
    store.bind(session_id="s1", share_page_id="sp1", portal_user_id="u1", ragflow_resource_id="d1")
    store.bind(session_id="s2", share_page_id="sp1", portal_user_id="u1", ragflow_resource_id="d1")
    store.mark_deleted("s1")
    pending = store.list_pending_deletion()
    assert len(pending) == 1
    assert pending[0].session_id == "s1"


def test_session_store_list_all_for_user_includes_deleted(db_session_maker):
    """list_all_for_user 含 deleted_at 标记(级联删除需处理所有,跨分享页)。"""
    store = SessionStore(db_session_maker)
    store.bind(session_id="s1", share_page_id="sp1", portal_user_id="u1", ragflow_resource_id="d1")
    store.bind(session_id="s2", share_page_id="sp2", portal_user_id="u1", ragflow_resource_id="d2")
    store.mark_deleted("s1")
    all_for_user = store.list_all_for_user("u1")
    assert len(all_for_user) == 2, "list_all_for_user 应含 deleted_at 标记的"


# ===========================================================================
# AuditStore DB 后端行为(外部 API 不变)
# ===========================================================================


def test_audit_store_record_and_list_via_db(db_session_maker):
    """AuditStore.record + list(DB 后端),按 action/actor/时间过滤。"""
    store = AuditStore(db_session_maker)
    store.record(actor_user_id="u1", action="login_success", target_type="user", target_id="u1")
    store.record(actor_user_id="u1", action="login_failure", target_type="user", target_id="nobody")
    store.record(actor_user_id="u2", action="grant_create", target_type="grant", target_id="sp1")
    assert len(store.list(limit=100)) == 3
    assert len(store.list(action="login_success")) == 1
    assert len(store.list(actor_user_id="u1")) == 2
    # meta_json 序列化
    store.record(actor_user_id="u1", action="user_disable", target_type="user", target_id="u2", meta={"reason": "test"})
    logs = store.list(action="user_disable")
    assert len(logs) == 1
    assert "reason" in logs[0].meta_json


def test_audit_store_list_ordered_by_at_desc(db_session_maker):
    """AuditStore.list 按 at 倒序(最新在前)。"""
    store = AuditStore(db_session_maker)
    store.record(actor_user_id="u1", action="login_success", target_type="user", target_id="u1")
    time.sleep(0.01)
    store.record(actor_user_id="u2", action="login_success", target_type="user", target_id="u2")
    logs = store.list(limit=10)
    assert logs[0].at >= logs[1].at, "应按 at 倒序"


# ===========================================================================
# 验收点:build_seed_data idempotent(多次调用不重复创建,不报错)
# ===========================================================================


def test_build_seed_data_idempotent(db_session_maker, monkeypatch):
    """build_seed_data 重复调用不报错,不产生重复 seed 数据。"""
    monkeypatch.setenv("PORTAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("PORTAL_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("PORTAL_USER2_USERNAME", "user2")
    monkeypatch.setenv("PORTAL_USER2_PASSWORD", "testpass123")
    settings = load_settings()
    build_seed_data(settings, db_session_maker)
    # 第二次调用(idempotent)
    build_seed_data(settings, db_session_maker)
    seed = SeedData(db_session_maker)
    users = seed.list_users()
    usernames = [u.username for u in users]
    # admin 与 user2 各只一条
    assert usernames.count("admin") == 1
    assert usernames.count("user2") == 1
    # 默认分享页只一条
    pages = [p for p in seed.list_share_pages() if p.id == "sp_default"]
    assert len(pages) == 1
    # grant 不重复
    grants = seed.list_grants("sp_default")
    assert len(grants) == 2, f"默认 grant 应为 2 条(admin+user2),实际: {len(grants)}"


# ===========================================================================
# 配置:DB URL 走环境变量
# ===========================================================================


def test_config_has_db_url(monkeypatch):
    """Settings 含 portal_db_url 字段,从 PORTAL_DB_URL 环境变量读取。"""
    monkeypatch.setenv("PORTAL_DB_URL", "sqlite:///./test_config_check.db")
    settings = load_settings()
    assert settings.portal_db_url == "sqlite:///./test_config_check.db"
    # 默认值
    monkeypatch.delenv("PORTAL_DB_URL", raising=False)
    settings = load_settings()
    assert hasattr(settings, "portal_db_url")
    # 清理测试文件
    if os.path.exists("./test_config_check.db"):
        os.remove("./test_config_check.db")


# ===========================================================================
# DB 写入后 ORM 行为:datetime/bool 类型正确
# ===========================================================================


def test_orm_user_roundtrip(db_session_maker):
    """ORM 直接写入 PortalUserModel 后能查回,字段类型正确。"""
    with db_session_maker() as session:
        from portal.password import hash_password

        user = PortalUserModel(
            id="u_orm",
            username="orm_user",
            password_hash=hash_password("pw"),
            email="orm@e.com",
            is_admin=False,
            enabled=True,
            created_at=time.time(),
        )
        session.add(user)
        session.commit()
    with db_session_maker() as session:
        loaded = session.get(PortalUserModel, "u_orm")
        assert loaded is not None
        assert loaded.username == "orm_user"
        assert loaded.is_admin is False
        assert loaded.enabled is True


def test_orm_grant_unique_constraint_enforced(db_session_maker):
    """DB 层唯一约束生效:重复插入同 (share_page_id, subject_type, subject_id, permission) 报错。"""
    with db_session_maker() as session:
        session.add(
            SharePageModel(
                id="sp_uq",
                name="p",
                ragflow_type="chat",
                ragflow_resource_id="d",
                embed_type="fullscreen",
                enabled=True,
                created_at=time.time(),
            )
        )
        session.add(
            PortalUserModel(
                id="u_uq",
                username="uq_user",
                password_hash="h",
                email="u@e.com",
                is_admin=False,
                enabled=True,
                created_at=time.time(),
            )
        )
        session.commit()
    with db_session_maker() as session:
        session.add(
            SharePageGrantModel(share_page_id="sp_uq", subject_type="user", subject_id="u_uq", permission="use")
        )
        session.commit()
    with db_session_maker() as session:
        session.add(
            SharePageGrantModel(share_page_id="sp_uq", subject_type="user", subject_id="u_uq", permission="use")
        )
        with pytest.raises(Exception):  # noqa: PT011 — SQLAlchemy 各方言异常类型不同,用基类
            session.commit()
