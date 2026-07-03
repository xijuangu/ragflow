"""硬编码数据模型 — Slice 1 + Slice 2 + Slice 3。

数据模型(对应 ISSUES.md Issue 1 + Issue 2 + Issue 3):
  portal_user(id, username, password_hash, is_admin=true, enabled=true)
  share_page(id, name, ragflow_type='chat', ragflow_resource_id=<dialog_id>,
             embed_type='fullscreen', enabled=true)
  share_page_grant(share_page_id, subject_type='user', subject_id, permission='use')
  chat_session_owner(session_id, share_page_id, portal_user_id NOT NULL,
                     ragflow_resource_id, title, created_at, last_active_at)

Slice 3 新增:第二个硬编码普通用户 user2(用于隔离测试)、SeedData 的
has_use_grant / revoke_grant 方法(支持完整校验链步骤 2 与撤销授权)。
Slice 4 才做 CRUD,本 slice 用内存硬编码数据。
Slice 4 才上 DB,Slice 2 的 chat_session_owner 用内存 SessionStore。
"""

import time
from dataclasses import dataclass, field
from typing import Literal

from portal.config import Settings
from portal.password import hash_password

# 类型别名约束字面量取值(消除 Primitive Obsession,Slice 4 CRUD 会扩展可选值)
Permission = Literal["use", "manage"]
RagflowType = Literal["chat", "agent"]
EmbedType = Literal["fullscreen", "widget"]
SubjectType = Literal["user", "group"]


@dataclass
class PortalUser:
    id: str
    username: str
    password_hash: str
    is_admin: bool = True
    enabled: bool = True


@dataclass
class SharePage:
    id: str
    name: str
    ragflow_type: RagflowType = "chat"
    ragflow_resource_id: str = ""
    embed_type: EmbedType = "fullscreen"
    enabled: bool = True


@dataclass
class SharePageGrant:
    share_page_id: str
    subject_type: SubjectType = "user"
    subject_id: str = ""
    permission: Permission = "use"


@dataclass
class ChatSessionOwner:
    """会话归属记录(对应 ISSUES.md Issue 2 的 chat_session_owner 表)。

    session_id 为主键(对应 RAGFlow API4Conversation.id);
    portal_user_id NOT NULL(预创建时即绑定到当前用户)。
    """

    session_id: str
    share_page_id: str
    portal_user_id: str  # NOT NULL — 归属绑定到具体门户用户
    ragflow_resource_id: str  # 对应 share_page.ragflow_resource_id(dialog_id)
    title: str = ""
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)


class SessionStore:
    """内存会话归属表 — Slice 2 不持久化,Slice 4 可换 DB。

    类似 Slice 1 的 TokenStore,但记录的是「session_id → 门户用户」的归属关系。
    """

    def __init__(self):
        self._sessions: dict = {}  # session_id -> ChatSessionOwner

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
        self._sessions[session_id] = owner
        return owner

    def get(self, session_id: str):
        """按 session_id 取回归属记录;不存在返回 None。"""
        return self._sessions.get(session_id)

    def list_for_user(self, portal_user_id: str, share_page_id: str) -> list:
        """按用户 + 分享页查询会话列表(对应验收点 4:我的会话)。

        基础隔离:只返回 portal_user_id 匹配的记录(用户看不到他人的 session)。
        """
        return [
            s
            for s in self._sessions.values()
            if s.portal_user_id == portal_user_id and s.share_page_id == share_page_id
        ]

    def update_last_active(self, session_id: str) -> bool:
        """更新会话最后活跃时间(对应验收点 3:对话后 last_active_at 更新)。

        返回 True 表示 session 存在并已更新;False 表示 session 不存在。
        """
        owner = self._sessions.get(session_id)
        if owner is None:
            return False
        owner.last_active_at = time.time()
        return True


@dataclass
class SeedData:
    """启动时构建的硬编码数据(Slice 1 无 DB)。"""

    users_by_username: dict
    users_by_id: dict
    share_pages_by_id: dict
    grants: list

    def has_use_grant(self, share_page_id: str, subject_id: str) -> bool:
        """校验指定 subject 对分享页是否有 use 权限(校验链步骤 2)。

        Slice 3 只支持 subject_type='user';Slice 4 才启用 group subject_type。
        撤销授权后此方法返回 False(网关每次请求都调用,实现「撤销立即失效」)。
        """
        return any(
            g.share_page_id == share_page_id and g.subject_id == subject_id and g.permission == "use"
            for g in self.grants
        )

    def revoke_grant(self, share_page_id: str, subject_id: str) -> bool:
        """撤销授权:删除指定 grant 行(对应 ISSUES.md Issue 3 撤销机制)。

        返回 True 表示找到并删除;False 表示 grant 不存在(调用方 → 404)。
        注意:不删除 chat_session_owner 记录(历史会话保留,管理员可查)。
        """
        for i, g in enumerate(self.grants):
            if g.share_page_id == share_page_id and g.subject_id == subject_id:
                self.grants.pop(i)
                return True
        return False


def build_seed_data(settings: Settings) -> SeedData:
    """根据配置构造硬编码 admin + user2 用户、分享页与授权。

    Slice 3 新增第二个硬编码普通用户 user2(is_admin=False),
    用于用户间隔离测试。user2 对默认分享页有 use 权限。
    """
    admin = PortalUser(
        id="u_admin",
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        is_admin=True,
        enabled=True,
    )
    user2 = PortalUser(
        id="u_user2",
        username=settings.user2_username,
        password_hash=hash_password(settings.user2_password),
        is_admin=False,
        enabled=True,
    )
    share_page = SharePage(
        id="sp_default",
        name="默认分享页",
        ragflow_type="chat",
        ragflow_resource_id=settings.ragflow_dialog_id,
        embed_type="fullscreen",
        enabled=True,
    )
    admin_grant = SharePageGrant(
        share_page_id=share_page.id,
        subject_type="user",
        subject_id=admin.id,
        permission="use",
    )
    user2_grant = SharePageGrant(
        share_page_id=share_page.id,
        subject_type="user",
        subject_id=user2.id,
        permission="use",
    )
    return SeedData(
        users_by_username={admin.username: admin, user2.username: user2},
        users_by_id={admin.id: admin, user2.id: user2},
        share_pages_by_id={share_page.id: share_page},
        grants=[admin_grant, user2_grant],
    )
