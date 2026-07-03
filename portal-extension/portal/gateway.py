"""嵌入访问网关 — 令牌签发、iframe URL 构造、SSE 代理。

核心机制(原型 H1 验证结论):
  - 网关持有真实 beta Token(api_token.beta 列),绝不返回浏览器。
  - 用户登录 + 授权校验通过后,网关签发短期嵌入令牌 T_short(随机字符串,
    内存存储,5 分钟过期,可撤销)。
  - iframe URL 的 auth 参数放 T_short(RAGFlow 前端 getAuthorization() 原生
    优先读 URL ?auth=,回退才读 localStorage,因此真实 beta Token 全程不离开网关)。
  - iframe 内 SSE 请求经网关代理:校验 T_short → 用 beta Token 调 RAGFlow bot_api
    → 流式响应回传 iframe。
"""

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


def build_iframe_url(ragflow_host: str, dialog_id: str, t_short: str) -> str:
    """构造 iframe URL:auth 参数放 T_short,shared_id 放 dialog_id。

    URL 格式(对应 RAGFlow 前端原生注入点):
      {RAGFLOW_HOST}/chat/share?shared_id={dialog_id}&auth={T_short}&from=chat
    """
    params = urlencode({"shared_id": dialog_id, "auth": t_short, "from": "chat"})
    return f"{ragflow_host.rstrip('/')}/chat/share?{params}"


def extract_t_short(request: Request):
    """从请求 Authorization header 提取 T_short。

    iframe 内 RAGFlow 前端用 getAuthorization() 生成 'Bearer {T_short}'。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer ") :].strip() or None


async def proxy_sse_to_ragflow(request: Request, dialog_id: str):
    """SSE 代理:校验 T_short → 用 beta Token 调 RAGFlow bot_api → 流式回传。

    对应验收点 4(无效/过期 T_short → 401)与验收点 6(beta Token 调 RAGFlow SSE)。
    """
    settings = request.app.state.settings
    token_store = request.app.state.token_store
    # 校验 T_short
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
    # 用 beta Token 调 RAGFlow bot_api,流式转发(beta Token 只在此处使用,不返回浏览器)
    upstream_url = f"{settings.ragflow_host.rstrip('/')}/api/v1/chatbots/{dialog_id}/completions"
    upstream_headers = {
        "Authorization": f"Bearer {settings.ragflow_beta_token}",
        "Content-Type": "application/json",
    }

    async def stream_generator():
        # 流式期间不设读超时(SSE 可长时间),但连接阶段设 10s 超时
        timeout = httpx.Timeout(timeout=None, connect=10.0)
        try:
            # trust_env=False:网关连内部 RAGFlow 不走系统代理环境变量
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream("POST", upstream_url, content=body, headers=upstream_headers) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.RequestError:
            # 连接失败:返回 SSE 错误事件(不含任何敏感信息)
            yield 'data: {"error": "上游服务不可用"}\n\n'.encode("utf-8")

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
