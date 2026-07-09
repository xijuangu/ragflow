# 数据模型

Portal 使用 SQLAlchemy 2.x ORM。`PORTAL_DB_URL` 决定后端数据库，测试默认 SQLite in-memory，生产可用文件型 SQLite 或 MySQL。

`init_db(engine)` 会 `create_all`，并对后续新增列执行幂等迁移。当前没有 Alembic。

## 表

### `portal_user`

门户用户。

| 字段 | 说明 |
|---|---|
| `id` | 主键。 |
| `username` | 登录名，唯一。 |
| `password_hash` | 密码哈希。 |
| `email` | 邮箱。 |
| `is_admin` | 平台管理员，可跨 org。 |
| `enabled` | 是否启用；禁用后不能登录，历史会话保留。 |
| `created_at` | 创建时间戳。 |
| `sso_provider` / `sso_external_id` | OIDC 用户绑定。 |
| `org_id` | 所属 org。 |
| `org_admin` | org 管理员，只能管理本 org。 |

### `portal_group`

用户组。

| 字段 | 说明 |
|---|---|
| `id` | 主键。 |
| `name` | 组名。 |
| `created_at` | 创建时间戳。 |
| `org_id` | 所属 org。 |

### `portal_group_member`

用户组成员关系。

| 字段 | 说明 |
|---|---|
| `group_id` | 组 ID，复合主键。 |
| `user_id` | 用户 ID，复合主键。 |
| `added_at` | 加入时间戳。 |
| `org_id` | 所属 org。 |

### `share_page`

门户分享页，对应 RAGFlow 的 chat dialog 或 agent。

| 字段 | 说明 |
|---|---|
| `id` | 主键。 |
| `name` | 门户展示名。 |
| `ragflow_type` | `chat` 或 `agent`。 |
| `ragflow_resource_id` | RAGFlow dialog id 或 agent id。 |
| `embed_type` | `fullscreen` 或 `widget`。 |
| `enabled` | 是否启用。 |
| `created_at` | 创建时间戳。 |
| `org_id` | 所属 org。 |
| `is_public` | 是否允许公开访问。 |

### `share_page_grant`

分享页授权。

| 字段 | 说明 |
|---|---|
| `id` | 自增主键。 |
| `share_page_id` | 分享页 ID。 |
| `subject_type` | `user` 或 `group`。 |
| `subject_id` | 用户 ID 或组 ID。 |
| `permission` | 当前主要使用 `use`，预留 `manage`。 |
| `org_id` | 所属 org。 |

唯一约束：`share_page_id + subject_type + subject_id + permission`。

### `chat_session_owner`

门户会话归属映射。

| 字段 | 说明 |
|---|---|
| `session_id` | 主键，对应 RAGFlow `API4Conversation.id`。 |
| `share_page_id` | 门户分享页 ID。 |
| `portal_user_id` | 归属用户 ID。公开分享可使用公开用户语义。 |
| `ragflow_resource_id` | RAGFlow dialog id 或 agent id。 |
| `title` | 门户侧显示标题。 |
| `created_at` | 创建时间戳。 |
| `last_active_at` | 最近活跃时间戳。 |
| `deleted_at` | 双删失败后的待重试标记；空表示正常。 |
| `message_count` | 消息数缓存。 |
| `org_id` | 所属 org。 |

消息正文不存这里。正文、引用、文档预览从 RAGFlow sessions 端点读取。

### `audit_log`

审计日志，长期保留。

| 字段 | 说明 |
|---|---|
| `id` | 主键。 |
| `actor_user_id` | 操作者。 |
| `action` | 操作类型。 |
| `target_type` | 目标类型。 |
| `target_id` | 目标 ID。 |
| `at` | 时间戳。 |
| `meta_json` | JSON 字符串。 |
| `org_id` | 所属 org。 |

当前主要 action：

```text
login_success
login_failure
grant_create
grant_revoke
session_delete
session_view_elevated
user_enable
user_disable
public_chat
```

## 持久化策略

- DB 表是业务事实源，必须持久化。
- `TokenStore` 是内存态，进程重启后所有 `pt_` 令牌失效，用户重新登录或刷新分享页即可。
- `IPRateLimiter` 是内存态，进程重启后限流窗口清空。
- RAGFlow 消息正文不复制到 Portal DB，避免双事实源。

## 当前实现检查结论

代码入口在 `portal/main.py`：

1. `load_settings()` 读取 `PORTAL_DB_URL`。
2. `_create_engine_from_url()` 根据 URL 创建 SQLAlchemy engine。
   - `sqlite://` 或 `sqlite:///:memory:` 使用 `StaticPool`，测试时共享内存库连接。
   - 其他 SQLite URL 使用文件型 SQLite。
   - MySQL 等其他方言使用默认连接池并开启 `pool_pre_ping=True`。
3. `init_db(engine)` 创建表，并执行幂等补列迁移。
4. `create_session_maker(engine)` 创建 session 工厂。
5. `build_seed_data()` 从 DB 读取或初始化种子用户、匿名用户、默认分享页和默认 grant。
6. `SessionStore`、`AuditStore`、`SeedData` 都用同一个 `session_maker` 写库。

代码层面的持久化边界：

| 对象 | 当前实现 | 重启后是否保留 |
|---|---|---|
| 用户、SSO 绑定、启停、org/admin 标记 | `portal_user` | 取决于 `PORTAL_DB_URL` 是否持久化 |
| 用户组与成员 | `portal_group`、`portal_group_member` | 取决于 DB |
| 分享页与公开状态 | `share_page` | 取决于 DB |
| 授权 | `share_page_grant` | 取决于 DB |
| 会话归属、标题、消息数、待删除标记 | `chat_session_owner` | 取决于 DB |
| 审计日志 | `audit_log` | 取决于 DB |
| `pt_` 短期门户令牌 | `TokenStore` 进程内存 | 不保留 |
| 公开分享 IP 限流窗口 | `IPRateLimiter` 进程内存 | 不保留 |
| RAGFlow 对话正文、引用、文档预览 | RAGFlow `API4Conversation` | 由 RAGFlow 自己持久化 |

生产风险点：

- 如果 `PORTAL_DB_URL=sqlite://`，门户所有 DB 表都是内存库，进程重启后会丢失用户、授权、会话归属和审计。
- 当前生产文档建议使用 `sqlite:////home/xijuangu/portal-data/portal.db`，并让数据文件独立于代码目录，避免 `rsync --delete` 删除。
- 当前没有 Alembic；新增列依靠 `db.py` 中的 `_migrate_add_*` 函数做有限的幂等 `ALTER TABLE ADD COLUMN`。
- `build_seed_data()` 只在固定 ID 记录不存在时创建种子数据；已有种子用户不会因 `.env` 密码变化自动改密。

## 删除策略

普通会话删除：

1. 调 RAGFlow DELETE sessions 端点。
2. 成功后硬删除 `chat_session_owner`。
3. 失败则保留记录并写 `deleted_at`，由后台任务或管理员手动重试。

硬删除用户：

1. 遍历该用户所有会话。
2. 尝试删除 RAGFlow 会话。
3. 无论 RAGFlow 是否成功，门户侧会删除用户和归属记录，避免用户已不存在但门户仍有孤儿记录。
