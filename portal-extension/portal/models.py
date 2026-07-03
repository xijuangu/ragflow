"""硬编码数据模型 — Slice 1 最小子集。

数据模型(对应 ISSUES.md Issue 1):
  portal_user(id, username, password_hash, is_admin=true, enabled=true)
  share_page(id, name, ragflow_type='chat', ragflow_resource_id=<dialog_id>,
             embed_type='fullscreen', enabled=true)
  share_page_grant(share_page_id, subject_type='user', subject_id, permission='use')

Slice 4 才做 CRUD,本 slice 用内存硬编码数据。
"""

from dataclasses import dataclass
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
class SeedData:
    """启动时构建的硬编码数据(Slice 1 无 DB)。"""

    users_by_username: dict
    users_by_id: dict
    share_pages_by_id: dict
    grants: list


def build_seed_data(settings: Settings) -> SeedData:
    """根据配置构造硬编码 admin 用户、分享页与授权。"""
    admin = PortalUser(
        id="u_admin",
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        is_admin=True,
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
    grant = SharePageGrant(
        share_page_id=share_page.id,
        subject_type="user",
        subject_id=admin.id,
        permission="use",
    )
    return SeedData(
        users_by_username={admin.username: admin},
        users_by_id={admin.id: admin},
        share_pages_by_id={share_page.id: share_page},
        grants=[grant],
    )
