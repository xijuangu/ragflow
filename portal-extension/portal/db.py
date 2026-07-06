"""数据库访问层 — Slice 8 DB 持久化(SQLAlchemy 2.x ORM)。

设计原则(deep module):
  - 外部接口极简:`init_db(engine)` + `create_session_maker(engine)` 两个函数,
    + 7 张表的 ORM 模型类(供测试/迁移脚本直接操作)。
  - DB 细节(引擎方言、连接池、事务、表结构)全部封在内部。
  - 生产用 MySQL(pymysql 驱动,sync engine);单元测试用 SQLite in-memory。
    通过 `PORTAL_DB_URL` 环境变量切换,本模块不读环境变量(由 config.py 负责)。

7 张表 schema 对应 PRD 数据模型 + ISSUES.md Issue 8:
  portal_user(id PK, username UNIQUE, password_hash, email, is_admin, enabled, created_at)
  portal_group(id PK, name, created_at)
  portal_group_member(group_id+user_id 复合 PK, added_at)
  share_page(id PK, name, ragflow_type, ragflow_resource_id, embed_type, enabled, created_at)
  share_page_grant(id PK autoincrement, share_page_id, subject_type, subject_id, permission,
                   UNIQUE(share_page_id, subject_type, subject_id, permission))
  chat_session_owner(session_id PK, share_page_id, portal_user_id NOT NULL,
                     ragflow_resource_id, title, created_at, last_active_at,
                     deleted_at NULLABLE, message_count)
  audit_log(id PK, actor_user_id, action, target_type, target_id, at, meta_json)

`init_db(engine)` 用 `Base.metadata.create_all(engine)`,天然 idempotent:
  - 表已存在 → skip(IF NOT EXISTS 语义)
  - 表不存在 → 创建
  - 不会 ALTER 已有表结构(增量字段需单独迁移,本 slice 不涉及)
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
    """portal_user 表 ORM 模型。"""

    __tablename__ = "portal_user"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class PortalGroupModel(Base):
    """portal_group 表 ORM 模型。"""

    __tablename__ = "portal_group"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class PortalGroupMemberModel(Base):
    """portal_group_member 表 ORM 模型((group_id, user_id) 复合主键)。"""

    __tablename__ = "portal_group_member"

    group_id: Mapped[str] = mapped_column(String(64), ForeignKey("portal_group.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("portal_user.id"), primary_key=True)
    added_at: Mapped[float] = mapped_column(Float, nullable=False)


class SharePageModel(Base):
    """share_page 表 ORM 模型。"""

    __tablename__ = "share_page"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ragflow_type: Mapped[str] = mapped_column(String(32), default="chat", nullable=False)
    ragflow_resource_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    embed_type: Mapped[str] = mapped_column(String(32), default="fullscreen", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class SharePageGrantModel(Base):
    """share_page_grant 表 ORM 模型。

    唯一约束 (share_page_id, subject_type, subject_id, permission) 对应
    SeedData.create_grant 幂等:重复创建返回同一 grant,撤销一次即彻底
    (避免误创建两次导致撤销一次后残留 grant 仍使 has_use_grant=True 的安全漏洞)。
    """

    __tablename__ = "share_page_grant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    share_page_id: Mapped[str] = mapped_column(String(64), ForeignKey("share_page.id"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), default="use", nullable=False)

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


class AuditLogModel(Base):
    """audit_log 表 ORM 模型(Slice 6,8 类敏感操作,永久保留 PR D8b)。"""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[float] = mapped_column(Float, nullable=False)
    meta_json: Mapped[str] = mapped_column(String(4096), default="", nullable=False)


def init_db(engine) -> None:
    """创建所有 7 张表(idempotent)。

    `Base.metadata.create_all` 内部用 `IF NOT EXISTS`(SQLAlchemy 各方言自动处理),
    表已存在则跳过,不报错。可重复执行,对应验收点 1。

    注意:不会 ALTER 已有表结构(新增字段需单独迁移脚本,本 slice 不涉及)。
    """
    Base.metadata.create_all(engine)


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
