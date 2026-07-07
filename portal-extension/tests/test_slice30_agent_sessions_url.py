"""Slice 30 测试 — agent 类型 sessions 端点 URL 构造(404 修复)。

背景:portal 网关 `_ragflow_bot_segment`(gateway.py:457)对 agent 统一返回 "agentbots",
用于 completions 与 sessions 两类端点。但 RAGFlow 官方端点路径不一致:

  - completions: agent → `/api/v1/agentbots/<id>/completions`(bot_api.py)
  - sessions:    agent → `/api/v1/agents/<id>/sessions/<sid>`(agent_api.py)

导致 portal 网关对 agent 调 `/agentbots/<id>/sessions/<sid>` 返回 404。

修复:新增 `_ragflow_sessions_segment`(agent → "agents",chat → "chatbots"),
仅用于 sessions 端点(GET/PATCH/DELETE);completions 端点仍用 `_ragflow_bot_segment`
(agent → "agentbots",保持不变)。
"""

import json
from types import SimpleNamespace

import httpx
import pytest

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_dummy_settings():
    """构造测试用 Settings(非真实凭据)。"""
    from portal.config import Settings

    return Settings(
        admin_username="a",
        admin_password="p",
        user2_username="u",
        user2_password="p",
        session_secret="s",
        ragflow_host="http://ragflow-mock.invalid",
        ragflow_beta_token="fake-beta-token",
        ragflow_dialog_id="d1",
        t_short_ttl_seconds=300,
        portal_db_url="sqlite://",
        retry_delete_interval_seconds=300,
    )


def _make_capturing_client(captured_requests, *, sse: bool = False):
    """构造 mock httpx.AsyncClient,捕获所有请求并返回 200 响应。

    - sse=False:普通 JSON 200 响应(适用于 GET/PATCH/DELETE sessions 与 precreate)
    - sse=True:SSE 流式 200 响应(适用于 precreate completions 首帧)
    """

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_requests.append(req)
                if sse:
                    content = b'data: {"data":{"session_id":"pre-sess"}}\n\n'
                    return httpx.Response(
                        200, content=content, headers={"content-type": "text/event-stream"}
                    )
                return httpx.Response(200, json={"code": 0, "data": {"id": "s1", "name": "n"}})

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    return _MockAsyncClient


# ===========================================================================
# fetch_session_history_via_ragflow:agent 走 /agents/,chat 走 /chatbots/
# ===========================================================================


async def test_fetch_session_history_agent_uses_agents_segment(monkeypatch):
    """agent 类型 fetch_session_history 走 /api/v1/agents/<id>/sessions/<sid>(非 agentbots)。"""
    from portal.gateway import fetch_session_history_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()
    await fetch_session_history_via_ragflow(
        settings, "agent-001", "sess-001", ragflow_type="agent"
    )

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/agents/agent-001/sessions/sess-001" in url, f"应走 /agents/,实际 URL: {url}"
    assert "/agentbots/" not in url, f"不应走 /agentbots/,实际 URL: {url}"
    assert captured[0].method == "GET"


async def test_fetch_session_history_chat_still_uses_chatbots_segment(monkeypatch):
    """chat 类型 fetch_session_history 仍走 /api/v1/chatbots/<id>/sessions/<sid>(向后兼容)。"""
    from portal.gateway import fetch_session_history_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()
    await fetch_session_history_via_ragflow(
        settings, "dialog-1", "sess-1", ragflow_type="chat"
    )

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/chatbots/dialog-1/sessions/sess-1" in url, f"chat 应走 /chatbots/,实际 URL: {url}"
    assert "/agents/" not in url


# ===========================================================================
# rename_session_via_ragflow:agent 走 /agents/,chat 走 /chatbots/
# ===========================================================================


async def test_rename_session_agent_uses_agents_segment(monkeypatch):
    """agent 类型 rename_session 走 /api/v1/agents/<id>/sessions/<sid>(非 agentbots)。"""
    from portal.gateway import rename_session_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()
    await rename_session_via_ragflow(
        settings, "agent-002", "sess-002", "新标题", ragflow_type="agent"
    )

    assert len(captured) == 1
    req = captured[0]
    url = str(req.url)
    assert "/api/v1/agents/agent-002/sessions/sess-002" in url, f"应走 /agents/,实际 URL: {url}"
    assert "/agentbots/" not in url
    assert req.method == "PATCH"
    body = json.loads(req.content)
    assert body["name"] == "新标题"


async def test_rename_session_chat_still_uses_chatbots_segment(monkeypatch):
    """chat 类型 rename_session 仍走 /api/v1/chatbots/<id>/sessions/<sid>(向后兼容)。"""
    from portal.gateway import rename_session_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()
    await rename_session_via_ragflow(
        settings, "dialog-2", "sess-2", "标题", ragflow_type="chat"
    )

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/chatbots/dialog-2/sessions/sess-2" in url
    assert "/agents/" not in url


# ===========================================================================
# delete_session_via_ragflow:agent 走 /agents/,chat 走 /chatbots/
# ===========================================================================


async def test_delete_session_agent_uses_agents_segment(monkeypatch):
    """agent 类型 delete_session 走 /api/v1/agents/<id>/sessions/<sid>(非 agentbots)。"""
    from portal.gateway import delete_session_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()
    await delete_session_via_ragflow(settings, "agent-003", "sess-003", ragflow_type="agent")

    assert len(captured) == 1
    req = captured[0]
    url = str(req.url)
    assert "/api/v1/agents/agent-003/sessions/sess-003" in url, f"应走 /agents/,实际 URL: {url}"
    assert "/agentbots/" not in url
    assert req.method == "DELETE"


async def test_delete_session_chat_still_uses_chatbots_segment(monkeypatch):
    """chat 类型 delete_session 仍走 /api/v1/chatbots/<id>/sessions/<sid>(向后兼容)。"""
    from portal.gateway import delete_session_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()
    await delete_session_via_ragflow(settings, "dialog-3", "sess-3", ragflow_type="chat")

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/chatbots/dialog-3/sessions/sess-3" in url
    assert "/agents/" not in url


# ===========================================================================
# completions 端点仍用 _ragflow_bot_segment(agent → agentbots,保持不变)
# ===========================================================================


async def test_precreate_agent_session_completions_still_uses_agentbots(monkeypatch):
    """agent 类型 completions 端点仍走 /api/v1/agentbots/<id>/completions(不变)。

    precreate_session_via_ragflow(ragflow_type='agent') 用于创建 session,
    调 POST /api/v1/agentbots/<id>/completions(Slice 16 既有行为)。
    """
    from portal.gateway import precreate_session_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        "portal.gateway.httpx.AsyncClient", _make_capturing_client(captured, sse=True)
    )

    settings = _make_dummy_settings()
    session_id = await precreate_session_via_ragflow(
        settings, "agent-completions-001", ragflow_type="agent"
    )
    assert session_id == "pre-sess"

    assert len(captured) == 1
    req = captured[0]
    url = str(req.url)
    assert "/api/v1/agentbots/agent-completions-001/completions" in url, (
        f"completions 应仍走 /agentbots/,实际 URL: {url}"
    )
    assert "/agents/" not in url, f"completions 不应走 /agents/,实际 URL: {url}"
    assert req.method == "POST"


async def test_precreate_chat_session_completions_uses_chatbots(monkeypatch):
    """chat 类型 completions 端点走 /api/v1/chatbots/<id>/completions(向后兼容)。"""
    from portal.gateway import precreate_session_via_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        "portal.gateway.httpx.AsyncClient", _make_capturing_client(captured, sse=True)
    )

    settings = _make_dummy_settings()
    await precreate_session_via_ragflow(settings, "chat-completions-001", ragflow_type="chat")

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/chatbots/chat-completions-001/completions" in url
    assert "/agentbots/" not in url


# ===========================================================================
# proxy_session_history_to_ragflow:agent 走 /agents/,chat 走 /chatbots/
# (Slice 24 GET /sessions/<id> 代理端点 — 同样受 Slice 30 修复影响)
# ===========================================================================


async def test_proxy_session_history_agent_uses_agents_segment(monkeypatch):
    """agent 类型 proxy_session_history_to_ragflow 走 /api/v1/agents/<id>/sessions/<sid>。

    Slice 24 的 GET /api/v1/agentbots/<id>/sessions/<sid> 代理端点也构造 sessions URL,
    同样需要走 /agents/ 段(Slice 30 修复同步覆盖此处)。
    """
    from portal.gateway import proxy_session_history_to_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()

    # 构造最小 Request mock:proxy_session_history_to_ragflow 在无 T_short 时走透传分支,
    # 用 _build_passthrough_headers(request) 与 client.get(upstream_url, headers=...)。
    class _FakeRequest:
        def __init__(self):
            self.app = SimpleNamespace(state=SimpleNamespace(settings=settings, token_store=_FakeTokenStore()))
            self.headers = {}

    class _FakeTokenStore:
        def get_record(self, t_short):
            return None

    # _build_passthrough_headers 读 request.headers,需返回 dict
    monkeypatch.setattr(
        "portal.gateway._build_passthrough_headers", lambda req: {"Authorization": "Bearer x"}
    )

    request = _FakeRequest()
    await proxy_session_history_to_ragflow(request, "agent-proxy-001", "sess-proxy-001", ragflow_type="agent")

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/agents/agent-proxy-001/sessions/sess-proxy-001" in url, (
        f"应走 /agents/,实际 URL: {url}"
    )
    assert "/agentbots/" not in url


async def test_proxy_session_history_chat_still_uses_chatbots_segment(monkeypatch):
    """chat 类型 proxy_session_history_to_ragflow 仍走 /api/v1/chatbots/<id>/sessions/<sid>。"""
    from portal.gateway import proxy_session_history_to_ragflow

    captured: list[httpx.Request] = []
    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _make_capturing_client(captured))

    settings = _make_dummy_settings()

    class _FakeRequest:
        def __init__(self):
            self.app = SimpleNamespace(state=SimpleNamespace(settings=settings, token_store=_FakeTokenStore()))
            self.headers = {}

    class _FakeTokenStore:
        def get_record(self, t_short):
            return None

    monkeypatch.setattr(
        "portal.gateway._build_passthrough_headers", lambda req: {"Authorization": "Bearer x"}
    )

    request = _FakeRequest()
    await proxy_session_history_to_ragflow(request, "chat-proxy-001", "sess-proxy-1", ragflow_type="chat")

    assert len(captured) == 1
    url = str(captured[0].url)
    assert "/api/v1/chatbots/chat-proxy-001/sessions/sess-proxy-1" in url
    assert "/agents/" not in url


# ===========================================================================
# _ragflow_sessions_segment 单元测试
# ===========================================================================


def test_ragflow_sessions_segment_agent_returns_agents():
    """_ragflow_sessions_segment('agent') 返回 'agents'(RAGFlow agent_api.py 路径)。"""
    from portal.gateway import _ragflow_sessions_segment

    assert _ragflow_sessions_segment("agent") == "agents"


def test_ragflow_sessions_segment_chat_returns_chatbots():
    """_ragflow_sessions_segment('chat') 返回 'chatbots'(RAGFlow bot_api.py 路径,向后兼容)。"""
    from portal.gateway import _ragflow_sessions_segment

    assert _ragflow_sessions_segment("chat") == "chatbots"


def test_ragflow_bot_segment_unchanged_for_completions():
    """_ragflow_bot_segment 保持不变:agent → 'agentbots',chat → 'chatbots'(用于 completions)。"""
    from portal.gateway import _ragflow_bot_segment

    assert _ragflow_bot_segment("agent") == "agentbots"
    assert _ragflow_bot_segment("chat") == "chatbots"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
