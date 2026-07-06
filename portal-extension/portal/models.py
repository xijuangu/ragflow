"""数据模型 — Slice 1 + Slice 2 + Slice 3 + Slice 4 + Slice 5 + Slice 6 + Slice 8。

数据模型(对应 ISSUES.md Issue 1 + Issue 2 + Issue 3 + Issue 4 + Issue 5 + Issue 6 + Issue 8):
  portal_user(id, username, password_hash, email, is_admin, enabled, created_at)
  portal_group(id, name, created_at)
  portal_group_member(group_id, user_id, added_at)  — 本 slice 用 set 简化
  share_page(id, name, ragflow_type='chat', ragflow_resource_id,
             embed_type='fullscreen', enabled)
  share_page_grant(share_page_id, subject_type, subject_id, permission='use')
  chat_session_owner(session_id, share_page_id, portal_user_id NOT NULL,
                     ragflow_resource_id, title, created_at, last_active_at,
                     deleted_at)  — Slice 5 加 deleted_at(双删失败标记)
  audit_log(id, actor_user_id, action, target_type, target_id, at, meta_json)
    — Slice 6 加审计日志(8 类敏感操作,内存存储,永久保留)

Slice 4 把 Slice 1-3 的硬编码 SeedData 改为可变内存存储,新增用户组与完整 CRUD 方法;
has_use_grant 升级支持 user + group 两种 subject_type(用户组继承)。
Slice 5 加会话重命名/删除/标记删除方法,用户硬删除(delete_user 级联清理组成员关系)。
Slice 6 加 AuditLog + AuditStore(8 类敏感操作审计,内存存储,永久保留无 TTL),
SessionStore.list_all 支持管理员跨用户会话查询(按用户/分享页/时间过滤)。

Slice 8 DB 持久化迁移(内存存储 → SQLAlchemy):
  - SeedData / SessionStore / AuditStore 内部从 dict/list 改为 SQLAlchemy ORM,
    外部方法签名完全不变(routes/gateway 不感知存储层变更)。
  - 构造函数接受 session_maker(由 main.py 在启动时注入)。
  - TokenStore 保留内存(见 gateway.py):T_short 5min 过期 + 可撤销,
    重启失效是可接受的(用户重新登录获取新 T_short),文档说明见 gateway.py。
  - 测试隔离:单元测试用 SQLite in-memory(见 tests/conftest.py),不依赖外部 MySQL。
"""

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from portal.config import Settings
from portal.db import (
    AuditLogModel,
    ChatSessionOwnerModel,
    PortalGroupMemberModel,
    PortalGroupModel,
    PortalUserModel,
    SharePageGrantModel,
    SharePageModel,
)
from portal.password import hash_password

logger = logging.getLogger(__name__)

# 类型别名约束字面量取值(消除 Primitive Obsession)
Permission = Literal["use", "manage"]
RagflowType = Literal["chat", "agent"]
EmbedType = Literal["fullscreen", "widget"]
SubjectType = Literal["user", "group"]
# Slice 6 审计日志枚举(PR D7b:仅覆盖敏感操作,8 类)
# Slice 15 加 public_chat(公开访问审计,可选开关 PUBLIC_AUDIT_ENABLED)
AuditAction = Literal[
    "login_success",
    "login_failure",
    "grant_create",
    "grant_revoke",
    "session_delete",
    "session_view_elevated",
    "user_enable",
    "user_disable",
    "public_chat",
]
AuditTargetType = Literal["user", "share_page", "session", "grant"]


# ---------------------------------------------------------------------------
# 领域数据类(dataclass,与 ORM 模型解耦 — 路由层只认 dataclass,不认 ORM)
# ---------------------------------------------------------------------------


@dataclass
class PortalUser:
    """门户用户(对应 portal_user 表)。

    Slice 4 补齐 email / created_at 字段(D5 数据模型)。
    Slice 14 加 sso_provider / sso_external_id(可空,自建账号用户为 None;
    SSO 用户首次登录时写入,用于匹配 IdP 返回的 sub)。
    """

    id: str
    username: str
    password_hash: str
    email: str = ""
    is_admin: bool = False
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    # Slice 14:SSO 登录字段(可空,自建账号用户为 None)
    sso_provider: str | None = None
    sso_external_id: str | None = None


@dataclass
class PortalGroup:
    """用户组(对应 portal_group 表,D4)。

    用于按组批量授权,组成员继承组对分享页的 use 权限。
    """

    id: str
    name: str
    created_at: float = field(default_factory=time.time)


@dataclass
class SharePage:
    """分享页(对应 share_page 表)。

    ragflow_type / embed_type 一期固定值(D9),不开放选择器。
    Slice 15:加 is_public 字段(默认 False),公开分享页免登录访问 + IP 限流。
    """

    id: str
    name: str
    ragflow_type: RagflowType = "chat"
    ragflow_resource_id: str = ""
    embed_type: EmbedType = "fullscreen"
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    # Slice 15:公开分享(免登录访问 + IP 限流),默认 False
    is_public: bool = False


@dataclass
class SharePageGrant:
    """分享页授权(对应 share_page_grant 表)。

    subject_type ∈ {user, group}:Slice 4 启用 group。
    permission ∈ {use, manage}:一期只解析 use(D5)。
    """

    share_page_id: str
    subject_type: SubjectType = "user"
    subject_id: str = ""
    permission: Permission = "use"


@dataclass
class ChatSessionOwner:
    """会话归属记录(对应 chat_session_owner 表)。

    session_id 为主键(对应 RAGFlow API4Conversation.id);
    portal_user_id NOT NULL(预创建时即绑定到当前用户)。
    Slice 5 加 deleted_at 字段(双删失败标记,None=正常,非空=待重试)。
    """

    session_id: str
    share_page_id: str
    portal_user_id: str  # NOT NULL — 归属绑定到具体门户用户
    ragflow_resource_id: str  # 对应 share_page.ragflow_resource_id(dialog_id)
    title: str = ""
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    deleted_at: float | None = None  # Slice 5:双删失败标记(None=正常,非空=待重试)
    # Slice 6 修复:消息数(预创建为 0,恢复会话 GET history 时用 len(messages) 更新;
    # SSE 代理后可能滞后,但字段存在满足 spec「元数据含消息数」要求)。
    message_count: int = 0


# ---------------------------------------------------------------------------
# ORM ↔ dataclass 转换(deep module:DB 细节封在内部,路由层只拿 dataclass)
# ---------------------------------------------------------------------------


def _user_from_orm(row: PortalUserModel) -> PortalUser:
    return PortalUser(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        email=row.email,
        is_admin=row.is_admin,
        enabled=row.enabled,
        created_at=row.created_at,
        sso_provider=row.sso_provider,
        sso_external_id=row.sso_external_id,
    )


def _group_from_orm(row: PortalGroupModel) -> PortalGroup:
    return PortalGroup(id=row.id, name=row.name, created_at=row.created_at)


def _share_page_from_orm(row: SharePageModel) -> SharePage:
    return SharePage(
        id=row.id,
        name=row.name,
        ragflow_type=row.ragflow_type,
        ragflow_resource_id=row.ragflow_resource_id,
        embed_type=row.embed_type,
        enabled=row.enabled,
        created_at=row.created_at,
        is_public=row.is_public,
    )


def _grant_from_orm(row: SharePageGrantModel) -> SharePageGrant:
    return SharePageGrant(
        share_page_id=row.share_page_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        permission=row.permission,
    )


def _session_owner_from_orm(row: ChatSessionOwnerModel) -> ChatSessionOwner:
    return ChatSessionOwner(
        session_id=row.session_id,
        share_page_id=row.share_page_id,
        portal_user_id=row.portal_user_id,
        ragflow_resource_id=row.ragflow_resource_id,
        title=row.title,
        created_at=row.created_at,
        last_active_at=row.last_active_at,
        deleted_at=row.deleted_at,
        message_count=row.message_count,
    )


class SessionStore:
    """会话归属表 — Slice 8 改为 SQLAlchemy 持久化(外部 API 不变)。

    类似 TokenStore,但记录的是「session_id → 门户用户」的归属关系。
    Slice 2 不持久化,Slice 4 仍内存,Slice 8 迁移到 DB。
    """

    def __init__(self, session_maker: sessionmaker):
        self._sm = session_maker

    def bind(
        self,
        session_id: str,
        share_page_id: str,
        portal_user_id: str,
        ragflow_resource_id: str,
        title: str = "",
    ) -> ChatSessionOwner:
        """预创建 session 后绑定到当前用户(对应验收点 1:归属绑定)。"""
        now = time.time()
        owner = ChatSessionOwner(
            session_id=session_id,
            share_page_id=share_page_id,
            portal_user_id=portal_user_id,
            ragflow_resource_id=ragflow_resource_id,
            title=title or "新会话",
            created_at=now,
            last_active_at=now,
        )
        with self._sm() as session:
            row = ChatSessionOwnerModel(
                session_id=owner.session_id,
                share_page_id=owner.share_page_id,
                portal_user_id=owner.portal_user_id,
                ragflow_resource_id=owner.ragflow_resource_id,
                title=owner.title,
                created_at=owner.created_at,
                last_active_at=owner.last_active_at,
                deleted_at=None,
                message_count=0,
            )
            session.add(row)
            session.commit()
        return owner

    def get(self, session_id: str):
        """按 session_id 取回归属记录;不存在返回 None。"""
        with self._sm() as session:
            row = session.get(ChatSessionOwnerModel, session_id)
            return _session_owner_from_orm(row) if row is not None else None

    def list_for_user(self, portal_user_id: str, share_page_id: str) -> list:
        """按用户 + 分享页查询会话列表(对应验收点 4:我的会话)。

        基础隔离:只返回 portal_user_id 匹配的记录(用户看不到他人的 session)。
        Slice 5:排除 deleted_at 非空的记录(标记待重试的不在用户列表显示)。
        """
        with self._sm() as session:
            stmt = select(ChatSessionOwnerModel).where(
                ChatSessionOwnerModel.portal_user_id == portal_user_id,
                ChatSessionOwnerModel.share_page_id == share_page_id,
                ChatSessionOwnerModel.deleted_at.is_(None),
            )
            return [_session_owner_from_orm(r) for r in session.scalars(stmt)]

    def update_last_active(self, session_id: str) -> bool:
        """更新会话最后活跃时间(对应验收点 3:对话后 last_active_at 更新)。

        返回 True 表示 session 存在并已更新;False 表示 session 不存在。
        """
        with self._sm() as session:
            row = session.get(ChatSessionOwnerModel, session_id)
            if row is None:
                return False
            row.last_active_at = time.time()
            session.commit()
            return True

    def rename(self, session_id: str, title: str) -> bool:
        """重命名会话标题(对应 Slice 5 验收点:用户重命名自己的会话)。

        返回 True 表示 session 存在并已更新;False 表示 session 不存在。
        """
        with self._sm() as session:
            row = session.get(ChatSessionOwnerModel, session_id)
            if row is None:
                return False
            row.title = title
            session.commit()
            return True

    def update_message_count(self, session_id: str, count: int) -> bool:
        """更新会话消息数(恢复会话后用 len(messages) 同步,Slice 6 验收点 3)。

        Slice 8 新增:DB 后端 get() 返回独立 dataclass 实例,路由层直接改属性
        不会持久化,故提供此公开 API 由路由层调用。
        返回 True 表示 session 存在并已更新;False 表示 session 不存在。
        """
        with self._sm() as session:
            row = session.get(ChatSessionOwnerModel, session_id)
            if row is None:
                return False
            row.message_count = count
            session.commit()
            return True

    def delete(self, session_id: str) -> bool:
        """硬删除会话归属记录(从存储移除,对应 Slice 5 双删成功后清门户侧)。

        返回 True 表示 session 存在并已删除;False 表示 session 不存在。
        """
        with self._sm() as session:
            row = session.get(ChatSessionOwnerModel, session_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def mark_deleted(self, session_id: str) -> bool:
        """标记会话为待删除(双删失败时:RAGFlow 删除失败,门户侧标记 deleted_at 待重试)。

        记录保留(不从存储移除),供后台重试任务查询。
        返回 True 表示 session 存在并已标记;False 表示 session 不存在。
        """
        with self._sm() as session:
            row = session.get(ChatSessionOwnerModel, session_id)
            if row is None:
                return False
            row.deleted_at = time.time()
            session.commit()
            return True

    def list_all_for_user(self, portal_user_id: str) -> list:
        """返回用户的所有会话(跨分享页,用于 Slice 5 用户硬删除级联)。

        与 list_for_user 的区别:不限 share_page_id,且包含 deleted_at 标记的记录
        (级联删除需处理所有会话,包括待重试的)。
        """
        with self._sm() as session:
            stmt = select(ChatSessionOwnerModel).where(ChatSessionOwnerModel.portal_user_id == portal_user_id)
            return [_session_owner_from_orm(r) for r in session.scalars(stmt)]

    def list_pending_deletion(self) -> list:
        """返回 deleted_at 非空的会话(供后台重试任务查询,Slice 5 提供查询不实现重试)。"""
        with self._sm() as session:
            stmt = select(ChatSessionOwnerModel).where(ChatSessionOwnerModel.deleted_at.is_not(None))
            return [_session_owner_from_orm(r) for r in session.scalars(stmt)]

    def list_all(
        self,
        portal_user_id: str | None = None,
        share_page_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        keyword: str | None = None,
        limit: int = 100,
    ) -> list:
        """管理员跨用户列出所有会话(按用户/分享页/时间/关键词过滤,Slice 6 验收点 2)。

        与 list_for_user 的区别:不限 portal_user_id(管理员视角),支持多维度过滤;
        排除 deleted_at 非空的记录(待重试的会话由 list_pending_deletion 单独查询)。
        默认按 created_at 倒序(最新的在前),limit 默认 100。
        keyword 按标题模糊匹配(大小写不敏感的 ``in`` 匹配,None 表示不过滤)。
        """
        kw_lower = keyword.lower() if keyword else None
        with self._sm() as session:
            stmt = select(ChatSessionOwnerModel).where(ChatSessionOwnerModel.deleted_at.is_(None))
            if portal_user_id is not None:
                stmt = stmt.where(ChatSessionOwnerModel.portal_user_id == portal_user_id)
            if share_page_id is not None:
                stmt = stmt.where(ChatSessionOwnerModel.share_page_id == share_page_id)
            if since is not None:
                stmt = stmt.where(ChatSessionOwnerModel.created_at >= since)
            if until is not None:
                stmt = stmt.where(ChatSessionOwnerModel.created_at <= until)
            # keyword 模糊匹配:在 Python 层过滤(SQLite LIKE 大小写敏感不一致,统一 Python 层)
            rows = list(session.scalars(stmt))
            if kw_lower is not None:
                rows = [r for r in rows if kw_lower in (r.title or "").lower()]
            rows.sort(key=lambda r: r.created_at, reverse=True)
            return [_session_owner_from_orm(r) for r in rows[:limit]]

    async def cascade_delete_for_user(self, portal_user_id: str, settings) -> list:
        """级联删除用户的所有会话(含 deleted_at 非空),用于用户硬删除场景。

        与单会话双删策略(``dual_delete_session``)的关键区别:
          用户硬删除后无法后续重试(无用户上下文),所以 RAGFlow 失败的会话也
          **硬删除**门户侧记录(避免 portal_user_id 指向不存在用户的孤儿记录);
          RAGFlow 侧的 API4Conversation 残留由管理员后续手动清理(脚本/管理界面)。
          失败记 ``logger.warning`` 供审计(Slice 6 加审计日志端点暴露)。

        实现:
          - 遍历用户所有会话(``list_all_for_user``,含 deleted_at 非空,跨分享页)。
          - 对每个会话调 gateway ``delete_session_via_ragflow``(忽略失败,失败时 logger.warning)。
          - 全部硬删除门户侧记录(包括 deleted_at 非空的)。

        返回 ``[(session_id, success), ...]`` 供路由层记录审计/日志
        (success=True 表示 RAGFlow 删除成功,False 表示失败但门户侧仍硬删除)。
        """
        # 延迟导入避免循环依赖(gateway 不导入 models,但保持懒加载以防未来变更)
        from portal.gateway import delete_session_via_ragflow

        results: list = []
        for owner in self.list_all_for_user(portal_user_id):
            success = True
            try:
                await delete_session_via_ragflow(settings, owner.ragflow_resource_id, owner.session_id)
            except Exception as e:
                # RAGFlow 失败不阻塞级联删除(用户已不存在,无法后续重试,避免孤儿)
                # 记 warning 供审计;RAGFlow 侧残留由管理员后续手动清理
                logger.warning(
                    "用户硬删除级联:RAGFlow 删除会话失败(门户侧仍硬删除以避免孤儿),session_id=%s dialog_id=%s error=%s",
                    owner.session_id,
                    owner.ragflow_resource_id,
                    e,
                )
                success = False
            # 无论 RAGFlow 是否成功,都硬删除门户侧记录(避免孤儿)
            self.delete(owner.session_id)
            results.append((owner.session_id, success))
        return results


def _gen_id(prefix: str) -> str:
    """生成带前缀的随机 ID(降低碰撞,便于调试可读)。"""
    return f"{prefix}_{secrets.token_hex(8)}"


@dataclass
class AuditLog:
    """审计日志记录(对应 audit_log 表,Slice 6)。

    8 类敏感操作(PR D7b):login_success | login_failure | grant_create |
    grant_revoke | session_delete | session_view_elevated | user_enable | user_disable。
    永久保留,无 TTL/自动清理(PR D8b)。
    """

    id: str
    actor_user_id: str
    action: AuditAction
    target_type: AuditTargetType
    target_id: str
    at: float  # unix 时间戳(与 chat_session_owner.created_at 一致)
    meta_json: str  # JSON string,可选上下文(如 username / subject_type 等)


def _audit_log_from_orm(row: AuditLogModel) -> AuditLog:
    return AuditLog(
        id=row.id,
        actor_user_id=row.actor_user_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        at=row.at,
        meta_json=row.meta_json,
    )


class AuditStore:
    """审计日志存储 — Slice 6 内存,Slice 8 改为 SQLAlchemy 持久化(外部 API 不变)。

    永久保留,不自动清理(PR D8b);普通用户的日常操作不记审计(PR D7b 仅敏感操作)。
    """

    def __init__(self, session_maker: sessionmaker):
        self._sm = session_maker

    def record(
        self,
        actor_user_id: str,
        action: AuditAction,
        target_type: AuditTargetType,
        target_id: str,
        meta: dict | None = None,
    ) -> AuditLog:
        """记录一条审计日志(8 类敏感操作之一)。

        meta 为可选上下文 dict,序列化为 JSON 字符串存入 meta_json。
        返回新建的 AuditLog(已写入 DB)。
        """
        log = AuditLog(
            id=_gen_id("al"),
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            at=time.time(),
            meta_json=json.dumps(meta, ensure_ascii=False) if meta else "",
        )
        with self._sm() as session:
            row = AuditLogModel(
                id=log.id,
                actor_user_id=log.actor_user_id,
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                at=log.at,
                meta_json=log.meta_json,
            )
            session.add(row)
            session.commit()
        return log

    def list(
        self,
        actor_user_id: str | None = None,
        action: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ) -> list:
        """查询审计日志(按 actor/action/时间过滤,默认按时间倒序)。

        与 SessionStore.list_all 一致的过滤语义:None 表示不过滤该维度。
        默认 limit=100;返回最新的 limit 条(按 at 倒序)。
        """
        with self._sm() as session:
            stmt = select(AuditLogModel)
            if actor_user_id is not None:
                stmt = stmt.where(AuditLogModel.actor_user_id == actor_user_id)
            if action is not None:
                stmt = stmt.where(AuditLogModel.action == action)
            if since is not None:
                stmt = stmt.where(AuditLogModel.at >= since)
            if until is not None:
                stmt = stmt.where(AuditLogModel.at <= until)
            stmt = stmt.order_by(AuditLogModel.at.desc()).limit(limit)
            return [_audit_log_from_orm(r) for r in session.scalars(stmt)]


class SeedData:
    """可变存储 — Slice 4 把 Slice 1-3 的硬编码数据改为 CRUD 入口,
    Slice 8 改为 SQLAlchemy 持久化(外部 API 不变)。

    存储结构(DB 表):
      portal_user / portal_group / portal_group_member / share_page / share_page_grant

    所有写操作都通过 SQLAlchemy session 提交事务,保证一致性。
    """

    def __init__(self, session_maker: sessionmaker):
        self._sm = session_maker

    # -----------------------------------------------------------------
    # 用户 CRUD
    # -----------------------------------------------------------------

    def create_user(self, username: str, email: str, password_hash: str, *, is_admin: bool = False) -> PortalUser:
        """创建用户(管理员调用);username 必须唯一(DB 唯一约束保证)。

        返回新建的 PortalUser(已写入 DB)。
        调用方需在写入前检查 username 重复(本方法不抛异常,只追加;
        DB 唯一约束是兜底,但调用方先查可给出更友好的错误)。
        """
        user = PortalUser(
            id=_gen_id("u"),
            username=username,
            password_hash=password_hash,
            email=email,
            is_admin=is_admin,
            enabled=True,
            created_at=time.time(),
        )
        with self._sm() as session:
            row = PortalUserModel(
                id=user.id,
                username=user.username,
                password_hash=user.password_hash,
                email=user.email,
                is_admin=user.is_admin,
                enabled=user.enabled,
                created_at=user.created_at,
            )
            session.add(row)
            session.commit()
        return user

    def get_user_by_sso(self, sso_provider: str, sso_external_id: str):
        """按 SSO provider + external_id(sub) 查用户;不存在返回 None。

        Slice 14:SSO 回调用此方法匹配本地用户。自建账号用户的 sso_provider 为 NULL,
        不会被匹配(只有 SSO 创建的用户 sso_provider 非空)。
        """
        with self._sm() as session:
            stmt = select(PortalUserModel).where(
                PortalUserModel.sso_provider == sso_provider,
                PortalUserModel.sso_external_id == sso_external_id,
            )
            row = session.scalars(stmt).first()
            return _user_from_orm(row) if row is not None else None

    def create_sso_user(
        self,
        sso_provider: str,
        sso_external_id: str,
        username: str,
        email: str = "",
    ) -> PortalUser:
        """SSO 用户首次登录时自动创建本地用户记录(Slice 14)。

        默认 is_admin=false、enabled=true(权限与自建普通账号一致);
        password_hash 为空(SSO 用户不用密码登录,但字段 NOT NULL,存占位值)。
        username 从 IdP claims 取(email_preferred 或 sub),调用方需保证唯一。
        """
        user = PortalUser(
            id=_gen_id("u"),
            username=username,
            password_hash="",  # SSO 用户无密码(NOT NULL 占位)
            email=email,
            is_admin=False,
            enabled=True,
            created_at=time.time(),
            sso_provider=sso_provider,
            sso_external_id=sso_external_id,
        )
        with self._sm() as session:
            row = PortalUserModel(
                id=user.id,
                username=user.username,
                password_hash=user.password_hash,
                email=user.email,
                is_admin=user.is_admin,
                enabled=user.enabled,
                created_at=user.created_at,
                sso_provider=user.sso_provider,
                sso_external_id=user.sso_external_id,
            )
            session.add(row)
            session.commit()
        return user

    def get_user(self, user_id: str):
        """按 id 取用户;不存在返回 None。"""
        with self._sm() as session:
            row = session.get(PortalUserModel, user_id)
            return _user_from_orm(row) if row is not None else None

    def get_user_by_username(self, username: str):
        """按 username 取用户;不存在返回 None。"""
        with self._sm() as session:
            stmt = select(PortalUserModel).where(PortalUserModel.username == username)
            row = session.scalars(stmt).first()
            return _user_from_orm(row) if row is not None else None

    def list_users(self) -> list:
        """列出所有用户。"""
        with self._sm() as session:
            stmt = select(PortalUserModel)
            return [_user_from_orm(r) for r in session.scalars(stmt)]

    def set_user_enabled(self, user_id: str, enabled: bool) -> bool:
        """启用/禁用用户;返回 True 表示找到并更新。"""
        with self._sm() as session:
            row = session.get(PortalUserModel, user_id)
            if row is None:
                return False
            row.enabled = enabled
            session.commit()
            return True

    def delete_user(self, portal_user_id: str) -> bool:
        """硬删除用户(Slice 5):从 portal_user 删除 + 清理组成员关系。

        注意:此方法只删用户记录与组成员关系,不级联删 chat_session_owner
        (由路由层调 SessionStore.delete + delete_session_via_ragflow 完成级联双删)。
        返回 True 表示找到并删除;False 表示用户不存在。
        """
        with self._sm() as session:
            row = session.get(PortalUserModel, portal_user_id)
            if row is None:
                return False
            # 清理组成员关系(避免 dangling user_id)
            stmt = select(PortalGroupMemberModel).where(PortalGroupMemberModel.user_id == portal_user_id)
            for member_row in session.scalars(stmt):
                session.delete(member_row)
            session.delete(row)
            session.commit()
            return True

    # -----------------------------------------------------------------
    # 用户组 CRUD
    # -----------------------------------------------------------------

    def create_group(self, name: str) -> PortalGroup:
        """创建用户组(管理员调用)。"""
        group = PortalGroup(id=_gen_id("g"), name=name, created_at=time.time())
        with self._sm() as session:
            row = PortalGroupModel(id=group.id, name=group.name, created_at=group.created_at)
            session.add(row)
            session.commit()
        return group

    def get_group(self, group_id: str):
        """按 id 取用户组;不存在返回 None。"""
        with self._sm() as session:
            row = session.get(PortalGroupModel, group_id)
            return _group_from_orm(row) if row is not None else None

    def list_groups(self) -> list:
        """列出所有用户组。"""
        with self._sm() as session:
            stmt = select(PortalGroupModel)
            return [_group_from_orm(r) for r in session.scalars(stmt)]

    def add_group_member(self, group_id: str, user_id: str) -> bool:
        """添加用户到用户组;返回 True 表示成功。

        组或用户不存在 → 返回 False(调用方 → 404)。
        重复添加 → 幂等返回 True(复合主键去重,DB 层 INSERT OR IGNORE 语义由
        SQLite/MySQL 各自处理;此处先查再插,保证幂等)。
        """
        with self._sm() as session:
            if session.get(PortalGroupModel, group_id) is None:
                return False
            if session.get(PortalUserModel, user_id) is None:
                return False
            # 幂等:已存在则不重复插入
            existing = session.get(PortalGroupMemberModel, (group_id, user_id))
            if existing is not None:
                return True
            row = PortalGroupMemberModel(group_id=group_id, user_id=user_id, added_at=time.time())
            session.add(row)
            session.commit()
            return True

    def remove_group_member(self, group_id: str, user_id: str) -> bool:
        """从用户组移除用户;返回 True 表示找到并移除。"""
        with self._sm() as session:
            row = session.get(PortalGroupMemberModel, (group_id, user_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list_user_groups(self, user_id: str) -> list:
        """返回用户所属的所有 group_id(用于 ACL 解析)。"""
        with self._sm() as session:
            stmt = select(PortalGroupMemberModel.group_id).where(PortalGroupMemberModel.user_id == user_id)
            return list(session.scalars(stmt))

    def list_group_members(self, group_id: str) -> set:
        """返回用户组的成员 user_id 集合(封装 group_members 访问,消除 Feature Envy)。

        组不存在时返回空集合(调用方按需先 get_group 校验存在性)。
        返回集合的拷贝,避免外部修改内部状态。
        """
        with self._sm() as session:
            stmt = select(PortalGroupMemberModel.user_id).where(PortalGroupMemberModel.group_id == group_id)
            return set(session.scalars(stmt))

    # -----------------------------------------------------------------
    # 分享页 CRUD
    # -----------------------------------------------------------------

    def create_share_page(self, name: str, ragflow_resource_id: str) -> SharePage:
        """创建分享页(管理员调用);embed_type/ragflow_type 一期固定(D9)。"""
        page = SharePage(
            id=_gen_id("sp"),
            name=name,
            ragflow_type="chat",
            ragflow_resource_id=ragflow_resource_id,
            embed_type="fullscreen",
            enabled=True,
            created_at=time.time(),
        )
        with self._sm() as session:
            row = SharePageModel(
                id=page.id,
                name=page.name,
                ragflow_type=page.ragflow_type,
                ragflow_resource_id=page.ragflow_resource_id,
                embed_type=page.embed_type,
                enabled=page.enabled,
                created_at=page.created_at,
            )
            session.add(row)
            session.commit()
        return page

    def get_share_page(self, share_page_id: str):
        """按 id 取分享页;不存在返回 None。"""
        with self._sm() as session:
            row = session.get(SharePageModel, share_page_id)
            return _share_page_from_orm(row) if row is not None else None

    def list_share_pages(self) -> list:
        """列出所有分享页。"""
        with self._sm() as session:
            stmt = select(SharePageModel)
            return [_share_page_from_orm(r) for r in session.scalars(stmt)]

    def set_share_page_enabled(self, share_page_id: str, enabled: bool) -> bool:
        """启用/禁用分享页;返回 True 表示找到并更新。"""
        with self._sm() as session:
            row = session.get(SharePageModel, share_page_id)
            if row is None:
                return False
            row.enabled = enabled
            session.commit()
            return True

    def set_share_page_public(self, share_page_id: str, is_public: bool) -> bool:
        """Slice 15:设置分享页公开/私有;返回 True 表示找到并更新。

        公开分享页(is_public=true)允许免登录访问 + IP 限流;
        关闭后,已签发的公开 T_short 立即失效(网关每次校验 is_public 状态)。
        """
        with self._sm() as session:
            row = session.get(SharePageModel, share_page_id)
            if row is None:
                return False
            row.is_public = is_public
            session.commit()
            return True

    # -----------------------------------------------------------------
    # 授权 CRUD
    # -----------------------------------------------------------------

    def create_grant(
        self, share_page_id: str, subject_type: SubjectType, subject_id: str, permission: Permission = "use"
    ) -> SharePageGrant:
        """创建授权(管理员调用)。

        调用方需校验 share_page_id 与 subject_id 存在(本方法只追加,不校验)。
        幂等:若已存在 (share_page_id, subject_type, subject_id, permission) 完全相同的 grant,
        返回现有 grant 不重复创建(避免误创建两次导致撤销一次后残留 grant 仍使 has_use_grant=True
        的安全漏洞;撤销一次即彻底)。DB 唯一约束兜底。
        """
        with self._sm() as session:
            stmt = select(SharePageGrantModel).where(
                SharePageGrantModel.share_page_id == share_page_id,
                SharePageGrantModel.subject_type == subject_type,
                SharePageGrantModel.subject_id == subject_id,
                SharePageGrantModel.permission == permission,
            )
            existing = session.scalars(stmt).first()
            if existing is not None:
                return _grant_from_orm(existing)
            row = SharePageGrantModel(
                share_page_id=share_page_id,
                subject_type=subject_type,
                subject_id=subject_id,
                permission=permission,
            )
            session.add(row)
            session.commit()
            return _grant_from_orm(row)

    def list_grants(self, share_page_id: str) -> list:
        """列出某分享页的所有 grant。"""
        with self._sm() as session:
            stmt = select(SharePageGrantModel).where(SharePageGrantModel.share_page_id == share_page_id)
            return [_grant_from_orm(r) for r in session.scalars(stmt)]

    def revoke_grant(self, share_page_id: str, subject_type: SubjectType, subject_id: str) -> bool:
        """撤销授权:删除匹配 (share_page_id, subject_type, subject_id) 的首条 grant。

        Slice 4 升级:支持 user 与 group 两种 subject_type(Slice 3 只支持 user)。
        返回 True 表示找到并删除;False 表示 grant 不存在(调用方 → 404)。
        不删除 chat_session_owner 记录(历史会话保留,管理员可查)。
        """
        with self._sm() as session:
            stmt = select(SharePageGrantModel).where(
                SharePageGrantModel.share_page_id == share_page_id,
                SharePageGrantModel.subject_type == subject_type,
                SharePageGrantModel.subject_id == subject_id,
            )
            row = session.scalars(stmt).first()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # -----------------------------------------------------------------
    # ACL 解析(网关校验链步骤 2)
    # -----------------------------------------------------------------

    def list_user_granted_share_page_ids(self, portal_user_id: str) -> set:
        """返回用户被授权(use 权限)的分享页 id 集合(直接授权 + 组继承)。

        统一 ACL 解析入口,消除 has_use_grant 与 list_share_pages_for_user 重复的
        user/group grant 过滤逻辑。subject_type 分支在此处唯一实现:
          - subject_type='user' and subject_id == portal_user_id(直接授权)
          - subject_type='group' and subject_id in 用户所属组列表(组继承)
        用户不存在 → 空集合。
        """
        with self._sm() as session:
            # 用户存在性检查
            if session.get(PortalUserModel, portal_user_id) is None:
                return set()
            user_group_ids = set(self.list_user_groups(portal_user_id))
            stmt = select(SharePageGrantModel).where(SharePageGrantModel.permission == "use")
            authorized: set = set()
            for g in session.scalars(stmt):
                if g.subject_type == "user" and g.subject_id == portal_user_id:
                    authorized.add(g.share_page_id)
                elif g.subject_type == "group" and g.subject_id in user_group_ids:
                    authorized.add(g.share_page_id)
            return authorized

    def has_use_grant(self, share_page_id: str, portal_user_id: str) -> bool:
        """校验用户对分享页是否有 use 权限(校验链步骤 2,Slice 4 升级)。

        委托 list_user_granted_share_page_ids 统一解析 user 直接授权 + 组继承。
        撤销授权后此方法返回 False(网关每次请求都调用,实现「撤销立即失效」);
        create_grant 幂等保证撤销一次即彻底(无残留重复 grant)。
        """
        return share_page_id in self.list_user_granted_share_page_ids(portal_user_id)

    def list_share_pages_for_user(self, portal_user_id: str) -> list:
        """列出用户被授权的分享页(直接 + 组继承,且 enabled=True)。

        普通用户 GET /share-pages 用此方法过滤。委托
        list_user_granted_share_page_ids 统一 ACL 解析(消除重复的 grant 过滤逻辑)。
        """
        authorized_page_ids = self.list_user_granted_share_page_ids(portal_user_id)
        with self._sm() as session:
            result: list = []
            for pid in authorized_page_ids:
                row = session.get(SharePageModel, pid)
                if row is not None and row.enabled:
                    result.append(_share_page_from_orm(row))
            return result


def build_seed_data(settings: Settings, session_maker: sessionmaker) -> SeedData:
    """构造初始数据:admin + user2 + 默认分享页 + grant(Slice 1-3 兼容,Slice 8 DB idempotent)。

    Slice 4 保留 seed admin 与 user2 以避免 Slice 1-3 测试回归;
    CRUD 是增量,可在运行时通过 /admin/* 端点继续创建。
    Slice 8:启动时从 DB 读取,不存在则初始化写入(idempotent,可重复执行)。
    """
    seed = SeedData(session_maker)
    # admin(平台管理员,D5)— idempotent:已存在则跳过
    if seed.get_user("u_admin") is None:
        admin = PortalUser(
            id="u_admin",
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            email="admin@example.com",
            is_admin=True,
            enabled=True,
            created_at=time.time(),
        )
        with session_maker() as session:
            session.add(
                PortalUserModel(
                    id=admin.id,
                    username=admin.username,
                    password_hash=admin.password_hash,
                    email=admin.email,
                    is_admin=admin.is_admin,
                    enabled=admin.enabled,
                    created_at=admin.created_at,
                )
            )
            session.commit()
    # user2(普通用户,Slice 3 隔离测试用)
    if seed.get_user("u_user2") is None:
        user2 = PortalUser(
            id="u_user2",
            username=settings.user2_username,
            password_hash=hash_password(settings.user2_password),
            email="user2@example.com",
            is_admin=False,
            enabled=True,
            created_at=time.time(),
        )
        with session_maker() as session:
            session.add(
                PortalUserModel(
                    id=user2.id,
                    username=user2.username,
                    password_hash=user2.password_hash,
                    email=user2.email,
                    is_admin=user2.is_admin,
                    enabled=user2.enabled,
                    created_at=user2.created_at,
                )
            )
            session.commit()
    # Slice 15:匿名用户(公开分享页会话归属)— idempotent:已存在则跳过
    # 用途:公开分享页(is_public=true)预创建 session 时,portal_user_id 绑定到 u_anonymous,
    # 使公开会话不归属任何具体登录用户,且可被管理员通过 user_id=u_anonymous 过滤查询。
    if seed.get_user("u_anonymous") is None:
        with session_maker() as session:
            session.add(
                PortalUserModel(
                    id="u_anonymous",
                    username="anonymous",
                    password_hash="",  # 匿名用户无密码,不可登录
                    email="anonymous@portal.local",
                    is_admin=False,
                    enabled=True,
                    created_at=time.time(),
                )
            )
            session.commit()
    # 默认分享页(Slice 1 硬编码,关联配置的 RAGFLOW_DIALOG_ID)
    if seed.get_share_page("sp_default") is None:
        share_page = SharePage(
            id="sp_default",
            name="默认分享页",
            ragflow_type="chat",
            ragflow_resource_id=settings.ragflow_dialog_id,
            embed_type="fullscreen",
            enabled=True,
            created_at=time.time(),
        )
        with session_maker() as session:
            session.add(
                SharePageModel(
                    id=share_page.id,
                    name=share_page.name,
                    ragflow_type=share_page.ragflow_type,
                    ragflow_resource_id=share_page.ragflow_resource_id,
                    embed_type=share_page.embed_type,
                    enabled=share_page.enabled,
                    created_at=share_page.created_at,
                )
            )
            session.commit()
    # admin 与 user2 对默认分享页的 use grant(Slice 3 隔离测试需要 user2 也能访问)
    # create_grant 幂等:已存在则跳过(DB 唯一约束兜底)
    seed.create_grant("sp_default", "user", "u_admin", "use")
    seed.create_grant("sp_default", "user", "u_user2", "use")
    return seed
