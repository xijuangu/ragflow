"""Slice 16 端到端测试 — 悬浮组件(widget)+ Agent 支持。

覆盖验收点(ISSUES.md Issue 16):
  1. 管理员能创建 embed_type=widget 的分享页,前端生成可嵌入的 JS snippet。
  2. 悬浮组件在任意页面右下角加载,点击展开对话窗,能正常对话。
  3. 管理员能创建 ragflow_type=agent 的分享页,iframe 加载 RAGFlow Agent 界面。
  4. Agent 分享页的 SSE 代理走 agentbot 端点,流式响应正常。
  5. 网关校验链对 widget 与 agent 类型都生效。
  6. widget 与 agent 的会话进入「我的会话」列表,可重命名/删除/重新打开。
  7. X-Frame-Options 策略对 widget 场景适配(跨域嵌入需调整 CSP/frame-ancestors)。

设计:
  - 复用 conftest.py 的 mock helper(mock_precreate / mock_ragflow_sse / mock_fetch_history)。
  - widget 模式:`GET /widget/<id>` 返回独立 HTML 页面,`embed-url` 返回 widget_url + snippet。
  - agent 模式:`embed-url` 返回 agent iframe URL(/agent/share 路径),SSE 代理走 agentbot 端点。
  - CSP:widget 页面响应含 frame-ancestors 头,允许跨域嵌入;其他页面保持 SAMEORIGIN。
"""

import os
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _extract_params(url: str) -> dict:
    """从 URL 提取 query 参数。"""
    return parse_qs(urlparse(url).query)


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


def _mock_agent_precreate(monkeypatch, session_id: str):
    """辅助:mock 网关的 precreate_agent_session_via_ragflow 返回给定 session_id。"""
    monkeypatch.setattr(
        "portal.routes.precreate_agent_session_via_ragflow",
        AsyncMock(return_value=session_id),
    )


def _mock_ragflow_agent_sse_success(monkeypatch, sse_body: bytes = b'data: {"code":0}\n\n'):
    """辅助:mock 网关内的 httpx.AsyncClient,让 agent SSE 上游返回 200 成功流。"""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


# ===========================================================================
# 验收点 1:管理员能创建 embed_type=widget 的分享页
# ===========================================================================


async def test_admin_create_widget_share_page(client, app):
    """管理员能创建 embed_type=widget 的分享页,响应含 embed_type=widget。"""
    await _login(client)
    resp = await client.post(
        "/admin/share-pages",
        json={
            "name": "悬浮组件分享页",
            "ragflow_resource_id": "dialog-widget-001",
            "embed_type": "widget",
        },
    )
    assert resp.status_code == 201, f"创建失败: {resp.text}"
    body = resp.json()
    assert body["embed_type"] == "widget"
    assert body["ragflow_type"] == "chat"  # 默认 chat
    assert body["ragflow_resource_id"] == "dialog-widget-001"


async def test_admin_create_widget_share_page_default_fullscreen(client, app):
    """不传 embed_type 时默认 fullscreen(向后兼容)。"""
    await _login(client)
    resp = await client.post(
        "/admin/share-pages",
        json={"name": "默认全屏", "ragflow_resource_id": "dialog-001"},
    )
    assert resp.status_code == 201
    assert resp.json()["embed_type"] == "fullscreen"


# ===========================================================================
# 验收点 3:管理员能创建 ragflow_type=agent 的分享页
# ===========================================================================


async def test_admin_create_agent_share_page(client, app):
    """管理员能创建 ragflow_type=agent 的分享页,响应含 ragflow_type=agent。"""
    await _login(client)
    resp = await client.post(
        "/admin/share-pages",
        json={
            "name": "Agent 分享页",
            "ragflow_resource_id": "agent-abc-001",
            "ragflow_type": "agent",
        },
    )
    assert resp.status_code == 201, f"创建失败: {resp.text}"
    body = resp.json()
    assert body["ragflow_type"] == "agent"
    assert body["embed_type"] == "fullscreen"  # 默认 fullscreen
    assert body["ragflow_resource_id"] == "agent-abc-001"


async def test_admin_create_invalid_embed_type_rejected(client, app):
    """无效 embed_type 被 Pydantic 422 拒绝。"""
    await _login(client)
    resp = await client.post(
        "/admin/share-pages",
        json={
            "name": "无效",
            "ragflow_resource_id": "x",
            "embed_type": "invalid_type",
        },
    )
    assert resp.status_code == 422


async def test_admin_create_invalid_ragflow_type_rejected(client, app):
    """无效 ragflow_type 被 Pydantic 422 拒绝。"""
    await _login(client)
    resp = await client.post(
        "/admin/share-pages",
        json={
            "name": "无效",
            "ragflow_resource_id": "x",
            "ragflow_type": "invalid_type",
        },
    )
    assert resp.status_code == 422


# ===========================================================================
# 验收点 1(续):widget 分享页 embed-url 返回 widget_url + snippet
# ===========================================================================


async def test_widget_embed_url_returns_widget_url_and_snippet(client, app):
    """widget 类型 embed-url 返回 widget_url 与 snippet(而非 iframe_url)。"""
    await _login(client)
    # 创建 widget 分享页
    create = await client.post(
        "/admin/share-pages",
        json={
            "name": "悬浮组件",
            "ragflow_resource_id": "dialog-widget-002",
            "embed_type": "widget",
        },
    )
    sp_id = create.json()["id"]
    # 给 admin 授权(默认分享页已有,新建的需授权)
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    # 请求 embed-url
    resp = await client.get(f"/share-pages/{sp_id}/embed-url")
    assert resp.status_code == 200, f"embed-url 失败: {resp.text}"
    body = resp.json()
    assert body["embed_type"] == "widget"
    assert "widget_url" in body
    assert "snippet" in body
    assert sp_id in body["widget_url"]
    # widget_url 是 /widget/<id> 路径
    assert "/widget/" in body["widget_url"]
    # snippet 是可嵌入的 HTML(含 iframe 标签)
    assert "<iframe" in body["snippet"] or "<script" in body["snippet"]
    # widget 类型不返回 iframe_url(避免前端误用全屏 iframe)
    assert "iframe_url" not in body


async def test_fullscreen_embed_url_returns_iframe_url(client, app):
    """fullscreen 类型 embed-url 仍返回 iframe_url(向后兼容)。"""
    await _login(client)
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    body = resp.json()
    assert "iframe_url" in body
    # fullscreen 类型不返回 widget 专属字段
    assert "widget_url" not in body
    assert "snippet" not in body


# ===========================================================================
# 验收点 3(续):agent 分享页 embed-url 返回 agent iframe URL
# ===========================================================================


async def test_agent_embed_url_returns_agent_iframe_url(client, app):
    """agent 类型 embed-url 返回 /agent/share 路径的 iframe URL。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={
            "name": "Agent",
            "ragflow_resource_id": "agent-share-001",
            "ragflow_type": "agent",
        },
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    resp = await client.get(f"/share-pages/{sp_id}/embed-url")
    assert resp.status_code == 200, f"embed-url 失败: {resp.text}"
    body = resp.json()
    assert body["ragflow_type"] == "agent"
    assert "iframe_url" in body
    # agent iframe URL 走 /agent/share 路径(而非 /chat/share)
    assert "/agent/share" in body["iframe_url"]
    # 含 auth=T_short
    params = _extract_params(body["iframe_url"])
    assert "auth" in params
    assert "shared_id" in params


# ===========================================================================
# 验收点 2:widget 独立 HTML 页面端点
# ===========================================================================


async def test_widget_page_returns_html(client, app):
    """GET /widget/<id> 返回独立 HTML 页面(含悬浮组件标记)。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={
            "name": "悬浮组件页",
            "ragflow_resource_id": "dialog-widget-003",
            "embed_type": "widget",
        },
    )
    sp_id = create.json()["id"]
    resp = await client.get(f"/widget/{sp_id}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    # HTML 含 widget 容器标记(供测试与前端识别)
    assert "widget-root" in body or "widget-container" in body
    assert sp_id in body


async def test_widget_page_for_disabled_share_page_returns_404(client, app):
    """禁用的分享页 widget 端点返回 404。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={
            "name": "禁用悬浮",
            "ragflow_resource_id": "dialog-disabled",
            "embed_type": "widget",
        },
    )
    sp_id = create.json()["id"]
    await client.patch(f"/admin/share-pages/{sp_id}", json={"enabled": False})
    resp = await client.get(f"/widget/{sp_id}")
    assert resp.status_code == 404


# ===========================================================================
# 验收点 7:widget 页面 CSP/frame-ancestors 适配
# ===========================================================================


async def test_widget_page_has_frame_ancestors_csp(client, app):
    """widget 页面响应含 frame-ancestors CSP 头(允许跨域嵌入)。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={
            "name": "CSP 测试",
            "ragflow_resource_id": "dialog-csp",
            "embed_type": "widget",
        },
    )
    sp_id = create.json()["id"]
    resp = await client.get(f"/widget/{sp_id}")
    # widget 页面应有 frame-ancestors CSP(允许跨域嵌入)
    csp = resp.headers.get("content-security-policy", "")
    xfo = resp.headers.get("x-frame-options", "")
    # 至少有一个允许跨域嵌入的标识:frame-ancestors CSP 或无 X-Frame-Options 限制
    assert "frame-ancestors" in csp.lower() or xfo.upper() not in ("SAMEORIGIN", "DENY"), (
        f"widget 页面应允许跨域嵌入,CSP={csp!r} XFO={xfo!r}"
    )


async def test_non_widget_page_keeps_sameorigin(client, app):
    """非 widget 页面(如 /share-pages)保持 X-Frame-Options: SAMEORIGIN。"""
    await _login(client)
    resp = await client.get("/share-pages")
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"


# ===========================================================================
# 验收点 4:Agent SSE 代理走 agentbot 端点
# ===========================================================================


async def test_agent_sse_proxy_uses_agentbot_endpoint(client, app, monkeypatch):
    """Agent 分享页 SSE 代理走 /api/v1/agentbots/<id>/completions 端点。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={
            "name": "Agent SSE",
            "ragflow_resource_id": "agent-sse-001",
            "ragflow_type": "agent",
        },
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    # 预创建 agent session
    _mock_agent_precreate(monkeypatch, "agent-session-001")
    precreate = await client.post(f"/share-pages/{sp_id}/sessions")
    assert precreate.status_code == 200, f"预创建失败: {precreate.text}"
    assert precreate.json()["session_id"] == "agent-session-001"

    # 获取 T_short(从 embed-url)
    embed = await client.get(f"/share-pages/{sp_id}/embed-url")
    assert embed.status_code == 200
    t_short = _extract_params(embed.json()["iframe_url"])["auth"][0]

    # mock agent SSE 上游成功流
    captured_urls = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_urls.append(str(req.url))
                sse = 'data: {"answer":"agent 回答","session_id":"agent-session-001","id":"m1","final":true}\n\n'
                return httpx.Response(
                    200,
                    content=sse.encode("utf-8"),
                    headers={"content-type": "text/event-stream"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    # 调 agent SSE 代理端点
    resp = await client.post(
        "/api/v1/agentbots/agent-sse-001/completions",
        json={"question": "agent 测试", "stream": True, "session_id": "agent-session-001"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200, f"agent SSE 失败: {resp.text}"

    # 验证上游 URL 走 agentbot 端点(而非 chatbot)
    assert any("/api/v1/agentbots/agent-sse-001/completions" in u for u in captured_urls), (
        f"应走 agentbot 端点,实际上游 URL: {captured_urls}"
    )
    assert not any("/api/v1/chatbots/" in u for u in captured_urls), "不应走 chatbot 端点"


async def test_chat_sse_proxy_still_uses_chatbot_endpoint(client, app, monkeypatch):
    """chat 类型 SSE 代理仍走 /api/v1/chatbots/<id>/completions(向后兼容)。"""
    await _login(client)
    # 预创建 session
    monkeypatch.setattr(
        "portal.routes.precreate_session_via_ragflow",
        AsyncMock(return_value="chat-session-001"),
    )
    await client.post("/share-pages/sp_default/sessions")
    embed = await client.get("/share-pages/sp_default/embed-url")
    t_short = _extract_params(embed.json()["iframe_url"])["auth"][0]
    dialog_id = os.environ["RAGFLOW_DIALOG_ID"]

    captured_urls = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_urls.append(str(req.url))
                return httpx.Response(
                    200, content=b'data: {"code":0}\n\n', headers={"content-type": "text/event-stream"}
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "chat 测试", "stream": True, "session_id": "chat-session-001"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200
    assert any(f"/api/v1/chatbots/{dialog_id}/completions" in u for u in captured_urls)


# ===========================================================================
# 验收点 5:网关校验链对 widget 与 agent 类型都生效
# ===========================================================================


async def test_agent_sse_passthrough_unknown_token(client, app, monkeypatch):
    """Slice 18:Agent SSE 代理 — 未知的 Bearer token → 透传 RAGFlow agentbot 端点。

    旧行为:Bearer token 不在 TokenStore → 401。
    Slice 18:token 不在 TokenStore → 非 portal T_short → 透传 RAGFlow。
    """
    captured_urls = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_urls.append(str(req.url))
                return httpx.Response(
                    200,
                    content=b'data: {"code":0}\n\n',
                    headers={"content-type": "text/event-stream"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    resp = await client.post(
        "/api/v1/agentbots/agent-val-001/completions",
        json={"question": "test", "stream": True},
        headers={"Authorization": "Bearer invalid-t-short"},
    )
    await resp.aread()
    assert resp.status_code == 200
    # 验证透传到 agentbot 端点
    assert any("/api/v1/agentbots/agent-val-001/completions" in u for u in captured_urls), (
        f"应透传到 agentbot 端点,实际 URLs: {captured_urls}"
    )


async def test_agent_sse_rejects_missing_grant(client, app, monkeypatch):
    """Agent SSE 代理:无 grant 的用户 → 403(校验链步骤 1 生效)。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Agent 无授权", "ragflow_resource_id": "agent-nogrant-001", "ragflow_type": "agent"},
    )
    sp_id = create.json()["id"]
    # 不给 admin 授权,直接请求 embed-url → 403
    resp = await client.get(f"/share-pages/{sp_id}/embed-url")
    assert resp.status_code == 403


async def test_widget_embed_url_rejects_no_grant(client, app):
    """widget 分享页 embed-url:无 grant → 403(校验链对 widget 生效)。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Widget 无授权", "ragflow_resource_id": "widget-nogrant", "embed_type": "widget"},
    )
    sp_id = create.json()["id"]
    # 新建分享页无授权,请求 embed-url → 403
    resp = await client.get(f"/share-pages/{sp_id}/embed-url")
    assert resp.status_code == 403


# ===========================================================================
# 验收点 6:widget 与 agent 会话进入「我的会话」列表
# ===========================================================================


async def test_widget_session_enters_my_sessions(client, app, monkeypatch):
    """widget 分享页预创建的会话进入「我的会话」列表。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Widget 会话", "ragflow_resource_id": "widget-sess-001", "embed_type": "widget"},
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    # mock 预创建(widget 用 chat 端点预创建,ragflow_type=chat)
    monkeypatch.setattr(
        "portal.routes.precreate_session_via_ragflow",
        AsyncMock(return_value="widget-session-001"),
    )
    resp = await client.post(f"/share-pages/{sp_id}/sessions")
    assert resp.status_code == 200

    # 列表应包含该会话
    listing = await client.get(f"/share-pages/{sp_id}/sessions")
    assert listing.status_code == 200
    sessions = listing.json()["sessions"]
    assert any(s["session_id"] == "widget-session-001" for s in sessions)


async def test_agent_session_enters_my_sessions(client, app, monkeypatch):
    """agent 分享页预创建的会话进入「我的会话」列表。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Agent 会话", "ragflow_resource_id": "agent-sess-001", "ragflow_type": "agent"},
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    # mock agent 预创建
    _mock_agent_precreate(monkeypatch, "agent-my-session-001")
    resp = await client.post(f"/share-pages/{sp_id}/sessions")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "agent-my-session-001"

    listing = await client.get(f"/share-pages/{sp_id}/sessions")
    sessions = listing.json()["sessions"]
    assert any(s["session_id"] == "agent-my-session-001" for s in sessions)


async def test_agent_session_can_be_renamed_and_deleted(client, app, monkeypatch):
    """agent 会话可重命名与删除(进入「我的会话」管理)。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Agent 管理", "ragflow_resource_id": "agent-mgmt-001", "ragflow_type": "agent"},
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    _mock_agent_precreate(monkeypatch, "agent-mgmt-sess")
    await client.post(f"/share-pages/{sp_id}/sessions")

    # mock 重命名(agent 用 agentbot 端点)
    monkeypatch.setattr("portal.routes.rename_agent_session_via_ragflow", AsyncMock(return_value=None))
    rename = await client.patch(
        f"/share-pages/{sp_id}/sessions/agent-mgmt-sess",
        json={"title": "新标题"},
    )
    assert rename.status_code == 200
    assert rename.json()["title"] == "新标题"

    # 列表标题已更新
    listing = await client.get(f"/share-pages/{sp_id}/sessions")
    assert any(s["title"] == "新标题" for s in listing.json()["sessions"])

    # mock 删除
    monkeypatch.setattr("portal.routes.delete_agent_session_via_ragflow", AsyncMock(return_value=None))
    monkeypatch.setattr("portal.gateway.delete_agent_session_via_ragflow", AsyncMock(return_value=None))
    delete = await client.delete(f"/share-pages/{sp_id}/sessions/agent-mgmt-sess")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True

    # 列表不再包含
    listing2 = await client.get(f"/share-pages/{sp_id}/sessions")
    assert not any(s["session_id"] == "agent-mgmt-sess" for s in listing2.json()["sessions"])


# ===========================================================================
# 验收点 2(续):widget snippet 含 iframe 指向 /widget/<id>
# ===========================================================================


async def test_widget_snippet_contains_iframe_with_widget_url(client, app):
    """widget snippet 是可嵌入的 iframe HTML,src 指向 /widget/<id>。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Snippet 测试", "ragflow_resource_id": "snippet-001", "embed_type": "widget"},
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    resp = await client.get(f"/share-pages/{sp_id}/embed-url")
    body = resp.json()
    snippet = body["snippet"]
    # snippet 含 iframe,src 指向 widget_url
    assert "<iframe" in snippet
    assert body["widget_url"] in snippet
    # snippet 含右下角定位样式(悬浮组件特征)
    assert "fixed" in snippet.lower() or "bottom" in snippet.lower()


# ===========================================================================
# 验收点 4(续):agent 预创建走 agentbot 端点
# ===========================================================================


async def test_agent_precreate_uses_agentbot_endpoint(client, app, monkeypatch):
    """agent 分享页预创建 session 走 agentbot 端点(/api/v1/agentbots/<id>/completions)。"""
    await _login(client)
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Agent 预创建", "ragflow_resource_id": "agent-pre-001", "ragflow_type": "agent"},
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )

    captured_urls = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_urls.append(str(req.url))
                sse = b'data: {"data":{"session_id":"agent-pre-sess"}}\n\n'
                return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    resp = await client.post(f"/share-pages/{sp_id}/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    assert resp.json()["session_id"] == "agent-pre-sess"
    # 上游 URL 走 agentbot 端点
    assert any("/api/v1/agentbots/agent-pre-001/completions" in u for u in captured_urls), (
        f"预创建应走 agentbot 端点,实际: {captured_urls}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
