"""Slice 18 端到端测试 — 网关统一代理分享页 API + nginx 路由分流(修复 B1 + B3)。

覆盖验收点(ISSUES.md Issue 18):
  1. portal 嵌入的 iframe 加载 RAGFlow 分享页,显示对话 UI(不再跳登录页)
  2. iframe 内 GET /api/v1/chatbots/{id}/info 返回 200(经网关换 beta Token)
  3. iframe 内 POST .../completions SSE 流式回复正常
  4. 直接访问 RAGFlow 原生分享页发消息返回 200(透传,不再 403)
  5. 原生分享页 GET /info(无 T_short)透传 RAGFlow,返回 200
  6. ragflow_type=agent 的分享页同样工作(/agentbots/{id}/inputs + /completions)
  7. 单测覆盖:网关对「有 T_short」与「无 T_short」两条分支的分发逻辑

设计:
  - /info 与 /inputs 代理:mock 上游 httpx,验证有 T_short 时换 beta Token、无 T_short 时透传。
  - SSE /completions 透传:mock 上游,验证无 T_short 时原始 Authorization 被保留。
  - 校验链:有 T_short 但 revoked → 401(校验链不跳过)。
"""

import os
from urllib.parse import parse_qs, urlparse

import httpx

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


async def _login_and_get_t_short(client, share_page_id="sp_default"):
    """辅助:登录并从 embed-url 提取 T_short 与 dialog_id。"""
    await _login(client)
    resp = await client.get(f"/share-pages/{share_page_id}/embed-url")
    assert resp.status_code == 200, f"embed-url 失败: {resp.text}"
    qs = _extract_params(resp.json()["iframe_url"])
    return qs["auth"][0], qs["shared_id"][0]


def _mock_upstream_json(monkeypatch, status_code=200, json_body=None, capture=None):
    """辅助:mock 网关 httpx.AsyncClient,让非 SSE GET 请求返回 JSON 响应。

    capture:可选 list,收集上游请求的 URL 与 Authorization。
    """
    import json

    body = json.dumps(json_body or {"code": 0, "data": {"title": "测试对话"}}).encode("utf-8")

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                if capture is not None:
                    capture.append({
                        "url": str(req.url),
                        "authorization": req.headers.get("Authorization", ""),
                    })
                return httpx.Response(
                    status_code,
                    content=body,
                    headers={"content-type": "application/json"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


# ===========================================================================
# 验收点 2:iframe 内 GET /api/v1/chatbots/{id}/info 返回 200(经网关换 beta Token)
# ===========================================================================


async def test_chatbot_info_with_tshort_uses_beta_token(client, app, monkeypatch):
    """有 T_short 调 /info → 网关换 beta Token 调 RAGFlow,返回 200。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    captured = []
    _mock_upstream_json(monkeypatch, 200, {"code": 0, "data": {"title": "测试"}}, captured)

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"/info 失败: {resp.text}"
    # 验证上游用了 beta Token(非原始 T_short)
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert len(captured) == 1
    assert captured[0]["authorization"] == f"Bearer {beta_token}", (
        f"应换 beta Token,实际: {captured[0]['authorization']}"
    )
    # 验证上游 URL 含 /info
    assert f"/api/v1/chatbots/{dialog_id}/info" in captured[0]["url"]


async def test_chatbot_info_response_passed_through(client, app, monkeypatch):
    """/info 代理的 JSON 响应原样回传(含 RAGFlow 返回的 title 等字段)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    _mock_upstream_json(
        monkeypatch,
        200,
        {"code": 0, "data": {"title": "自定义标题", "prologue": "欢迎"}},
    )

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["title"] == "自定义标题"
    assert body["data"]["prologue"] == "欢迎"


# ===========================================================================
# 验收点 5:原生分享页 GET /info(无 T_short)透传 RAGFlow,返回 200
# ===========================================================================


async def test_chatbot_info_passthrough_no_token(client, app, monkeypatch):
    """无 T_short 调 /info → 透传 RAGFlow(原生分享页场景),返回 200。"""
    captured = []
    _mock_upstream_json(monkeypatch, 200, {"code": 0, "data": {"title": "原生分享页"}}, captured)

    # 无 Authorization header → 透传
    resp = await client.get("/api/v1/chatbots/some-dialog-id/info")
    assert resp.status_code == 200
    # 验证透传到 RAGFlow(无 Authorization 添加)
    assert len(captured) == 1
    assert captured[0]["authorization"] == "", "无 Authorization 时不应添加 beta Token"
    assert "/api/v1/chatbots/some-dialog-id/info" in captured[0]["url"]


async def test_chatbot_info_passthrough_unknown_token(client, app, monkeypatch):
    """未知 Bearer token(非 portal T_short)调 /info → 透传 RAGFlow,保留原始 auth。"""
    captured = []
    _mock_upstream_json(monkeypatch, 200, {"code": 0, "data": {"title": "RAGFlow 原生"}}, captured)

    resp = await client.get(
        "/api/v1/chatbots/some-dialog-id/info",
        headers={"Authorization": "Bearer ragflow-beta-token-xxx"},
    )
    assert resp.status_code == 200
    # 验证原始 Authorization 被透传(非 portal beta Token 替换)
    assert len(captured) == 1
    assert captured[0]["authorization"] == "Bearer ragflow-beta-token-xxx", (
        f"应保留原始 Authorization 透传,实际: {captured[0]['authorization']}"
    )


# ===========================================================================
# 验收点 7:单测覆盖 — 「有 T_short」与「无 T_short」分支分发
# ===========================================================================


async def test_chatbot_info_rejects_revoked_tshort(client, app, monkeypatch):
    """有 T_short 但已撤销 → 401(校验链不跳过,非透传)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    # 撤销 T_short
    app.state.token_store.revoke(t_short)
    _mock_upstream_json(monkeypatch, 200)

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 401, f"已撤销的 T_short 应返回 401,实际: {resp.status_code}"


async def test_chatbot_info_rejects_expired_tshort(client, app, monkeypatch):
    """有 T_short 但已过期 → 401。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    # 模拟过期
    rec = app.state.token_store._tokens[t_short]
    rec.expires_at = 0.0
    _mock_upstream_json(monkeypatch, 200)

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 401


async def test_chatbot_info_rejects_missing_grant(client, app, monkeypatch):
    """有 T_short 但 grant 已撤销 → 403(校验链步骤 1 生效)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    # 撤销 admin 对 sp_default 的授权
    seed = app.state.seed
    seed.revoke_grant("sp_default", "user", "u_admin")
    _mock_upstream_json(monkeypatch, 200)

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403


async def test_chatbot_info_rejects_dialog_mismatch(client, app, monkeypatch):
    """T_short 绑定的 dialog_id 与请求的 dialog_id 不一致 → 401。"""
    t_short, _dialog_id = await _login_and_get_t_short(client)
    _mock_upstream_json(monkeypatch, 200)

    # 用不同的 dialog_id 调 /info
    resp = await client.get(
        "/api/v1/chatbots/wrong-dialog-id/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 401


# ===========================================================================
# 验收点 6:agent 类型同样工作(/agentbots/{id}/inputs + /completions)
# ===========================================================================


async def test_agentbot_inputs_with_tshort(client, app, monkeypatch):
    """Agent /inputs 代理:有 T_short → 换 beta Token 调 RAGFlow agentbot 端点。"""
    await _login(client)
    # 创建 agent 分享页 + 授权
    create = await client.post(
        "/admin/share-pages",
        json={"name": "Agent Info", "ragflow_resource_id": "agent-info-001", "ragflow_type": "agent"},
    )
    sp_id = create.json()["id"]
    await client.post(
        f"/admin/share-pages/{sp_id}/grants",
        json={"subject_type": "user", "subject_id": "u_admin", "permission": "use"},
    )
    # 获取 T_short
    embed = await client.get(f"/share-pages/{sp_id}/embed-url")
    assert embed.status_code == 200
    t_short = _extract_params(embed.json()["iframe_url"])["auth"][0]

    captured = []
    _mock_upstream_json(monkeypatch, 200, {"code": 0, "data": {"inputs": []}}, captured)

    resp = await client.get(
        "/api/v1/agentbots/agent-info-001/inputs",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"/inputs 失败: {resp.text}"
    # 验证上游走 agentbot 端点(/inputs 而非 /info)
    assert len(captured) == 1
    assert "/api/v1/agentbots/agent-info-001/inputs" in captured[0]["url"]
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert captured[0]["authorization"] == f"Bearer {beta_token}"


async def test_agentbot_inputs_passthrough_no_token(client, app, monkeypatch):
    """Agent /inputs 代理:无 T_short → 透传 RAGFlow agentbot 端点。"""
    captured = []
    _mock_upstream_json(monkeypatch, 200, {"code": 0, "data": {"inputs": []}}, captured)

    resp = await client.get("/api/v1/agentbots/agent-pass-001/inputs")
    assert resp.status_code == 200
    assert len(captured) == 1
    assert "/api/v1/agentbots/agent-pass-001/inputs" in captured[0]["url"]
    assert captured[0]["authorization"] == ""


# ===========================================================================
# 验收点 3 + 4:SSE /completions 透传(无 T_short 时原生分享页可发消息)
# ===========================================================================


async def test_chatbot_completions_passthrough_preserves_authorization(client, app, monkeypatch):
    """SSE /completions 透传:无 T_short 时保留原始 Authorization(含 RAGFlow beta Token)。"""
    captured_auth = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_auth.append(req.headers.get("Authorization", ""))
                return httpx.Response(
                    200,
                    content=b'data: {"answer":"ok"}\n\n',
                    headers={"content-type": "text/event-stream"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    # 模拟原生分享页用户:带 RAGFlow 自身 beta Token,无 portal 会话
    resp = await client.post(
        "/api/v1/chatbots/native-dialog-001/completions",
        json={"question": "原生分享页测试", "stream": True},
        headers={"Authorization": "Bearer ragflow-native-beta-token"},
    )
    await resp.aread()
    assert resp.status_code == 200
    # 验证原始 Authorization 被透传
    assert captured_auth == ["Bearer ragflow-native-beta-token"], (
        f"应保留原始 Authorization 透传,实际: {captured_auth}"
    )


async def test_chatbot_completions_with_tshort_uses_beta_token(client, app, monkeypatch):
    """SSE /completions:有 T_short → 换 beta Token(透传分支不触发)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    captured_auth = []

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                captured_auth.append(req.headers.get("Authorization", ""))
                return httpx.Response(
                    200,
                    content=b'data: {"answer":"ok","session_id":"s1","id":"m1","final":true}\n\n',
                    headers={"content-type": "text/event-stream"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "portal iframe 测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200
    # 验证上游用 beta Token(非原始 T_short)
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert captured_auth == [f"Bearer {beta_token}"], (
        f"应换 beta Token,实际: {captured_auth}"
    )


# ===========================================================================
# 验收点 5 补充:透传时 RAGFlow 上游错误状态码原样回传
# ===========================================================================


async def test_chatbot_info_passthrough_preserves_upstream_error(client, app, monkeypatch):
    """透传时 RAGFlow 返回 401 → 网关原样回传 401(不吞错误)。"""
    _mock_upstream_json(monkeypatch, 401, {"code": 401, "message": "Authentication error"})

    resp = await client.get("/api/v1/chatbots/some-dialog/info")
    assert resp.status_code == 401


async def test_chatbot_info_with_tshort_preserves_upstream_404(client, app, monkeypatch):
    """有 T_short 时 RAGFlow 返回 404 → 网关原样回传 404。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    _mock_upstream_json(monkeypatch, 404, {"code": 404, "message": "Not found"})

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 404
