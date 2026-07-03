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
"""

import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


@dataclass
class TokenRecord:
    """短期嵌入令牌记录(内存存储,对应 ISSUES.md 的 T_short 表)。"""

    token: str
    portal_user_id: str
    share_page_id: str
    expires_at: float  # unix 时间戳
    revoked: bool = False


class TokenStore:
    """内存令牌表 — Slice 1 不持久化,Slice 4 可换 DB。"""

    def __init__(self):
        self._tokens: dict = {}

    def issue(self, portal_user_id: str, share_page_id: str, ttl_seconds: int) -> str:
        """签发短期 T_short:随机字符串,绑定用户与分享页,设过期时间。"""
        token = secrets.token_urlsafe(32)
        self._tokens[token] = TokenRecord(
            token=token,
            portal_user_id=portal_user_id,
            share_page_id=share_page_id,
            expires_at=time.time() + ttl_seconds,
            revoked=False,
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

    def revoke(self, token: str) -> bool:
        """撤销 T_short(撤销后同令牌请求 → 401)。"""
        record = self._tokens.get(token)
        if record is None:
            return False
        record.revoked = True
        return True


def build_iframe_url(ragflow_host: str, dialog_id: str, t_short: str, session_id: str = "") -> str:
    """构造 iframe URL:auth 参数放 T_short,shared_id 放 dialog_id。

    URL 格式(对应 RAGFlow 前端原生注入点):
      {RAGFLOW_HOST}/chat/share?shared_id={dialog_id}&auth={T_short}&from=chat[&session_id=...]

    Slice 2:可选 session_id 参数注入 iframe URL(解决 RAGFlow 双步行为,
    首问直接带 session_id,RAGFlow 正常处理 question)。
    """
    params = {"shared_id": dialog_id, "auth": t_short, "from": "chat"}
    if session_id:
        params["session_id"] = session_id
    return f"{ragflow_host.rstrip('/')}/chat/share?{urlencode(params)}"


def extract_t_short(request: Request):
    """从请求 Authorization header 提取 T_short。

    iframe 内 RAGFlow 前端用 getAuthorization() 生成 'Bearer {T_short}'。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer ") :].strip() or None


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

    这是 Slice 3 完整校验链的归属隔离部分,提前在 Slice 2 落地以堵住
    「任意用户带他人 session_id 调 SSE 即可代理到 RAGFlow」的安全漏洞。
    """
    owner = session_store.get(session_id)
    if owner is None:
        raise HTTPException(status_code=403, detail="会话不存在或无权访问")
    if owner.portal_user_id != portal_user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    if owner.ragflow_resource_id != dialog_id:
        raise HTTPException(status_code=403, detail="会话与目标资源不匹配")


async def precreate_session_via_ragflow(settings, dialog_id: str) -> str:
    """调 RAGFlow 创建空 API4Conversation,返回 session_id(预创建方案)。

    调 POST /api/v1/chatbots/<dialog_id>/completions(question="", stream=true),
    RAGFlow 创建空 session 并在首帧返回 session_id(NOTES.md H4 验证的双步行为)。

    解决方案 B(推荐):门户预创建 session,首问直接带 session_id,
    RAGFlow 正常处理 question 与流式响应(无双步 prologue)。
    """
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/chatbots/{dialog_id}/completions"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token, content_type="application/json")
    # 空 question 触发 RAGFlow 创建 session 返回 prologue(NOTES.md H4 验证)
    body = json.dumps({"question": "", "stream": True, "quote": True}).encode("utf-8")
    async with _build_upstream_client(timeout=30.0) as client:
        async with client.stream("POST", upstream_url, content=body, headers=upstream_headers) as resp:
            if resp.status_code != 200:
                raise _ragflow_http_error(resp, "RAGFlow 预创建 session 失败")
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
    raise HTTPException(status_code=502, detail="RAGFlow 预创建 session 未返回 session_id")


async def fetch_session_history_via_ragflow(settings, dialog_id: str, session_id: str) -> dict:
    """调 RAGFlow GET 端点取回会话消息与引用(对应验收点 5:重新打开恢复)。

    调 GET /api/v1/chatbots/<dialog_id>/sessions/<session_id>(Slice 2 新增端点,
    复用 API4ConversationService.get_by_id,无新业务逻辑)。
    返回结构:{session_id, dialog_id, name, messages, reference}。
    """
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/chatbots/{dialog_id}/sessions/{session_id}"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token)
    async with _build_upstream_client(timeout=30.0) as client:
        resp = await client.get(upstream_url, headers=upstream_headers)
        if resp.status_code != 200:
            raise _ragflow_http_error(resp, "RAGFlow 取回会话失败")
        body = resp.json()
        # RAGFlow get_result 包裹结构:{"code":0,"data":{...}}
        data = body.get("data", body) if isinstance(body, dict) else body
        return data


async def proxy_sse_to_ragflow(request: Request, dialog_id: str):
    """SSE 代理:校验 T_short + session_id 归属 → 用 beta Token 调 RAGFlow bot_api → 流式回传。

    对应验收点 4(无效/过期 T_short → 401)与验收点 6(beta Token 调 RAGFlow SSE)。
    Slice 2 验收点 7(基础归属隔离):若请求体含 session_id,校验其归属当前 T_short
    持有用户且 dialog_id 一致,不匹配 → 403(堵住「任意用户带他人 session_id 调 SSE」漏洞)。
    Slice 2:last_active_at 仅在流成功完成后更新(失败流不更新)。
    """
    settings = request.app.state.settings
    token_store = request.app.state.token_store
    # 校验 T_short(校验链步骤 1)
    t_short = extract_t_short(request)
    if not t_short:
        raise HTTPException(status_code=401, detail="缺少 Authorization 令牌")
    record = token_store.validate(t_short)
    if record is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # 校验 T_short 绑定的分享页对应的 dialog_id 与请求的 dialog_id 一致
    seed = request.app.state.seed
    share_page = seed.share_pages_by_id.get(record.share_page_id)
    if not share_page or share_page.ragflow_resource_id != dialog_id:
        raise HTTPException(status_code=401, detail="令牌与目标资源不匹配")
    # 读取请求体(原样转发给 RAGFlow)
    body = await request.body()
    # Slice 2:从请求体解析 session_id(用于归属校验与更新 last_active_at)
    request_session_id = _parse_session_id_from_body(body)
    session_store = getattr(request.app.state, "session_store", None)
    # 归属隔离:若请求体含 session_id,校验其归属当前 T_short 持有用户(校验链步骤 2/3)
    # 不带 session_id(首次对话)时不校验;带 session_id 必须归属当前用户,否则 403。
    if request_session_id and session_store is not None:
        _assert_session_ownership(session_store, request_session_id, record.portal_user_id, dialog_id)
    # 用 beta Token 调 RAGFlow bot_api,流式转发(beta Token 只在此处使用,不返回浏览器)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/chatbots/{dialog_id}/completions"
    upstream_headers = _build_upstream_headers(settings.ragflow_beta_token, content_type="application/json")

    async def stream_generator():
        # 流式期间不设读超时(SSE 可长时间),但连接阶段设 10s 超时
        success = False
        try:
            async with _build_upstream_client(timeout=None) as client:
                async with client.stream("POST", upstream_url, content=body, headers=upstream_headers) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
            # 流完整消费完毕(无异常)才标记成功
            success = True
        except httpx.RequestError:
            # 连接失败:返回 SSE 错误事件(不含任何敏感信息)
            yield 'data: {"error": "上游服务不可用"}\n\n'.encode("utf-8")
        finally:
            # Slice 2:仅在流成功完成后更新 last_active_at(失败流不更新,避免误推活跃时间)
            if success and request_session_id and session_store is not None:
                session_store.update_last_active(request_session_id)

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
