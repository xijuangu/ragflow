"""数据库访问层 — Slice 8 DB 持久化(SQLAlchemy 2.x ORM)。

设计原则(deep module):
  - 外部接口极简:`init_db(engine)` + `create_session_maker(engine)` 两个函数,
    + 7 张表的 ORM 模型类(供测试/迁移脚本直接操作)。
  - DB 细节(引擎方言、连接池、事务、表结构)全部封在内部。
  - 生产用 MySQL(pymysql 驱动,sync engine);单元测试用 SQLite in-memory。
    通过 `PORTAL_DB_URL` 环境变量切换,本模块不读环境变量(由 config.py 负责)。

7 张表 schema 对应 PRD 数据模型 + ISSUES.md Issue 8 + Issue 13(多租户 org_id):
  portal_user(id PK, username UNIQUE, password_hash, email, is_admin, enabled, created_at,
              sso_provider NULL, sso_external_id NULL,
              org_id DEFAULT 'default', org_admin DEFAULT False)  — Slice 13
  portal_group(id PK, name, created_at, org_id DEFAULT 'default')  — Slice 13
  portal_group_member(group_id+user_id 复合 PK, added_at, org_id DEFAULT 'default')  — Slice 13
  share_page(id PK, name, ragflow_type, ragflow_resource_id, embed_type, enabled, created_at,
             org_id DEFAULT 'default')  — Slice 13
  share_page_grant(id PK autoincrement, share_page_id, subject_type, subject_id, permission,
                   org_id DEFAULT 'default',  — Slice 13
                   UNIQUE(share_page_id, subject_type, subject_id, permission))
  chat_session_owner(session_id PK, share_page_id, portal_user_id NOT NULL,
                     ragflow_resource_id, title, created_at, last_active_at,
                     deleted_at NULLABLE, message_count,
                     org_id DEFAULT 'default')  — Slice 13
  audit_log(id PK, actor_user_id, action, target_type, target_id, at, meta_json,
            org_id DEFAULT 'default')  — Slice 13

`init_db(engine)` 用 `Base.metadata.create_all(engine)`,天然 idempotent:
  - 表已存在 → skip(IF NOT EXISTS 语义)
  - 表不存在 → 创建
  - 不会 ALTER 已有表结构(增量字段由 _migrate_add_sso_columns / _migrate_add_org_columns 补)
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

# 7 张表名集合(供测试与迁移脚本检查 schema 完整性)
PORTAL_TABLES: set[str] = {
    "portal_user",
    "portal_group",
    "portal_group_member",
    "share_page",
    "share_page_grant",
    "chat_session_owner",
    "audit_log",
}


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 Declarative Base。

    用显式 MetaData(naming_convention)保证约束名跨方言一致,
    便于 Alembic/迁移工具未来接入(Slice 8 用 create_all,无 Alembic)。
    """

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class PortalUserModel(Base):
    """portal_user 表 ORM 模型。

    Slice 14:加 sso_provider / sso_external_id(可空),用于 SSO 用户匹配。
    自建账号用户这两个字段为 NULL;SSO 用户首次登录时写入。
    Slice 13:加 org_id(默认 'default',多租户隔离)+ org_admin(默认 False,
    介于普通用户与平台 is_admin 之间的 org 级管理员角色)。
    """

    __tablename__ = "portal_user"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Slice 14:SSO 登录字段(可空,自建账号用户为 NULL)
    sso_provider: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    sso_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    # Slice 13:多租户字段(org_id 默认 'default';org_admin 默认 False)
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    org_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PortalGroupModel(Base):
    """portal_group 表 ORM 模型。

    Slice 13:加 org_id(默认 'default'),组按 org 隔离。
    """

    __tablename__ = "portal_group"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Slice 13:多租户 org_id
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)


class PortalGroupMemberModel(Base):
    """portal_group_member 表 ORM 模型((group_id, user_id) 复合主键)。

    Slice 13:加 org_id(默认 'default',与 group/user 的 org_id 一致,便于按 org 筛选)。
    """

    __tablename__ = "portal_group_member"

    group_id: Mapped[str] = mapped_column(String(64), ForeignKey("portal_group.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("portal_user.id"), primary_key=True)
    added_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Slice 13:多租户 org_id(冗余,便于按 org 筛选组成员关系)
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)


class SharePageModel(Base):
    """share_page 表 ORM 模型。

    Slice 13:加 org_id(默认 'default'),分享页按 org 隔离。
    Slice 15:加 is_public 字段(默认 False),公开分享页免登录访问。
    """

    __tablename__ = "share_page"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ragflow_type: Mapped[str] = mapped_column(String(32), default="chat", nullable=False)
    ragflow_resource_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    embed_type: Mapped[str] = mapped_column(String(32), default="fullscreen", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Slice 13:多租户 org_id
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    # Slice 15:公开分享(免登录访问 + IP 限流),默认 False
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SharePageGrantModel(Base):
    """share_page_grant 表 ORM 模型。

    唯一约束 (share_page_id, subject_type, subject_id, permission) 对应
    SeedData.create_grant 幂等:重复创建返回同一 grant,撤销一次即彻底
    (避免误创建两次导致撤销一次后残留 grant 仍使 has_use_grant=True 的安全漏洞)。

    Slice 13:加 org_id(默认 'default',与 share_page 的 org_id 一致,便于按 org 筛选 grant)。
    """

    __tablename__ = "share_page_grant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    share_page_id: Mapped[str] = mapped_column(String(64), ForeignKey("share_page.id"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), default="use", nullable=False)
    # Slice 13:多租户 org_id(冗余,便于按 org 筛选 grant)
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "share_page_id",
            "subject_type",
            "subject_id",
            "permission",
            name="uq_share_page_grant_quad",
        ),
    )


class ChatSessionOwnerModel(Base):
    """chat_session_owner 表 ORM 模型。

    session_id 为主键(对应 RAGFlow API4Conversation.id);
    portal_user_id NOT NULL(预创建时即绑定到当前用户,PRD D3);
    deleted_at NULLABLE(Slice 5 双删失败标记,None=正常,非空=待重试);
    message_count(Slice 6:恢复会话时更新,SSE 代理后可能滞后)。

    Slice 13:加 org_id(默认 'default',与用户/分享页的 org_id 一致,
    便于按 org 筛选会话;网关跨 org 访问会话 → 403)。
    """

    __tablename__ = "chat_session_owner"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    share_page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    portal_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ragflow_resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_active_at: Mapped[float] = mapped_column(Float, nullable=False)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Slice 13:多租户 org_id
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)


class AuditLogModel(Base):
    """audit_log 表 ORM 模型(Slice 6,敏感操作,永久保留 PR D8b)。

    Slice 13:加 org_id(默认 'default',审计日志按 org 维度筛选,对应验收点 5)。
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[float] = mapped_column(Float, nullable=False)
    meta_json: Mapped[str] = mapped_column(String(4096), default="", nullable=False)
    # Slice 13:多租户 org_id(审计维度,可按 org 筛选)
    org_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)


def _migrate_add_sso_columns(engine) -> None:
    """Slice 14:idempotent 迁移 — 给已有 portal_user 表加 sso_provider / sso_external_id 列。

    `create_all` 不会 ALTER 已有表结构(只创建缺失的表)。对已存在的 portal_user 表,
    需用 ALTER TABLE ADD COLUMN 补字段。检查列是否已存在,已存在则跳过(idempotent)。

    SQLite/MySQL 均支持 ``ALTER TABLE ... ADD COLUMN ...``;NULL 列无需默认值。
    """
    inspector = inspect(engine)
    if "portal_user" not in inspector.get_table_names():
        return  # 表不存在(create_all 会建),无需迁移
    existing_cols = {c["name"] for c in inspector.get_columns("portal_user")}
    with engine.begin() as conn:
        if "sso_provider" not in existing_cols:
            conn.execute(text("ALTER TABLE portal_user ADD COLUMN sso_provider VARCHAR(32) NULL"))
        if "sso_external_id" not in existing_cols:
            conn.execute(text("ALTER TABLE portal_user ADD COLUMN sso_external_id VARCHAR(255) NULL"))


# Slice 13:7 张表需加 org_id 列;portal_user 额外加 org_admin 列
# (table_name, [columns_to_add]) — 每个列为 (name, sql_type, default_clause)
_ORG_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "portal_user": [
        ("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL"),
        ("org_admin", "BOOLEAN", "DEFAULT 0 NOT NULL"),
    ],
    "portal_group": [("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL")],
    "portal_group_member": [("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL")],
    "share_page": [("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL")],
    "share_page_grant": [("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL")],
    "chat_session_owner": [("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL")],
    "audit_log": [("org_id", "VARCHAR(64)", "DEFAULT 'default' NOT NULL")],
}


def _migrate_add_org_columns(engine) -> None:
    """Slice 13:idempotent 迁移 — 给 7 张已有表加 org_id 列(+ portal_user 加 org_admin)。

    `create_all` 不会 ALTER 已有表结构。对已存在的表,需用 ALTER TABLE ADD COLUMN
    补 org_id 字段。检查列是否已存在,已存在则跳过(idempotent)。

    现有数据归入默认 org:ADD COLUMN ... DEFAULT 'default' NOT NULL,
    SQLite/MySQL 均会让现有行自动获得 DEFAULT 值(对应验收点 1)。

    SQLite 注意:ALTER TABLE ADD COLUMN 带 NOT NULL 必须 DEFAULT(否则报错);
    MySQL 8+ 同理。此处 DEFAULT 'default' 满足两方言。
    BOOLEAN 在 SQLite 是亲和类型(存 0/1),DEFAULT 0 等价 False。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, cols in _ORG_COLUMNS.items():
        if table not in existing_tables:
            continue  # 表不存在(create_all 会建),无需迁移
        existing_cols = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for col_name, col_type, default_clause in cols:
                if col_name not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type} {default_clause}"))


def _migrate_add_is_public_column(engine) -> None:
    """Slice 15:idempotent 迁移 — 给已有 share_page 表加 is_public 列(默认 False)。

    与 ``_migrate_add_sso_columns`` 同模式:create_all 不 ALTER 已有表结构,
    需用 ALTER TABLE ADD COLUMN 补字段。检查列是否已存在,已存在则跳过(idempotent)。

    SQLite/MySQL 均支持 ``ALTER TABLE ... ADD COLUMN ...``;NOT NULL 列需带默认值
    (SQLite 用 0/1 表示 BOOLEAN,MySQL 用 FALSE)。
    """
    inspector = inspect(engine)
    if "share_page" not in inspector.get_table_names():
        return  # 表不存在(create_all 会建),无需迁移
    existing_cols = {c["name"] for c in inspector.get_columns("share_page")}
    if "is_public" in existing_cols:
        return  # 列已存在,跳过
    with engine.begin() as conn:
        # SQLite/MySQL 都支持 BOOLEAN NOT NULL DEFAULT 0/False
        conn.execute(text("ALTER TABLE share_page ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT 0"))


def init_db(engine) -> None:
    """创建所有 7 张表(idempotent)。

    `Base.metadata.create_all` 内部用 `IF NOT EXISTS`(SQLAlchemy 各方言自动处理),
    表已存在则跳过,不报错。可重复执行,对应验收点 1。

    Slice 14:create_all 之后执行 _migrate_add_sso_columns,给已有 portal_user 表
    补 sso_provider / sso_external_id 列(create_all 不 ALTER 已有表结构)。
    新建表时模型已含这两列,迁移函数检测到列已存在自动跳过(idempotent)。

    Slice 13:create_all 之后执行 _migrate_add_org_columns,给 7 张已有表补
    org_id 列(以及 portal_user 的 org_admin 列)。新建表时模型已含这些列,
    迁移函数检测到列已存在自动跳过(idempotent)。现有数据归入默认 org('default')。

    Slice 15:create_all 之后执行 _migrate_add_is_public_column,给已有 share_page 表
    补 is_public 列(默认 False)。新建表时模型已含此列,迁移函数检测到列已存在自动跳过。
    """
    Base.metadata.create_all(engine)
    _migrate_add_sso_columns(engine)
    _migrate_add_org_columns(engine)
    _migrate_add_is_public_column(engine)


def create_session_maker(engine) -> sessionmaker:
    """基于 engine 构造 session 工厂(``sessionmaker``)。

    用法:
        sm = create_session_maker(engine)
        with sm() as session:
            session.add(...)
            session.commit()
    """
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def make_sqlite_in_memory_engine():
    """构造 SQLite in-memory engine(StaticPool 共享连接,保证 :memory: 跨 session 可见)。

    供测试与本地开发用。StaticPool 让所有 session 复用同一底层连接,
    否则 :memory: 每连接独立 DB,跨 session 数据不可见。
    """
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
