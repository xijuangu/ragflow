"""配置模块 — 所有敏感值从环境变量读取,不硬编码到代码。

环境变量:
  PORTAL_ADMIN_USERNAME   硬编码 admin 用户名(默认 admin)
  PORTAL_ADMIN_PASSWORD   admin 明文密码(启动时哈希;真实密码只走环境变量,不写入文件)
  PORTAL_USER2_USERNAME   硬编码第二个普通用户名(Slice 3 隔离测试用,默认 user2)
  PORTAL_USER2_PASSWORD   user2 明文密码(启动时哈希)
  PORTAL_SESSION_SECRET   会话 cookie 签名密钥
  RAGFLOW_HOST            RAGFlow web 地址(如 http://172.16.10.180)
  RAGFLOW_BETA_TOKEN      RAGFlow api_token.beta 列的值(网关持有,绝不返回浏览器)
  RAGFLOW_DIALOG_ID       硬编码分享页关联的 RAGFlow Chat dialog_id
  T_SHORT_TTL_SECONDS     短期嵌入令牌有效期(默认 300 = 5 分钟)
  PORTAL_DB_URL           Slice 8 DB 连接 URL(默认 sqlite:// 即 in-memory;
                          生产用 mysql+pymysql://user:pass@host:3306/portal)
"""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """运行时配置(由 load_settings() 从环境变量构造)。"""

    admin_username: str
    admin_password: str
    user2_username: str
    user2_password: str
    session_secret: str
    ragflow_host: str
    ragflow_beta_token: str
    ragflow_dialog_id: str
    t_short_ttl_seconds: int
    portal_db_url: str  # Slice 8:DB 连接 URL(SQLite/MySQL 由 URL scheme 决定)


def load_settings() -> Settings:
    """从环境变量读取配置。每次调用都读取,便于测试覆盖。"""
    return Settings(
        admin_username=os.environ.get("PORTAL_ADMIN_USERNAME", "admin"),
        admin_password=os.environ.get("PORTAL_ADMIN_PASSWORD", ""),
        user2_username=os.environ.get("PORTAL_USER2_USERNAME", "user2"),
        user2_password=os.environ.get("PORTAL_USER2_PASSWORD", ""),
        session_secret=os.environ.get("PORTAL_SESSION_SECRET", "dev-insecure-secret-change-me"),
        ragflow_host=os.environ.get("RAGFLOW_HOST", "http://localhost:9380"),
        ragflow_beta_token=os.environ.get("RAGFLOW_BETA_TOKEN", ""),
        ragflow_dialog_id=os.environ.get("RAGFLOW_DIALOG_ID", ""),
        t_short_ttl_seconds=int(os.environ.get("T_SHORT_TTL_SECONDS", "300")),
        # Slice 8:默认 sqlite://(in-memory),生产用 mysql+pymysql://...
        portal_db_url=os.environ.get("PORTAL_DB_URL", "sqlite://"),
    )
