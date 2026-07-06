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
  RETRY_DELETE_INTERVAL_SECONDS  Slice 12 双删重试定时任务间隔(默认 300 = 5 分钟);
                                 设为 0 或负数禁用定时任务(管理员仍可手动触发 retry-delete)
  OIDC_ENABLED            Slice 14 是否启用 OIDC SSO 登录(默认 false)
  OIDC_ISSUER             OIDC IdP 的 issuer URL(如 https://keycloak.example/realms/main)
  OIDC_CLIENT_ID          OIDC client_id(在 IdP 注册门户时分配)
  OIDC_CLIENT_SECRET      OIDC client_secret(敏感,只走环境变量)
  OIDC_REDIRECT_URI       OIDC 回调地址(如 https://portal.example/sso/callback)
  SSO_AUTO_CREATE         SSO 用户首次登录是否自动创建本地用户(默认 true)
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
    # Slice 12:双删重试定时任务间隔(秒);<=0 禁用定时任务(管理员仍可手动触发)
    retry_delete_interval_seconds: int = 300
    # Slice 14:OIDC SSO 配置(默认禁用,OIDC_ENABLED=false 时 SSO 端点返回 404)
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    sso_auto_create: bool = True
    # Slice 16:widget 跨域嵌入允许的 frame-ancestors 来源(默认 * 允许任意域;
    # 生产建议配置为具体域名列表,如 https://example.com https://app.example.com)。
    # 仅对 /widget/* 路径生效,其他路径保持 X-Frame-Options: SAMEORIGIN(D10)。
    widget_frame_ancestors: str = "*"


def _env_bool(name: str, default: bool) -> bool:
    """环境变量布尔解析:true/1/yes/on(大小写不敏感)为真,其余为假。"""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


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
        # Slice 12:默认 300 秒(5 分钟);<=0 禁用定时任务(管理员仍可手动触发 retry-delete)
        retry_delete_interval_seconds=int(os.environ.get("RETRY_DELETE_INTERVAL_SECONDS", "300")),
        # Slice 14:OIDC SSO 配置(默认禁用,管理员配置环境变量后启用)
        oidc_enabled=_env_bool("OIDC_ENABLED", False),
        oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
        sso_auto_create=_env_bool("SSO_AUTO_CREATE", True),
        # Slice 16:widget 跨域嵌入 frame-ancestors(默认 * 允许任意域,生产建议配置具体域名)
        widget_frame_ancestors=os.environ.get("WIDGET_FRAME_ANCESTORS", "*"),
    )
