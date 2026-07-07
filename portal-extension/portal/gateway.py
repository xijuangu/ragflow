"""嵌入访问网关 — 令牌签发、iframe URL 构造、SSE 代理、session 预创建与恢复。

核心机制(原型 H1 验证结论):
  - 网关持有真实 beta Token(api_token.beta 列),绝不返回浏览器。
  - 用户登录 + 授权校验通过后,网关签发短期嵌入令牌 T_short(随机字符串,
    内存存储,5 分钟过期,可撤销)。
  - iframe URL 的 auth 参数放 T_short(RAGFlow 前端 getAuthorization() 原生
    优先读 URL ?auth=,回退才读 localStorage,因此真实 beta Token 全程不离开网关)。
  - iframe 内 SSE 请求经网关代理:校验 T_short → 用 beta Token 调 RAGFlow bot_api
    → 流式响应回传 iframe。

Slice 2 扩展(原型 H2/H3/H4 验证结论):
  - 预创建 session:用户打开分享页时,网关调 RAGFlow 创建空 API4Conversation,
    从 SSE 首帧解析 session_id,立即绑定到 chat_session_owner(解决 RAGFlow 双步行为)。
  - iframe URL 注入 session_id 参数(首问直接带 session_id,RAGFlow 正常处理 question)。
  - SSE 代理完成后更新 chat_session_owner.last_active_at。
  - 重新打开:网关调 RAGFlow GET /sessions/<session_id> 取回消息 + 引用。

Slice 3 扩展(原型 H5/H6 验证结论 — 完整校验链 + 撤销机制):
  - 网关 SSE 代理加完整四步校验链(登录态 + grant + session 归属 + dialog_id 一致)。
  - TokenStore.revoke_tokens_for_user_share_page:批量吊销某用户某分享页的所有 T_short。
  - 撤销授权 = 删 grant + 吊销 T_short;后续同 T_short 请求 → 403/401(立即失效)。

Slice 5 扩展(原型 H7/H8 验证结论 — 会话重命名/删除双删):
  - rename_session_via_ragflow:调 RAGFlow PATCH 端点更新 API4Conversation.name。
  - delete_session_via_ragflow:调 RAGFlow DELETE 端点删除 API4Conversation。
  - 均复用 Slice 2 的 _build_upstream_client / _build_upstream_headers / _ragflow_http_error helper。

Slice 16 扩展(widget + Agent 支持):
  - build_agent_iframe_url:构造 RAGFlow Agent iframe URL(/agent/share?...)。
  - build_widget_url / build_widget_snippet:widget 独立页面 URL 与可嵌入 iframe snippet。
  - precreate_agent_session_via_ragflow / fetch_agent_session_history_via_ragflow /
    rename_agent_session_via_ragflow / delete_agent_session_via_ragflow:
    agent 类型走 RAGFlow agentbot 端点(/api/v1/agentbots/<id>/...),与 chat 的
    chatbot 端点(/api/v1/chatbots/<id>/...)对应。
  - proxy_sse_to_ragflow 加 ragflow_type 参数:agent 类型上游走 agentbot 端点,
    message_count 同步调 fetch_agent_session_history_via_ragflow。
  - 校验链对 widget 与 agent 类型同样生效(不因 ragflow_type/embed_type 跳过)。
"""

import json
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from portal.auth import get_current_user

logger = logging.getLogger(__name__)


@dataclass
class TokenRecord:
    """短期嵌入令牌记录(内存存储,对应 ISSUES.md 的 T_short 表)。

    Slice 15:加 scope 字段区分标准令牌与公开令牌。
      - scope='standard':标准令牌,需 cookie + grant + session 归属校验;
      - scope='public':公开令牌,免 cookie/grant 校验,但每次校验 share_page.is_public=true
        (关闭 is_public 时,即便令牌未过期也立即 403)。
    """

    token: str
    portal_user_id: str
    share_page_id: str
    expires_at: float  # unix 时间戳
    revoked: bool = False
    scope: str = "standard"  # Slice 15:'standard'(默认)或 'public'(公开分享页)
    pending_greeting_session_ids: set[str] = field(default_factory=set)


# Slice 19:portal 签发的 T_short 前缀,网关据此区分「portal T_short」与「原生 beta Token」。
# 定义在 TokenStore 旁(前缀知识与签发/判定共处,避免散落)。
PORTAL_TOKEN_PREFIX = "pt_"


class TokenStore:
    """内存令牌表 — Slice 1 不持久化,Slice 8 保留内存(决策说明见下)。

    Slice 8 决策:T_short 不持久化,进程重启后所有已签发令牌失效。
    理由(ISSUES.md Issue 8 验收点 5 允许二选一):
      1. T_short 本质短命(默认 5 分钟过期,T_SHORT_TTL_SECONDS 可配),
         持久化的收益极小(5 分钟内的令牌很快自然过期)。
      2. T_short 是「用户登录态 + grant」的派生凭据,不是独立事实源 —
         用户重新登录即可获取新 T_short,不丢失任何业务数据。
      3. 保留内存避免每次 SSE 请求都查 DB(网关校验链步骤 2 高频调用),
         降低延迟与 DB 负载。
      4. 撤销机制靠「删 grant + 启动后 grant 已不在 DB」自然实现:
         重启后旧 T_short 失效,用户重新登录时若 grant 已撤销则拒绝签发新 T_short。
    副作用:重启时正在进行的 iframe 对话会中断(用户刷新页面重新登录即可恢复),
    历史会话(chat_session_owner)仍在 DB,不丢失。
    """

    def __init__(self):
        self._tokens: dict = {}

    def issue(self, portal_user_id: str, share_page_id: str, ttl_seconds: int, scope: str = "standard") -> str:
        """签发短期 T_short:随机字符串,绑定用户与分享页,设过期时间。

        Slice 15:scope 参数区分标准令牌('standard')与公开令牌('public')。
        公开令牌用于 /public/<id>/embed-url 签发,免 cookie/grant 校验,
        但 proxy_sse_public_to_ragflow 每次校验 share_page.is_public=true。

        Slice 19:T_short 加 `pt_` 前缀(portal token),网关据此区分「portal 签发的
        T_short」与「RAGFlow 原生 beta Token」:
          - `pt_` 前缀但不在 TokenStore → 过期/重启后失效的 portal T_short → 401(触发重新登录);
          - 无 `pt_` 前缀 → 原生 beta Token(非 portal 签发)→ 透传 RAGFlow。
        副作用:本 slice 上线后,旧格式 T_short(无 pt_ 前缀)的 iframe URL 失效,
        用户重新登录获取新 embed-url 即可(T_short 本就短命,5 分钟过期)。
        """
        token = PORTAL_TOKEN_PREFIX + secrets.token_urlsafe(32)
        self._tokens[token] = TokenRecord(
            token=token,
            portal_user_id=portal_user_id,
            share_page_id=share_page_id,
            expires_at=time.time() + ttl_seconds,
            revoked=False,
            scope=scope,
        )
        return token

    def validate(self, token: str):
        """校验 T_short 有效性:存在 / 未过期 / 未撤销。无效返回 None。"""
        record = self._tokens.get(token)
        if record is None:
            return None
        if record.revoked:
            return None
        if time.time() > record.expires_at:
            return None
        return record

    def get_record(self, token: str):
        """查找令牌记录(不论是否有效,用于获取 portal_user_id 与 share_page_id)。

        与 validate() 的区别:不校验 revoked / expires_at,仅返回记录或 None。
        用于网关校验链中「先查 grant、再查 T_short 有效性」的顺序 —
        撤销授权后 grant 不存在 → 403(即使 T_short 也被吊销),
        直接吊销 T_short(grant 仍在)→ 401。
        """
        return self._tokens.get(token)

    def revoke(self, token: str) -> bool:
        """撤销 T_short(撤销后同令牌请求 → 401)。"""
        record = self._tokens.get(token)
        if record is None:
            return False
        record.revoked = True
        return True

    def revoke_tokens_for_user_share_page(self, portal_user_id: str, share_page_id: str) -> int:
        """批量吊销某用户对某分享页的所有 T_short(对应 ISSUES.md Issue 3 撤销机制)。

        管理员撤销授权时调用:删 grant + 吊销所有已签发 T_short,
        后续同 T_short 请求 → 401(令牌已 revoked)。
        返回被吊销的令牌数量。
        """
        count = 0
        for record in self._tokens.values():
            if record.portal_user_id == portal_user_id and record.share_page_id == share_page_id and not record.revoked:
                record.revoked = True
                count += 1
        return count


class IPRateLimiter:
    """Slice 15:基于 IP 的内存滑动窗口限流器。

    每个客户端 IP 维护一个请求时间戳队列(双端队列),每次请求:
      1. 清除队列中超过 60 秒的时间戳(滑出窗口);
      2. 若队列长度 >= limit,拒绝(返回 False);
      3. 否则追加当前时间戳(返回 True)。

    内存存储,进程重启清零(可接受 — 限流是防滥用保护,不是业务事实)。
    limit <= 0 表示禁用限流(所有请求都通过)。
    """

    def __init__(self, limit_per_min: int):
        self._limit = limit_per_min
        self._hits: dict[str, deque] = {}

    def allow(self, client_ip: str) -> bool:
        """检查该 IP 是否允许通过;True=允许,False=超限。"""
        if self._limit <= 0:
            return True
        now = time.time()
        window_start = now - 60.0
        queue = self._hits.get(client_ip)
        if queue is None:
            queue = deque()
            self._hits[client_ip] = queue
        # 清除滑出窗口的时间戳
        while queue and queue[0] < window_start:
            queue.popleft()
        if len(queue) >= self._limit:
            return False
        queue.append(now)
        return True


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP(优先 X-Forwarded-For,回退 request.client.host)。"""
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        # 取第一个 IP(最左侧 = 最原始客户端)
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def build_iframe_url(ragflow_browser_origin: str, dialog_id: str, t_short: str, session_id: str = "") -> str:
    """构造 iframe URL:auth 参数放 T_short,shared_id 放 dialog_id。

    URL 格式(对应 RAGFlow 前端原生注入点):
      {RAGFLOW_BROWSER_ORIGIN}/chats/share?shared_id={dialog_id}&auth={T_short}&from=chat[&session_id=...]

    Slice 2:可选 session_id 参数注入 iframe URL(解决 RAGFlow 双步行为,
    首问直接带 session_id,RAGFlow 正常处理 question)。

    Slice 19:路径从 /chat/share(单数)修正为 /chats/share(复数) —
    RAGFlow 路由 Routes.ChatShare = '/chats/share'(web/src/routes.tsx:64)。
    旧路径 /chat/share 落到 catch-all → 渲染 404 页面(用户看到"闪一下 RAGFlow 页面")。

    Slice 19:ragflow_browser_origin 参数(非 ragflow_host)。空字符串 = 同源,
    返回相对路径 /chats/share(浏览器用当前 origin,API 请求走 nginx → portal 代理)。
    非 same-origin 部署时传完整 origin(如 http://172.16.10.180)。
    关键:iframe URL 必须走浏览器可访问的 origin(经 nginx),不能直连 RAGFlow 后端
    (如 :8080),否则 iframe 内 API 请求绕过 portal,RAGFlow 不认 pt_ T_short → 102 错误。
    """
    params = {"shared_id": dialog_id, "auth": t_short, "from": "chat"}
    if session_id:
        params["session_id"] = session_id
    base = ragflow_browser_origin.rstrip("/") if ragflow_browser_origin else ""
    return f"{base}/chats/share?{urlencode(params)}"


def build_agent_iframe_url(ragflow_browser_origin: str, agent_id: str, t_short: str, session_id: str = "") -> str:
    """构造 RAGFlow Agent iframe URL(Slice 16 — agent 类型)。

    URL 格式(对应 RAGFlow Agent 分享页注入点):
      {RAGFLOW_BROWSER_ORIGIN}/agent/share?shared_id={agent_id}&auth={T_short}&from=agent[&session_id=...]

    与 build_iframe_url 的区别:路径为 /agent/share(而非 /chats/share),from=agent。
    shared_id 放 agent_id(复用 share_page.ragflow_resource_id 字段)。

    Slice 19:ragflow_browser_origin 参数(非 ragflow_host)。空 = 同源相对路径。
    """
    params = {"shared_id": agent_id, "auth": t_short, "from": "agent"}
    if session_id:
        params["session_id"] = session_id
    base = ragflow_browser_origin.rstrip("/") if ragflow_browser_origin else ""
    return f"{base}/agent/share?{urlencode(params)}"


def build_widget_url(portal_origin: str, share_page_id: str) -> str:
    """构造 widget 独立 HTML 页面 URL(Slice 16 — widget 类型)。

    返回门户的 /widget/<share_page_id> 路径,供前端生成可嵌入 iframe snippet。
    portal_origin 为门户同源 origin(如 http://localhost:8000 或 https://portal.example);
    若为空则返回相对路径(同源场景)。
    """
    base = portal_origin.rstrip("/") if portal_origin else ""
    return f"{base}/widget/{share_page_id}"


def build_widget_snippet(widget_url: str) -> str:
    """构造可嵌入任意页面的 iframe snippet(Slice 16 — widget 类型)。

    返回一段 HTML,含右下角固定定位的 iframe(指向 /widget/<id>),
    管理员复制粘贴到任意页面即可加载悬浮组件。

    snippet 设计(简化方案,不跨 React 组件边界):
      - iframe 固定定位在页面右下角(bottom: 20px; right: 20px)。
      - 初始尺寸 400x600(悬浮对话窗典型大小)。
      - 外部页面通过 CSS 覆盖 .ragflow-widget-frame 可调整位置/尺寸。
    """
    return (
        f'<iframe class="ragflow-widget-frame" src="{widget_url}" '
        f'style="position:fixed;bottom:20px;right:20px;width:400px;height:600px;border:0;'
        f'z-index:2147483647;" title="RAGFlow 悬浮组件" allow="clipboard-read; clipboard-write"></iframe>'
    )


def extract_t_short(request: Request):
    """从请求 Authorization header 提取 T_short。

    iframe 内 RAGFlow 前端用 getAuthorization() 生成 'Bearer {T_short}'。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer ") :].strip() or None


def _is_portal_token(token: str | None) -> bool:
    """Slice 19:判断 token 是否为 portal 签发的 T_short(带 `pt_` 前缀)。

    网关透传分支据此区分:
      - `pt_` 前缀但不在 TokenStore → 过期/重启后失效的 portal T_short → 401;
      - 无 `pt_` 前缀 → 原生 beta Token(RAGFlow 自身签发)→ 透传 RAGFlow。
    """
    return bool(token) and token.startswith(PORTAL_TOKEN_PREFIX)


def _parse_session_id_from_body(body: bytes) -> str:
    """从 SSE 请求体中解析 session_id(用于归属校验与更新 last_active_at)。

    iframe 内 RAGFlow 前端在首次提问后会在请求体中携带 session_id。
    无 session_id 或解析失败返回空字符串。
    """
    if not body:
        return ""
    try:
        data = json.loads(body)
        sid = data.get("session_id") or ""
        return str(sid) if sid else ""
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""


def _parse_question_from_body(body: bytes) -> str | None:
    """从 SSE 请求体中解析 question;缺失或解析失败返回 None。

    RAGFlow 前端挂载 iframe 时会发 ``question: ""`` 的 greeting 请求。Issue 25
    需要把它与真实提问区分开,避免把 greeting session 绑定到门户会话列表。
    """
    if not body:
        return None
    try:
        data = json.loads(body)
        if "question" not in data:
            return None
        question = data.get("question")
        return str(question) if question is not None else ""
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_session_id_from_sse_chunk(chunk: bytes) -> str:
    """Slice 22:从 SSE 响应字节块解析 session_id(RAGFlow 在首帧返回新建 session 的 id)。

    RAGFlow SSE 首帧结构:{data: {session_id: "..."}} 或 {session_id: "..."}。
    chunk 可能含多行,逐行尝试解析 `data:` 前缀的 JSON。
    无 session_id 或解析失败返回空字符串(调用方累积,首个非空即用)。
    """
    if not chunk:
        return ""
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        return ""
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        # 兼容两种结构:{data:{session_id}} 与 {session_id}
        inner = data.get("data")
        sid = (
            (inner.get("session_id") if isinstance(inner, dict) else None)
            or data.get("session_id")
            or ""
        )
        if sid:
            return str(sid)
    return ""


def _build_upstream_headers(beta_token: str, content_type: str | None = None) -> dict:
    """构造发往 RAGFlow 的请求头(用 beta Token 鉴权)。

    beta Token 只在网关→RAGFlow 这一跳出现,绝不返回浏览器。
    """
    headers = {"Authorization": f"Bearer {beta_token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _build_upstream_client(*, timeout: float | None = 30.0) -> httpx.AsyncClient:
    """构造 httpx.AsyncClient(trust_env=False,网关连内部 RAGFlow 不走系统代理环境变量)。

    调用方需用 `async with` 管理生命周期。
    """
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout=timeout, connect=10.0), trust_env=False)


def _ragflow_http_error(resp, action: str) -> HTTPException:
    """统一包装 RAGFlow 上游非 200 响应为 502(网关错误)。"""
    return HTTPException(status_code=502, detail=f"{action}: HTTP {resp.status_code}")


def _assert_session_ownership(session_store, session_id: str, portal_user_id: str, dialog_id: str) -> None:
    """校验 session_id 归属当前用户且 dialog_id 一致(Slice 2 验收点 7:基础归属隔离)。

    校验链(任一失败 → 403):
      1. session_id 在 chat_session_owner 中存在;
      2. owner.portal_user_id == 当前 T_short 持有用户;
      3. owner.ragflow_resource_id == 请求 dialog_id(session 与 dialog 一致)。

    这是 Slice 3 完整校验链的归属隔离部分(步骤 3+4),提前在 Slice 2 落地以堵住
    「任意用户带他人 session_id 调 SSE 即可代理到 RAGFlow」的安全漏洞。
    """
    owner = session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=403, detail="会话不存在或无权访问")
    if owner.portal_user_id != portal_user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if owner.ragflow_resource_id != dialog_id:
        raise HTTPException(status_code=403, detail="会话与目标资源不匹配")


def _assert_or_allow_first_real_message(
    session_store,
    session_id: str,
    portal_user_id: str,
    dialog_id: str,
    request_question: str | None,
    pending_greeting_session_ids: set[str],
) -> None:
    """校验已绑定 session;真实首问允许未绑定 session 通过。

    Issue 25 中 greeting 请求先由 RAGFlow 创建 session,但门户不绑定。用户随后发
    真实首问时会带这个未绑定 session_id。这里允许 ``question != ""`` 的首问
    通过代理,实际绑定在 SSE 成功完成后发生。空 question 仍按普通归属校验拒绝。
    """
    owner = session_store.get(session_id)
    if owner is None and request_question not in (None, "") and session_id in pending_greeting_session_ids:
        return
    _assert_session_ownership(session_store, session_id, portal_user_id, dialog_id)


def _assert_grant_exists(seed, portal_user_id: str, share_page_id: str) -> None:
    """校验 grant 存在(校验链步骤 2 — Slice 3 新增,Slice 4 升级支持 group)。

    网关每次请求都校验 share_page_grant 存在性(用户或其所属组对该分享页有 use 权限)。
    撤销授权后 grant 不存在 → 403(实现「撤销立即失效」:即使 T_short 仍有效,
    grant 校验失败也拒绝代理)。这是「iframe 继续提问 → 403」的关键校验。

    Slice 3 只支持 subject_type='user';Slice 4 启用 group subject_type
    (has_use_grant 升级:user 直接授权 + 组成员继承,任一存在即通过)。
    """
    if not seed.has_use_grant(share_page_id, portal_user_id):
        raise HTTPException(status_code=403, detail="无权访问该分享页")


def _ragflow_bot_segment(ragflow_type: str) -> str:
    """RAGFlow bot URL 路径段:agent → 'agentbots',chat → 'chatbots'(TD15 统一)。

    仅用于 completions 端点(POST /api/v1/{bot_segment}/<id>/completions),
    RAGFlow bot_api.py 对 chat/agent 都用 chatbots/agentbots 路径。
    """
    return "agentbots" if ragflow_type == "agent" else "chatbots"


def _ragflow_sessions_segment(ragflow_type: str) -> str:
    """RAGFlow sessions URL 路径段:agent → 'agents',chat → 'chatbots'(Slice 30 修复)。

    用于 sessions 端点(GET/PATCH/DELETE /api/v1/{sessions_segment}/<id>/sessions/<sid>)。
    RAGFlow 官方路径不一致:
      - chat sessions:bot_api.py → /api/v1/chatbots/<id>/sessions/<sid>(与 completions 同段)
      - agent sessions:agent_api.py → /api/v1/agents/<id>/sessions/<sid>(非 agentbots!)
    故 agent sessions 必须走 /agents/ 段,否则 RAGFlow 返回 404。
    chat sessions 保持 /chatbots/(与 completions 一致,向后兼容)。
    """
    return "agents" if ragflow_type == "agent" else "chatbots"


async def precreate_session_via_ragflow(settings, dialog_id: str, ragflow_type: str = "chat") -> str:
    """调 RAGFlow 创建空 API4Conversation,返回 session_id(预创建方案)。

    调 POST /api/v1/{bot_segment}/<dialog_id>/completions(question="", stream=true),
    RAGFlow 创建空 session 并在首帧返回 session_id(NOTES.md H4 验证的双步行为)。

    解决方案 B(推荐):门户预创建 session,首问直接带 session_id,
    RAGFlow 正常处理 question 与流式响应(无双步 prologue)。

    TD15:统一 chat/agent 版本,ragflow_type='chat' 走 chatbot 端点,'agent' 走 agentbot 端点。
    首帧解析逻辑两者一致(RAGFlow agentbot 与 chatbot 返回结构相同)。
    """
    bot_segment = _ragflow_bot_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{dialog_id}/completions"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token, content_type="application/json")
    # 空 question 触发 RAGFlow 创建 session 返回 prologue(NOTES.md H4 验证)
    body = json.dumps({"question": "", "stream": True, "quote": True}).encode("utf-8")
    err_action = "预创建 agent session" if ragflow_type == "agent" else "预创建 session"
    async with _build_upstream_client(timeout=30.0) as client:
        async with client.stream("POST", upstream_url, content=body, headers=upstream_headers) as resp:
            if resp.status_code != 200:
                raise _ragflow_http_error(resp, f"RAGFlow {err_action} 失败")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                # RAGFlow 首帧结构:{"code":0,"data":{"session_id":"..."}}
                session_id = (
                    (data.get("data") or {}).get("session_id")
                    if isinstance(data.get("data"), dict)
                    else data.get("session_id")
                )
                if session_id:
                    return str(session_id)
    raise HTTPException(status_code=502, detail=f"RAGFlow {err_action} 未返回 session_id")


async def precreate_agent_session_via_ragflow(settings, agent_id: str) -> str:
    """调 RAGFlow Agent 创建空 session(Slice 16 — agent 预创建)。

    TD15:thin wrapper,委托给 precreate_session_via_ragflow(ragflow_type='agent')。
    保留独立函数名供测试 monkeypatch(patch portal.routes.precreate_agent_session_via_ragflow)。
    """
    return await precreate_session_via_ragflow(settings, agent_id, ragflow_type="agent")


async def fetch_session_history_via_ragflow(
    settings, dialog_id: str, session_id: str, ragflow_type: str = "chat"
) -> dict:
    """调 RAGFlow GET 端点取回会话消息与引用(对应验收点 5:重新打开恢复)。

    调 GET /api/v1/{bot_segment}/<dialog_id>/sessions/<session_id>(Slice 2 新增端点,
    复用 API4ConversationService.get_by_id,无新业务逻辑)。
    返回结构:{session_id, dialog_id, name, messages, reference}。

    TD15:统一 chat/agent 版本,ragflow_type='chat' 走 chatbot 端点,'agent' 走 agentbot 端点。
    返回结构两者一致(RAGFlow agentbot 与 chatbot GET 端点返回结构相同)。

    Slice 30:sessions 端点路径段用 _ragflow_sessions_segment(agent → 'agents',
    非 'agentbots')。RAGFlow agent sessions 走 agent_api.py 的 /agents/ 路径,
    bot_api.py 的 /agentbots/ 路径不含 sessions GET 端点 → 404。
    """
    bot_segment = _ragflow_sessions_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{dialog_id}/sessions/{session_id}"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token)
    err_action = "取回 agent 会话" if ragflow_type == "agent" else "取回会话"
    async with _build_upstream_client(timeout=30.0) as client:
        resp = await client.get(upstream_url, headers=upstream_headers)
        if resp.status_code != 200:
            raise _ragflow_http_error(resp, f"RAGFlow {err_action}失败")
        body = resp.json()
        # RAGFlow get_result 包裹结构:{"code":0,"data":{...}}
        data = body.get("data", body) if isinstance(body, dict) else body
        return data


async def fetch_agent_session_history_via_ragflow(settings, agent_id: str, session_id: str) -> dict:
    """调 RAGFlow Agent GET 端点取回 agent 会话消息与引用(Slice 16 — agent 类型)。

    TD15:thin wrapper,委托给 fetch_session_history_via_ragflow(ragflow_type='agent')。
    保留独立函数名供测试 monkeypatch。
    """
    return await fetch_session_history_via_ragflow(settings, agent_id, session_id, ragflow_type="agent")


async def rename_session_via_ragflow(
    settings, dialog_id: str, session_id: str, name: str, ragflow_type: str = "chat"
) -> None:
    """调 RAGFlow PATCH 端点更新 API4Conversation.name(对应 Slice 5 验收点:重命名同步)。

    复用 Slice 2 的 _build_upstream_client / _build_upstream_headers helper。
    非 200 → 抛 HTTPException(502),由调用方透传给客户端(同步策略:RAGFlow 失败则
    门户 title 不更新,保证两侧 name/title 同步,不出现一侧更新一侧未更新的不一致)。

    TD15:统一 chat/agent 版本,ragflow_type='chat' 走 chatbot 端点,'agent' 走 agentbot 端点。
    同步策略两者一致:RAGFlow 成功才更新门户 title;失败抛 502。

    Slice 30:sessions 端点路径段用 _ragflow_sessions_segment(agent → 'agents',
    非 'agentbots')。
    """
    bot_segment = _ragflow_sessions_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{dialog_id}/sessions/{session_id}"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token, content_type="application/json")
    err_action = "重命名 agent 会话" if ragflow_type == "agent" else "重命名会话"
    async with _build_upstream_client(timeout=30.0) as client:
        resp = await client.patch(upstream_url, headers=upstream_headers, json={"name": name})
        if resp.status_code != 200:
            raise _ragflow_http_error(resp, f"RAGFlow {err_action}失败")


async def rename_agent_session_via_ragflow(settings, agent_id: str, session_id: str, name: str) -> None:
    """调 RAGFlow Agent PATCH 端点更新 agent 会话标题(Slice 16 — agent 类型重命名)。

    TD15:thin wrapper,委托给 rename_session_via_ragflow(ragflow_type='agent')。
    保留独立函数名供测试 monkeypatch。
    """
    return await rename_session_via_ragflow(settings, agent_id, session_id, name, ragflow_type="agent")


async def delete_session_via_ragflow(settings, dialog_id: str, session_id: str, ragflow_type: str = "chat") -> None:
    """调 RAGFlow DELETE 端点删除 API4Conversation(对应 Slice 5 验收点:双删)。

    复用 Slice 2 的 _build_upstream_client / _build_upstream_headers helper。
    非 200 → 抛 HTTPException(502),由调用方决定标记 deleted_at 或阻塞(双删策略)。

    TD15:统一 chat/agent 版本,ragflow_type='chat' 走 chatbot 端点,'agent' 走 agentbot 端点。
    双删策略两者一致:RAGFlow 成功 → 门户硬删除;失败 → 标记 deleted_at 待重试。

    Slice 30:sessions 端点路径段用 _ragflow_sessions_segment(agent → 'agents',
    非 'agentbots')。
    """
    bot_segment = _ragflow_sessions_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{dialog_id}/sessions/{session_id}"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token)
    err_action = "删除 agent 会话" if ragflow_type == "agent" else "删除会话"
    async with _build_upstream_client(timeout=30.0) as client:
        resp = await client.delete(upstream_url, headers=upstream_headers)
        if resp.status_code != 200:
            raise _ragflow_http_error(resp, f"RAGFlow {err_action}失败")


async def delete_agent_session_via_ragflow(settings, agent_id: str, session_id: str) -> None:
    """调 RAGFlow Agent DELETE 端点删除 agent 会话(Slice 16 — agent 类型删除)。

    TD15:thin wrapper,委托给 delete_session_via_ragflow(ragflow_type='agent')。
    保留独立函数名供测试 monkeypatch(patch portal.routes 与 portal.gateway 两处)。
    """
    return await delete_session_via_ragflow(settings, agent_id, session_id, ragflow_type="agent")


async def proxy_sse_to_ragflow(request: Request, dialog_id: str, ragflow_type: str = "chat"):
    """SSE 代理:校验同源 cookie + T_short + grant + session_id 归属 → 用 beta Token 调 RAGFlow bot_api → 流式回传。

    对应验收点 4(无效/过期 T_short → 401)与验收点 6(beta Token 调 RAGFlow SSE)。
    Slice 2 验收点 7(基础归属隔离):若请求体含 session_id,校验其归属当前 T_short
    持有用户且 dialog_id 一致,不匹配 → 403(堵住「任意用户带他人 session_id 调 SSE」漏洞)。
    Slice 2:last_active_at 仅在流成功完成后更新(失败流不更新)。

    Slice 3 完整校验链(每次请求都执行,任一失败 → 403/401):
      步骤 0:同源 cookie 有效(spec 步骤 1)— get_current_user 校验门户登录态 cookie。
              iframe 同源加载时浏览器自动携带;无 cookie → 403(即使带有效 T_short)。
      步骤 1:share_page_grant 存在(spec 步骤 2)— _assert_grant_exists 校验 grant 仍在
              (撤销授权后 grant 不存在 → 403,即使 T_short 仍有效)。
      步骤 2:T_short 有效性(spec 步骤 1 的 T_short 维度)— revoked / 过期 → 401。
      步骤 3:session_id 归属当前用户(spec 步骤 3)— _assert_session_ownership 校验。
      步骤 4:session_id 的 dialog_id 与 share_page 一致(spec 步骤 4)— _assert_session_ownership 校验。

    顺序说明(spec 字面顺序为 cookie→T_short→grant→归属→dialog_id,此处调整为
    cookie→grant→T_short→归属→dialog_id):ISSUES Issue 3 验收点要求「撤销授权后
    同令牌 → 403」,而撤销授权 = 删 grant + 吊销 T_short 同时进行。若 T_short 校验在
    grant 之前,撤销后 T_short 已吊销会先返回 401,与 403 验收点矛盾。故 grant 校验
    先于 T_short 有效性校验,保证撤销后走 grant 分支 → 403。直接吊销 T_short(grant
    仍在)则走 T_short 分支 → 401。

    Slice 15 扩展:公开 T_short(scope='public')走公开校验链 —
      跳过 cookie + grant 校验,改为校验 share_page.is_public=true(每次请求都查 DB,
      关闭 is_public 立即 403),session 归属校验改为 u_anonymous。
      这是为了 iframe 兼容:iframe 内 RAGFlow 前端固定调 /api/v1/chatbots/{dialog}/completions,
      公开分享页的 iframe 用公开 T_short,需走此标准路径(不能强制 cookie)。

    Slice 18 扩展:无 T_short 或 token 不在 TokenStore(非 portal T_short)→ 透传 RAGFlow。
      原生分享页场景:用户直接访问 RAGFlow /chats/share?shared_id=xxx,RAGFlow 前端用
      自身 beta Token(非 portal T_short)调 /completions,nginx 路由到 portal,portal
      透传 RAGFlow(保留原始 Authorization + Cookie),RAGFlow 用自身 auth 处理。
      关键:区分「T_short 不存在」(token 不在 TokenStore → 透传)vs「T_short 无效」
      (token 在 TokenStore 但 revoked/expired → 401),前者透传,后者 401。

    Slice 19 扩展:用 `pt_` 前缀区分「过期 portal T_short」与「原生 beta Token」。
      portal 重启后 TokenStore 清空,iframe URL 里残留的旧 T_short(带 pt_ 前缀)调
      /completions — 旧逻辑误透传给 RAGFlow(pt_ token 非合法 beta Token → RAGFlow 返回
      109,前端 doc_aggs.find 崩溃)。新逻辑:
        - `pt_` 前缀但不在 TokenStore → 401(过期/重启后失效的 portal T_short,触发重新登录);
        - 无 `pt_` 前缀 → 原生 beta Token → 透传 RAGFlow(Slice 18 既有逻辑)。
    """
    settings = request.app.state.settings
    token_store = request.app.state.token_store
    # 提取 T_short(先于 cookie 校验,用于判断走标准链还是公开链)
    t_short = extract_t_short(request)
    record = token_store.get_record(t_short) if t_short else None
    # Slice 19:`pt_` 前缀但不在 TokenStore → 401(过期/重启后失效的 portal T_short)
    if record is None and _is_portal_token(t_short):
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # Slice 18:无 T_short 或无 `pt_` 前缀(原生 beta Token)→ 透传 RAGFlow
    # (原生分享页场景:用户带 RAGFlow 自身 beta Token,无 portal 会话)
    if record is None:
        body = await request.body()
        return _build_sse_passthrough_response(settings, request, body, dialog_id, ragflow_type)

    # Slice 15:公开 T_short 走公开校验链(iframe 兼容 — 公开分享页的 iframe 调标准路径)
    if record.scope == "public":
        return await _proxy_sse_public_core(request, dialog_id, t_short)

    # 标准校验链(scope='standard')
    # 校验链步骤 0:同源 cookie(spec 步骤 1)— 无 cookie → 403(即使带有效 T_short)
    await get_current_user(request)
    # 校验链步骤 1:grant 存在(spec 步骤 2 — 撤销授权后立即失效的关键校验)
    seed = request.app.state.seed
    _assert_grant_exists(seed, record.portal_user_id, record.share_page_id)
    # 校验链步骤 2:T_short 有效性(revoked / 过期 → 401)
    record = token_store.validate(t_short)
    if record is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # 校验 T_short 绑定的分享页对应的 dialog_id 与请求的 dialog_id 一致
    share_page = seed.get_share_page(record.share_page_id)
    if not share_page or share_page.ragflow_resource_id != dialog_id:
        raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")
    # 读取请求体(原样转发给 RAGFlow)
    body = await request.body()
    request_session_id = _parse_session_id_from_body(body)
    request_question = _parse_question_from_body(body)
    session_store = getattr(request.app.state, "session_store", None)
    # 归属隔离:若请求体含 session_id,校验其归属当前 T_short 持有用户
    if request_session_id and session_store is not None:
        _assert_or_allow_first_real_message(
            session_store,
            request_session_id,
            record.portal_user_id,
            dialog_id,
            request_question,
            record.pending_greeting_session_ids,
        )
    # Slice 22:传 portal_user_id + share_page_id + org_id,供网关在请求体无 session_id 时
    # 从 SSE 响应解析 session_id 并绑定(fetchSessionId 场景)
    return _build_sse_streaming_response(
        settings,
        body,
        dialog_id,
        request_session_id,
        request_question,
        session_store,
        ragflow_type,
        pending_greeting_session_ids=record.pending_greeting_session_ids,
        portal_user_id=record.portal_user_id,
        share_page_id=record.share_page_id,
        org_id=share_page.org_id,
    )


async def _proxy_sse_public_core(
    request: Request,
    dialog_id: str,
    t_short: str,
    *,
    body: bytes | None = None,
    fallback_session_id: str = "",
):
    """Slice 15:公开 T_short 的 SSE 代理核心校验(标准路径 + 公开端点共用,TD14 合并点)。

    公开校验链(每次请求都执行,任一失败 → 403/401):
      1. T_short 有效性(revoked / 过期 → 401);
      2. share_page.is_public == True(关闭 is_public → 403,即使 T_short 仍有效);
      3. share_page.enabled == True(禁用 → 403);
      4. share_page.ragflow_resource_id == 请求的 dialog_id;
      5. 若请求体含 session_id:归属 u_anonymous(公开会话锚点)。

    此函数不处理限流与审计(由调用方决定是否加),仅做校验 + SSE 代理。

    TD14:``proxy_sse_public_to_ragflow`` 不再内联此校验链,改为调用本函数。
    本函数拥有 T_short 有效性校验(``token_store.validate``);调用方用
    ``get_record`` 做 routing/scope 判断即可,不应预先 ``validate`` 再传入。
    参数 ``body`` 供公开端点传入已注入 session_id 的 body(标准路径不传,内部读 request.body)。
    参数 ``fallback_session_id`` 供公开端点传入 URL 中的 session_id(body 无法解析时的回退)。

    Slice 16:公开分享页当前仅支持 chat 类型(public embed-url 走 build_iframe_url
    即 /chat/share);后续若开放公开 agent,需在此传 share_page.ragflow_type。
    """
    settings = request.app.state.settings
    token_store = request.app.state.token_store
    seed = request.app.state.seed
    # 步骤 1:T_short 有效性(revoked / 过期 → 401)— 本函数拥有此校验
    record = token_store.validate(t_short)
    if record is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # 步骤 2:share_page 存在且 is_public=true(关闭 is_public → 403)
    share_page = seed.get_share_page(record.share_page_id)
    if not share_page:
        raise HTTPException(status_code=403, detail="分享页不存在")
    if not share_page.is_public:
        raise HTTPException(status_code=403, detail="公开分享已关闭")
    if not share_page.enabled:
        raise HTTPException(status_code=403, detail="分享页已禁用")
    # 步骤 3:dialog_id 一致
    if share_page.ragflow_resource_id != dialog_id:
        raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")
    # 读取请求体(调用方可传入已处理的 body;标准路径不传,内部读 request.body)
    if body is None:
        body = await request.body()
    request_session_id = _parse_session_id_from_body(body) or fallback_session_id
    request_question = _parse_question_from_body(body)
    session_store = getattr(request.app.state, "session_store", None)
    # 步骤 4:session 归属 u_anonymous(公开会话锚点)
    if request_session_id and session_store is not None:
        _assert_session_ownership(session_store, request_session_id, record.portal_user_id, dialog_id)
    # 公开分享页当前仅 chat 类型,ragflow_type 固定为 'chat'
    return _build_sse_streaming_response(
        settings, body, dialog_id, request_session_id, request_question, session_store, ragflow_type="chat"
    )


def _build_sse_streaming_response(
    settings,
    body: bytes,
    dialog_id: str,
    request_session_id: str,
    request_question: str | None,
    session_store,
    ragflow_type: str = "chat",
    *,
    pending_greeting_session_ids: set[str] | None = None,
    portal_user_id: str = "",
    share_page_id: str = "",
    org_id: str = "default",
) -> StreamingResponse:
    """构造 SSE 流式响应(标准路径与公开路径共用)。

    用 beta Token 调 RAGFlow bot_api,流式转发(beta Token 只在此处使用,不返回浏览器)。
    流成功完成后更新 last_active_at 与 message_count(与 Slice 12 一致)。

    Slice 16:加 ``ragflow_type`` 参数,agent 类型走 agentbot 端点
    (/api/v1/agentbots/<id>/completions),chat 类型走 chatbot 端点
    (/api/v1/chatbots/<id>/completions)。

    Slice 22:若请求体无 session_id 且是真实提问,RAGFlow 会新建 session 并在 SSE
    首帧返回 session_id,流成功完成后从 SSE 响应解析 session_id 并绑定到当前用户。
    根因:RAGFlow 前端 ``use-send-shared-message.ts:77`` 的 session_id 来自 SSE 响应
    (``derivedMessages[0].session_id``),不从 URL ``?session_id=`` 读 — precreate 往
    iframe URL 塞 session_id 无效,必须在网关侧绑定 RAGFlow 实际创建的 session。

    Slice 25:``question == ""`` 是 iframe 挂载时的 greeting 请求,不绑定、不计数,
    只把返回的 session_id 记入当前 T_short 的 pending 集合。真实首问只有带着
    这个 pending session_id 才能通过校验,并在流成功后绑定到当前用户。
    """
    # Slice 16:agent 类型走 agentbot 端点,chat 类型走 chatbot 端点
    bot_segment = _ragflow_bot_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{dialog_id}/completions"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token, content_type="application/json")

    async def stream_generator():
        # 流式期间不设读超时(SSE 可长时间),但连接阶段设 10s 超时
        success = False
        # Slice 22:累积解析 SSE 响应的 session_id(RAGFlow fetchSessionId 新建的 session)
        parsed_session_id = ""
        try:
            async with _build_upstream_client(timeout=None) as client:
                async with client.stream("POST", upstream_url, content=body, headers=upstream_headers) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                        # Slice 22:未解析到 session_id 时,尝试从当前 chunk 解析(首帧即含)
                        if not parsed_session_id:
                            sid = _parse_session_id_from_sse_chunk(chunk)
                            if sid:
                                parsed_session_id = sid
            # 流完整消费完毕(无异常)才标记成功
            success = True
        except httpx.RequestError:
            # 连接失败:返回 SSE 错误事件(不含任何敏感信息)
            yield 'data: {"error": "上游服务不可用"}\n\n'.encode("utf-8")
        finally:
            # Slice 22:不用 early return(会吞掉非 RequestError 的异常,如客户端断连的
            # CancelledError),改为守卫包裹。失败流不更新/绑定,异常正常传播。
            if success and session_store is not None:
                # Slice 2:仅在流成功完成后更新 last_active_at(失败流不更新,避免误推活跃时间)
                # Slice 12:同一时机更新 message_count(调 GET history 取最新消息数,
                #   与 resume_session 同逻辑保证一致;GET history 失败只 log warning 不破坏流)
                # Slice 16:agent 类型调 agentbot 端点取 history(chat 类型调 chatbot 端点)
                # Slice 22:统一目标 session_id(请求体有则用请求体的;无则用响应解析的)
                is_greeting = request_question == ""
                if is_greeting and parsed_session_id and pending_greeting_session_ids is not None:
                    pending_greeting_session_ids.add(parsed_session_id)
                target_session_id = request_session_id or (
                    parsed_session_id
                    if (not is_greeting and parsed_session_id and portal_user_id and share_page_id)
                    else ""
                )
                if target_session_id and not is_greeting:
                    # 已绑定:更新时间;未绑定:真实首问成功后建立归属。
                    if session_store.get(target_session_id) is None and portal_user_id and share_page_id:
                        session_store.bind(
                            session_id=target_session_id,
                            share_page_id=share_page_id,
                            portal_user_id=portal_user_id,
                            ragflow_resource_id=dialog_id,
                            org_id=org_id,
                        )
                        if pending_greeting_session_ids is not None:
                            pending_greeting_session_ids.discard(target_session_id)
                    else:
                        session_store.update_last_active(target_session_id)
                # Slice 26:message_count 表示 RAGFlow history.messages 条数,不再表示问答轮次。
                # GET history 失败不破坏已成功的 SSE 流,但也不写入错误的 +1 计数。
                if target_session_id and not is_greeting:
                    try:
                        await session_store.sync_message_count_from_history(
                            settings,
                            dialog_id,
                            target_session_id,
                            ragflow_type,
                        )
                    except Exception as exc:  # noqa: BLE001 - SSE 已成功,计数同步只能降级为告警。
                        logger.warning(
                            "failed to sync message_count from RAGFlow history after SSE: session_id=%s dialog_id=%s error=%s",
                            target_session_id,
                            dialog_id,
                            exc,
                        )

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


def _build_passthrough_headers(request: Request) -> dict:
    """Slice 18:构造透传请求头 — 保留原始 Authorization 与 Cookie(RAGFlow 自身鉴权)。

    仅转发白名单 header(避免转发 hop-by-hop header 或 portal 内部 header)。
    """
    headers = {}
    for key in ("Authorization", "Cookie", "Content-Type", "Accept"):
        val = request.headers.get(key)
        if val:
            headers[key] = val
    return headers


def _build_sse_passthrough_response(
    settings,
    request: Request,
    body: bytes,
    dialog_id: str,
    ragflow_type: str = "chat",
) -> StreamingResponse:
    """Slice 18:SSE 透传 — 无 T_short 时原样转发 RAGFlow(原生分享页场景)。

    保留原始 Authorization(RAGFlow 自身 beta Token)与 Cookie(RAGFlow session),
    原样转发请求,流式回传响应。不更新 last_active_at / message_count(非 portal 会话)。

    与 ``_build_sse_streaming_response`` 的区别:
      - 用原始 Authorization(非 portal beta Token);
      - 不做 session 归属校验或 message_count 更新;
      - 上游连接失败时返回 SSE 错误事件(与标准链一致)。
    """
    bot_segment = _ragflow_bot_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{dialog_id}/completions"
    passthrough_headers = _build_passthrough_headers(request)

    async def stream_generator():
        try:
            async with _build_upstream_client(timeout=None) as client:
                async with client.stream("POST", upstream_url, content=body, headers=passthrough_headers) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.RequestError:
            yield 'data: {"error": "上游服务不可用"}\n\n'.encode("utf-8")

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


async def proxy_bot_json_to_ragflow(
    request: Request,
    resource_id: str,
    suffix: str,
    ragflow_type: str = "chat",
):
    """Slice 18:非 SSE 代理(GET /info, GET /inputs)— JSON 响应原样回传。

    校验链与 SSE 代理(``proxy_sse_to_ragflow``)一致,但:
      - GET 请求无 body,无 session_id 归属校验;
      - 响应为 JSON(非 SSE 流),用 ``Response`` 回传;
      - 无 last_active_at / message_count 更新。

    路由分发(同 SSE):
      - 无 T_short 或 token 不在 TokenStore → 透传 RAGFlow(保留原始 auth);
      - T_short 在 TokenStore 但 revoked/expired → 401;
      - T_short 有效 → 校验 grant + 归属 → 换 beta Token 转发 RAGFlow。

    ``suffix`` 为 ``"info"``(chatbot)或 ``"inputs"``(agentbot),对应 RAGFlow 端点路径。
    """
    from fastapi.responses import Response

    settings = request.app.state.settings
    token_store = request.app.state.token_store
    bot_segment = _ragflow_bot_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{resource_id}/{suffix}"

    t_short = extract_t_short(request)
    record = token_store.get_record(t_short) if t_short else None
    # Slice 19:`pt_` 前缀但不在 TokenStore → 401(过期/重启后失效的 portal T_short)
    if record is None and _is_portal_token(t_short):
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # Slice 18:无 T_short 或无 `pt_` 前缀(原生 beta Token)→ 透传 RAGFlow
    if record is None:
        passthrough_headers = _build_passthrough_headers(request)
        try:
            async with _build_upstream_client() as client:
                resp = await client.get(upstream_url, headers=passthrough_headers)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="上游服务不可用")

    # T_short 在 TokenStore → 校验链
    if record.scope == "public":
        # 公开 T_short:校验 T_short 有效性 + is_public + enabled + dialog_id
        validated = token_store.validate(t_short)
        if validated is None:
            raise HTTPException(status_code=401, detail="令牌无效或已过期")
        seed = request.app.state.seed
        share_page = seed.get_share_page(validated.share_page_id)
        if not share_page:
            raise HTTPException(status_code=403, detail="分享页不存在")
        if not share_page.is_public:
            raise HTTPException(status_code=403, detail="公开分享已关闭")
        if not share_page.enabled:
            raise HTTPException(status_code=403, detail="分享页已禁用")
        if share_page.ragflow_resource_id != resource_id:
            raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")
    else:
        # 标准 T_short:cookie + grant + T_short 有效性 + dialog_id 一致
        await get_current_user(request)
        seed = request.app.state.seed
        _assert_grant_exists(seed, record.portal_user_id, record.share_page_id)
        validated = token_store.validate(t_short)
        if validated is None:
            raise HTTPException(status_code=401, detail="令牌无效或已过期")
        share_page = seed.get_share_page(validated.share_page_id)
        if not share_page or share_page.ragflow_resource_id != resource_id:
            raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")

    # 校验通过 → 用 beta Token 调 RAGFlow
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token)
    try:
        async with _build_upstream_client() as client:
            resp = await client.get(upstream_url, headers=upstream_headers)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="上游服务不可用")


async def proxy_session_history_to_ragflow(
    request: Request,
    resource_id: str,
    session_id: str,
    ragflow_type: str = "chat",
):
    """Slice 24:GET /sessions history 代理 — shared iframe 用 URL session_id 恢复历史。

    与 /info JSON 代理类似,但标准 T_short 需要额外校验 session 归属,避免用户用
    自己的 T_short 读取他人的 RAGFlow history。
    """
    from fastapi.responses import Response

    settings = request.app.state.settings
    token_store = request.app.state.token_store
    # Slice 30:sessions 端点路径段用 _ragflow_sessions_segment(agent → 'agents',
    # 非 'agentbots'),与 fetch/rename/delete_session_via_ragflow 一致。
    bot_segment = _ragflow_sessions_segment(ragflow_type)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/{bot_segment}/{resource_id}/sessions/{session_id}"

    t_short = extract_t_short(request)
    record = token_store.get_record(t_short) if t_short else None
    if record is None and _is_portal_token(t_short):
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    if record is None:
        try:
            async with _build_upstream_client() as client:
                resp = await client.get(upstream_url, headers=_build_passthrough_headers(request))
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="上游服务不可用")

    seed = request.app.state.seed
    session_store = getattr(request.app.state, "session_store", None)
    if record.scope == "public":
        validated = token_store.validate(t_short)
        if validated is None:
            raise HTTPException(status_code=401, detail="令牌无效或已过期")
        share_page = seed.get_share_page(validated.share_page_id)
        if not share_page:
            raise HTTPException(status_code=403, detail="分享页不存在")
        if not share_page.is_public:
            raise HTTPException(status_code=403, detail="公开分享已关闭")
        if not share_page.enabled:
            raise HTTPException(status_code=403, detail="分享页已禁用")
        if share_page.ragflow_resource_id != resource_id:
            raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")
        if session_store is not None:
            _assert_session_ownership(session_store, session_id, record.portal_user_id, resource_id)
    else:
        await get_current_user(request)
        _assert_grant_exists(seed, record.portal_user_id, record.share_page_id)
        validated = token_store.validate(t_short)
        if validated is None:
            raise HTTPException(status_code=401, detail="令牌无效或已过期")
        share_page = seed.get_share_page(validated.share_page_id)
        if not share_page or share_page.ragflow_resource_id != resource_id:
            raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")
        if session_store is not None:
            _assert_session_ownership(session_store, session_id, record.portal_user_id, resource_id)

    try:
        async with _build_upstream_client() as client:
            resp = await client.get(upstream_url, headers=_build_upstream_headers(settings.ragflow_beta_token))
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="上游服务不可用")


async def proxy_sse_public_to_ragflow(request: Request, share_page_id: str, session_id: str):
    """Slice 15:公开 SSE 代理端点 POST /public/<id>/sessions/<sid>/chat。

    与标准 proxy_sse_to_ragflow 的区别:
      1. 限流:按 IP 限流(每 IP 每分钟 N 次,超限 → 429);
      2. 审计:写 public_chat 审计(actor=u_anonymous,target_type=session,可配置关闭);
      3. session_id 来自 URL(不是 body),但仍支持 body 中带 session_id(RAGFlow 前端行为);
      4. 校验链:同 _proxy_sse_public_core(T_short 有效性 + is_public + dialog_id + session 归属)。

    此端点是公开分享页的显式对话端点;iframe 内 RAGFlow 前端调标准路径
    /api/v1/chatbots/{dialog}/completions 时由 proxy_sse_to_ragflow 处理(公开 T_short 走公开链)。
    """
    # 限流检查(最先执行,超限直接 429,不做任何校验)
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    if rate_limiter is not None:
        client_ip = _get_client_ip(request)
        if not rate_limiter.allow(client_ip):
            raise HTTPException(status_code=429, detail="请求过于频繁,请稍后再试")

    settings = request.app.state.settings
    token_store = request.app.state.token_store
    seed = request.app.state.seed
    # 提取 T_short → 401 if missing
    t_short = extract_t_short(request)
    if not t_short:
        raise HTTPException(status_code=401, detail="缺少 Authorization 令牌")
    record = token_store.get_record(t_short)
    if record is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # 校验 T_short scope 必须是 public(公开端点不接受标准 T_short)
    if record.scope != "public":
        raise HTTPException(status_code=401, detail="令牌类型不匹配")
    # 校验 share_page_id 一致(T_short 绑定的分享页必须与 URL 中的 share_page_id 一致)
    if record.share_page_id != share_page_id:
        raise HTTPException(status_code=401, detail="令牌与目标分享页不匹配")
    # 调用共用核心校验(T_short 有效性 + is_public + dialog_id + session 归属)
    # 需要先获取 share_page 的 dialog_id 用于 _proxy_sse_public_core 的 dialog_id 校验
    share_page = seed.get_share_page(share_page_id)
    if not share_page:
        raise HTTPException(status_code=404, detail="分享页不存在")
    dialog_id = share_page.ragflow_resource_id

    # 写审计日志(校验通过后、SSE 流之前;PUBLIC_AUDIT_ENABLED=false 时跳过)
    audit_store = getattr(request.app.state, "audit_store", None)
    if audit_store is not None and getattr(settings, "public_audit_enabled", True):
        audit_store.record(
            actor_user_id="u_anonymous",
            action="public_chat",
            target_type="session",
            target_id=session_id,
            meta={"share_page_id": share_page_id, "dialog_id": dialog_id},
        )

    # TD14:调用共用核心校验(T_short 有效性 + is_public + enabled + dialog_id + session 归属 + SSE 流)。
    # 把 URL 中的 session_id 注入 body,保证 _parse_session_id_from_body 能解出
    # (URL session_id 与 body 中的一致);body 解析失败时由 fallback_session_id 兜底。
    body = await request.body()
    # 若 body 中无 session_id,构造含 session_id 的 body(URL session_id 优先级保证)
    body_session_id = _parse_session_id_from_body(body)
    if not body_session_id and body:
        try:
            body_data = json.loads(body)
            body_data["session_id"] = session_id
            body = json.dumps(body_data).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # body 非 JSON,原样转发
    elif not body:
        # 空 body:构造最小 body 含 session_id
        body = json.dumps({"session_id": session_id, "stream": True}).encode("utf-8")

    return await _proxy_sse_public_core(
        request, dialog_id, t_short, body=body, fallback_session_id=session_id
    )
