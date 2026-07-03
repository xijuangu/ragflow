"""数据模型 — Slice 1 + Slice 2 + Slice 3 + Slice 4。

数据模型(对应 ISSUES.md Issue 1 + Issue 2 + Issue 3 + Issue 4):
  portal_user(id, username, password_hash, email, is_admin, enabled, created_at)
  portal_group(id, name, created_at)
  portal_group_member(group_id, user_id, added_at)  — 本 slice 用 set 简化
  share_page(id, name, ragflow_type='chat', ragflow_resource_id,
             embed_type='fullscreen', enabled)
  share_page_grant(share_page_id, subject_type, subject_id, permission='use')
  chat_session_owner(session_id, share_page_id, portal_user_id NOT NULL,
                     ragflow_resource_id, title, created_at, last_active_at)

Slice 4 把 Slice 1-3 的硬编码 SeedData 改为可变内存存储,新增用户组与完整 CRUD 方法;
has_use_grant 升级支持 user + group 两种 subject_type(用户组继承)。
仍用内存存储(线程安全由 GIL + 单进程 FastAPI 保证),DB 化作为独立 slice。
"""

import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from portal.config import Settings
from portal.password import hash_password

# 类型别名约束字面量取值(消除 Primitive Obsession)
Permission = Literal["use", "manage"]
RagflowType = Literal["chat", "agent"]
EmbedType = Literal["fullscreen", "widget"]
SubjectType = Literal["user", "group"]


@dataclass
class PortalUser:
    """门户用户(对应 portal_user 表)。

    Slice 4 补齐 email / created_at 字段(D5 数据模型)。
    """

    id: str
    username: str
    password_hash: str
    email: str = ""
    is_admin: bool = False
    enabled: bool = True
    created_at: float = field(default_factory=time.time)


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
    """

    id: str
    name: str
    ragflow_type: RagflowType = "chat"
    ragflow_resource_id: str = ""
    embed_type: EmbedType = "fullscreen"
    enabled: bool = True
    created_at: float = field(default_factory=time.time)


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
    """

    session_id: str
    share_page_id: str
    portal_user_id: str  # NOT NULL — 归属绑定到具体门户用户
    ragflow_resource_id: str  # 对应 share_page.ragflow_resource_id(dialog_id)
    title: str = ""
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)


class SessionStore:
    """内存会话归属表 — Slice 2 不持久化,Slice 4 仍内存(可换 DB)。

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


def _gen_id(prefix: str) -> str:
    """生成带前缀的随机 ID(降低碰撞,便于调试可读)。"""
    return f"{prefix}_{secrets.token_hex(8)}"


class SeedData:
    """可变内存存储 — Slice 4 把 Slice 1-3 的硬编码数据改为 CRUD 入口。

    存储结构:
      users_by_id / users_by_username — 用户索引(双向)
      groups_by_id                    — 用户组(成员用 group_id -> set[user_id] 索引)
      share_pages_by_id               — 分享页索引
      grants                          — 授权列表(无唯一索引,撤销按三元组匹配)

    所有写操作都更新对应索引,保证 list / get 一致性。
    """

    users_by_username: dict
    users_by_id: dict
    groups_by_id: dict
    group_members: dict  # group_id -> set[user_id]
    share_pages_by_id: dict
    grants: list

    def __init__(
        self,
        users_by_username: dict,
        users_by_id: dict,
        share_pages_by_id: dict,
        grants: list,
        groups_by_id: dict | None = None,
        group_members: dict | None = None,
    ):
        self.users_by_username = users_by_username
        self.users_by_id = users_by_id
        self.share_pages_by_id = share_pages_by_id
        self.grants = grants
        self.groups_by_id = groups_by_id or {}
        self.group_members = group_members or {}

    # -----------------------------------------------------------------
    # 用户 CRUD
    # -----------------------------------------------------------------

    def create_user(self, username: str, email: str, password_hash: str, *, is_admin: bool = False) -> PortalUser:
        """创建用户(管理员调用);username 必须唯一。

        返回新建的 PortalUser(已写入索引)。
        调用方需在写入前检查 username 重复(本方法不抛异常,只追加)。
        """
        user = PortalUser(
            id=_gen_id("u"),
            username=username,
            password_hash=password_hash,
            email=email,
            is_admin=is_admin,
            enabled=True,
        )
        self.users_by_id[user.id] = user
        self.users_by_username[user.username] = user
        return user

    def get_user(self, user_id: str):
        """按 id 取用户;不存在返回 None。"""
        return self.users_by_id.get(user_id)

    def get_user_by_username(self, username: str):
        """按 username 取用户;不存在返回 None。"""
        return self.users_by_username.get(username)

    def list_users(self) -> list:
        """列出所有用户。"""
        return list(self.users_by_id.values())

    def set_user_enabled(self, user_id: str, enabled: bool) -> bool:
        """启用/禁用用户;返回 True 表示找到并更新。"""
        user = self.users_by_id.get(user_id)
        if user is None:
            return False
        user.enabled = enabled
        return True

    # -----------------------------------------------------------------
    # 用户组 CRUD
    # -----------------------------------------------------------------

    def create_group(self, name: str) -> PortalGroup:
        """创建用户组(管理员调用)。"""
        group = PortalGroup(id=_gen_id("g"), name=name)
        self.groups_by_id[group.id] = group
        self.group_members.setdefault(group.id, set())
        return group

    def get_group(self, group_id: str):
        """按 id 取用户组;不存在返回 None。"""
        return self.groups_by_id.get(group_id)

    def list_groups(self) -> list:
        """列出所有用户组。"""
        return list(self.groups_by_id.values())

    def add_group_member(self, group_id: str, user_id: str) -> bool:
        """添加用户到用户组;返回 True 表示成功。

        组或用户不存在 → 返回 False(调用方 → 404)。
        重复添加 → 幂等返回 True(成员集合去重)。
        """
        if group_id not in self.groups_by_id:
            return False
        if user_id not in self.users_by_id:
            return False
        self.group_members.setdefault(group_id, set()).add(user_id)
        return True

    def remove_group_member(self, group_id: str, user_id: str) -> bool:
        """从用户组移除用户;返回 True 表示找到并移除。"""
        members = self.group_members.get(group_id)
        if not members or user_id not in members:
            return False
        members.discard(user_id)
        return True

    def list_user_groups(self, user_id: str) -> list:
        """返回用户所属的所有 group_id(用于 ACL 解析)。"""
        return [gid for gid, members in self.group_members.items() if user_id in members]

    def list_group_members(self, group_id: str) -> set:
        """返回用户组的成员 user_id 集合(封装 group_members 访问,消除 Feature Envy)。

        组不存在时返回空集合(调用方按需先 get_group 校验存在性)。
        返回集合的拷贝,避免外部修改内部状态。
        """
        return set(self.group_members.get(group_id, set()))

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
        )
        self.share_pages_by_id[page.id] = page
        return page

    def get_share_page(self, share_page_id: str):
        """按 id 取分享页;不存在返回 None。"""
        return self.share_pages_by_id.get(share_page_id)

    def list_share_pages(self) -> list:
        """列出所有分享页。"""
        return list(self.share_pages_by_id.values())

    def set_share_page_enabled(self, share_page_id: str, enabled: bool) -> bool:
        """启用/禁用分享页;返回 True 表示找到并更新。"""
        page = self.share_pages_by_id.get(share_page_id)
        if page is None:
            return False
        page.enabled = enabled
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
        的安全漏洞;撤销一次即彻底)。
        """
        for g in self.grants:
            if (
                g.share_page_id == share_page_id
                and g.subject_type == subject_type
                and g.subject_id == subject_id
                and g.permission == permission
            ):
                return g
        grant = SharePageGrant(
            share_page_id=share_page_id,
            subject_type=subject_type,
            subject_id=subject_id,
            permission=permission,
        )
        self.grants.append(grant)
        return grant

    def list_grants(self, share_page_id: str) -> list:
        """列出某分享页的所有 grant。"""
        return [g for g in self.grants if g.share_page_id == share_page_id]

    def revoke_grant(self, share_page_id: str, subject_type: SubjectType, subject_id: str) -> bool:
        """撤销授权:删除匹配 (share_page_id, subject_type, subject_id) 的首条 grant。

        Slice 4 升级:支持 user 与 group 两种 subject_type(Slice 3 只支持 user)。
        返回 True 表示找到并删除;False 表示 grant 不存在(调用方 → 404)。
        不删除 chat_session_owner 记录(历史会话保留,管理员可查)。
        """
        for i, g in enumerate(self.grants):
            if g.share_page_id == share_page_id and g.subject_type == subject_type and g.subject_id == subject_id:
                self.grants.pop(i)
                return True
        return False

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
        if portal_user_id not in self.users_by_id:
            return set()
        user_group_ids = self.list_user_groups(portal_user_id)
        authorized_page_ids: set = set()
        for g in self.grants:
            if g.permission != "use":
                continue
            if g.subject_type == "user" and g.subject_id == portal_user_id:
                authorized_page_ids.add(g.share_page_id)
            elif g.subject_type == "group" and g.subject_id in user_group_ids:
                authorized_page_ids.add(g.share_page_id)
        return authorized_page_ids

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
        return [
            self.share_pages_by_id[pid]
            for pid in authorized_page_ids
            if pid in self.share_pages_by_id and self.share_pages_by_id[pid].enabled
        ]


def build_seed_data(settings: Settings) -> SeedData:
    """构造初始数据:admin + user2 + 默认分享页 + grant(Slice 1-3 兼容)。

    Slice 4 保留 seed admin 与 user2 以避免 Slice 1-3 测试回归;
    CRUD 是增量,可在运行时通过 /admin/* 端点继续创建。
    """
    seed = SeedData(
        users_by_username={},
        users_by_id={},
        share_pages_by_id={},
        grants=[],
    )
    # admin(平台管理员,D5)
    admin = PortalUser(
        id="u_admin",
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        email="admin@example.com",
        is_admin=True,
        enabled=True,
    )
    seed.users_by_id[admin.id] = admin
    seed.users_by_username[admin.username] = admin
    # user2(普通用户,Slice 3 隔离测试用)
    user2 = PortalUser(
        id="u_user2",
        username=settings.user2_username,
        password_hash=hash_password(settings.user2_password),
        email="user2@example.com",
        is_admin=False,
        enabled=True,
    )
    seed.users_by_id[user2.id] = user2
    seed.users_by_username[user2.username] = user2
    # 默认分享页(Slice 1 硬编码,关联配置的 RAGFLOW_DIALOG_ID)
    share_page = SharePage(
        id="sp_default",
        name="默认分享页",
        ragflow_type="chat",
        ragflow_resource_id=settings.ragflow_dialog_id,
        embed_type="fullscreen",
        enabled=True,
    )
    seed.share_pages_by_id[share_page.id] = share_page
    # admin 与 user2 对默认分享页的 use grant(Slice 3 隔离测试需要 user2 也能访问)
    seed.grants.append(
        SharePageGrant(
            share_page_id=share_page.id,
            subject_type="user",
            subject_id=admin.id,
            permission="use",
        )
    )
    seed.grants.append(
        SharePageGrant(
            share_page_id=share_page.id,
            subject_type="user",
            subject_id=user2.id,
            permission="use",
        )
    )
    return seed
