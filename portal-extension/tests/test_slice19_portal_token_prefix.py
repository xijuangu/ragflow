"""Slice 19 端到端测试 — 区分过期 portal T_short 与原生 beta Token(修复 109 + x.find 崩溃)。

覆盖验收点(ISSUES.md Issue 19):
  1. TokenStore.issue 签发的 T_short 以 `pt_` 前缀开头。
  2. SSE /completions:`pt_` 前缀但不在 TokenStore → 401(过期/重启后失效,触发重新登录)。
  3. SSE /completions:无 `pt_` 前缀(原生 beta Token)→ 透传 RAGFlow,保留原始 Authorization。
  4. JSON /info:`pt_` 前缀但不在 TokenStore → 401。
  5. JSON /info:无 `pt_` 前缀 → 透传 RAGFlow。
  6. `pt_` 前缀且在 TokenStore 有效 → 换 beta Token 调 RAGFlow(Slice 18 既有逻辑保持)。

设计:
  - 现有 T_short(经 _login_and_get_t_short 获取)应自带 `pt_` 前缀,无需手动构造。
  - 构造一个不在 TokenStore 的 `pt_expiredxxx` token 模拟「重启后残留的旧 T_short」。
  - 用 `ragflow-beta-token-xxx`(无 `pt_` 前缀)模拟「原生 RAGFlow 分享页用户带的 beta Token」。
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
    """辅助:mock 网关 httpx.AsyncClient,让非 SSE GET 请求返回 JSON 响应。"""
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


def _mock_upstream_sse(monkeypatch, capture=None):
    """辅助:mock 网关 httpx.AsyncClient,让 SSE POST 请求返回流式响应。"""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                if capture is not None:
                    capture.append({
                        "url": str(req.url),
                        "authorization": req.headers.get("Authorization", ""),
                    })
                return httpx.Response(
                    200,
                    content=b'data: {"answer":"ok"}\n\n',
                    headers={"content-type": "text/event-stream"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


# ===========================================================================
# 验收点 1:TokenStore.issue 签发的 T_short 以 pt_ 前缀开头
# ===========================================================================


async def test_issued_t_short_has_pt_prefix(client):
    """portal 签发的 T_short 必须以 `pt_` 前缀开头(网关据此区分自有令牌与原生 beta Token)。"""
    t_short, _ = await _login_and_get_t_short(client)
    assert t_short.startswith("pt_"), (
        f"portal 签发的 T_short 应以 pt_ 前缀开头,实际: {t_short[:10]}..."
    )


# ===========================================================================
# 验收点 2 + 3:SSE /completions 的 pt_ 前缀区分(401 vs 透传)
# ===========================================================================


async def test_sse_pt_prefix_not_in_store_returns_401(client, app, monkeypatch):
    """SSE /completions:`pt_` 前缀但不在 TokenStore → 401(过期/重启后残留的旧 T_short)。

    场景:portal 重启后 TokenStore 清空,iframe URL 里残留的旧 T_short(带 pt_ 前缀)
    调 /completions。网关识别为 portal 自有令牌但已失效 → 401(触发 iframe 重新申请 T_short),
    而非透传给 RAGFlow(透传会让 RAGFlow 返回 109,前端 x.find 崩溃)。
    """
    _mock_upstream_sse(monkeypatch)  # 若误透传,mock 会返回 200(测试断言 401 会失败)

    resp = await client.post(
        "/api/v1/chatbots/some-dialog-id/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": "Bearer pt_expired_residue_token_after_restart"},
    )
    await resp.aread()
    assert resp.status_code == 401, (
        f"pt_ 前缀但不在 TokenStore 的 token 应返回 401,实际: {resp.status_code}"
    )


async def test_sse_no_prefix_token_passthrough(client, app, monkeypatch):
    """SSE /completions:无 `pt_` 前缀(原生 beta Token)→ 透传 RAGFlow,保留原始 Authorization。

    场景:原生 RAGFlow 分享页用户带的 beta Token(非 portal 签发,无 pt_ 前缀)
    调 /completions。网关识别为非 portal 令牌 → 透传,由 RAGFlow 自身 auth 处理。
    """
    captured = []
    _mock_upstream_sse(monkeypatch, captured)

    resp = await client.post(
        "/api/v1/chatbots/some-dialog-id/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": "Bearer ragflow-native-beta-token-xxx"},
    )
    await resp.aread()
    assert resp.status_code == 200, f"无 pt_ 前缀 token 应透传,实际: {resp.status_code}"
    assert len(captured) == 1, "应透传到 RAGFlow 一次"
    assert captured[0]["authorization"] == "Bearer ragflow-native-beta-token-xxx", (
        f"应保留原始 Authorization 透传,实际: {captured[0]['authorization']}"
    )


# ===========================================================================
# 验收点 4 + 5:JSON /info 的 pt_ 前缀区分(401 vs 透传)
# ===========================================================================


async def test_json_info_pt_prefix_not_in_store_returns_401(client, app, monkeypatch):
    """JSON /info:`pt_` 前缀但不在 TokenStore → 401(非透传)。

    场景:iframe 挂载调 /info,带残留的旧 T_short(pt_ 前缀但 portal 已重启)。
    网关返回 401,iframe 前端据此重新申请 T_short,而非拿到 RAGFlow 的 109 错误体
    导致下游 doc_aggs.find 崩溃。
    """
    _mock_upstream_json(monkeypatch, 200)  # 若误透传,mock 返回 200,断言 401 失败

    resp = await client.get(
        "/api/v1/chatbots/some-dialog-id/info",
        headers={"Authorization": "Bearer pt_expired_residue_token_after_restart"},
    )
    assert resp.status_code == 401, (
        f"pt_ 前缀但不在 TokenStore 应返回 401,实际: {resp.status_code}"
    )


async def test_json_info_no_prefix_token_passthrough(client, app, monkeypatch):
    """JSON /info:无 `pt_` 前缀 → 透传 RAGFlow,保留原始 Authorization。"""
    captured = []
    _mock_upstream_json(
        monkeypatch,
        200,
        {"code": 0, "data": {"title": "原生分享页"}},
        captured,
    )

    resp = await client.get(
        "/api/v1/chatbots/some-dialog-id/info",
        headers={"Authorization": "Bearer ragflow-native-beta-token-xxx"},
    )
    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0]["authorization"] == "Bearer ragflow-native-beta-token-xxx", (
        f"应保留原始 Authorization 透传,实际: {captured[0]['authorization']}"
    )


# ===========================================================================
# 验收点 6:pt_ 前缀且在 TokenStore 有效 → 换 beta Token(Slice 18 既有逻辑保持)
# ===========================================================================


async def test_pt_prefix_valid_t_short_swaps_beta_token(client, app, monkeypatch):
    """`pt_` 前缀且在 TokenStore 有效 → 网关换 beta Token 调 RAGFlow(非透传)。

    场景:正常 portal iframe 用户,带 fresh T_short(有 pt_ 前缀,在 TokenStore)。
    网关走标准校验链 → 换 beta Token → 调 RAGFlow → 200。
    """
    t_short, dialog_id = await _login_and_get_t_short(client)
    captured = []
    _mock_upstream_json(
        monkeypatch,
        200,
        {"code": 0, "data": {"title": "portal iframe 对话"}},
        captured,
    )

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"有效 T_short 调 /info 失败: {resp.text}"
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert len(captured) == 1
    assert captured[0]["authorization"] == f"Bearer {beta_token}", (
        f"有效 pt_ T_short 应换 beta Token,实际: {captured[0]['authorization']}"
    )


# ===========================================================================
# 验收点 7:无 Authorization header → 透传(Slice 18 既有行为保持)
# ===========================================================================


async def test_no_auth_header_still_passthrough(client, app, monkeypatch):
    """无 Authorization header → 透传 RAGFlow(原生分享页未注入 auth 的场景)。"""
    captured = []
    _mock_upstream_json(monkeypatch, 200, {"code": 0, "data": {}}, captured)

    resp = await client.get("/api/v1/chatbots/some-dialog-id/info")
    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0]["authorization"] == "", "无 Authorization 时不应添加 beta Token"


# ===========================================================================
# 验收点 8:SSE /completions pt_ 前缀且有效 → 换 beta Token(补 SSE 路径覆盖)
# ===========================================================================


async def test_sse_pt_prefix_valid_t_short_swaps_beta_token(client, app, monkeypatch):
    """SSE /completions:`pt_` 前缀且在 TokenStore 有效 → 换 beta Token(非透传)。

    补 SSE 路径的 pt_ + 有效 → 换 beta Token 覆盖(JSON 路径由
    test_pt_prefix_valid_t_short_swaps_beta_token 覆盖)。
    """
    t_short, dialog_id = await _login_and_get_t_short(client)
    captured = []
    _mock_upstream_sse(monkeypatch, captured)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200, f"有效 pt_ T_short 调 SSE 失败: {resp.status_code}"
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert len(captured) == 1
    assert captured[0]["authorization"] == f"Bearer {beta_token}", (
        f"有效 pt_ T_short 应换 beta Token,实际: {captured[0]['authorization']}"
    )


# ===========================================================================
# 验收点 9:响应体逐字节透传(修复 x.find 崩溃的回归锚点)
# ===========================================================================


async def test_json_info_response_body_passthrough_byte_for_byte(client, app, monkeypatch):
    """JSON /info 响应体逐字节透传(含 doc_aggs 数组,前端 .find 不再崩溃)。

    回归锚点:确保 portal 代理不改写 RAGFlow 响应体(无额外包装、无字段丢失)。
    上游返回的 JSON 原样回传给浏览器(仅 status + content + content-type 透传)。
    """
    t_short, dialog_id = await _login_and_get_t_short(client)
    # 上游返回含 doc_aggs 数组的响应(模拟 RAGFlow /info 真实结构)
    upstream_body = b'{"code":0,"data":{"title":"test","doc_aggs":[{"doc_id":"d1","doc_name":"doc.pdf","count":3}]}}'
    import json

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(req: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    content=upstream_body,
                    headers={"content-type": "application/json"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/info",
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200
    # 响应体逐字节相等(无 portal 改写)
    assert resp.content == upstream_body, (
        f"响应体应逐字节透传,实际: {resp.content!r}, 期望: {upstream_body!r}"
    )
    # doc_aggs 为数组(前端 .find 可用)
    body = json.loads(resp.content)
    assert isinstance(body["data"]["doc_aggs"], list), "doc_aggs 应为数组(前端 .find 依赖)"
    assert len(body["data"]["doc_aggs"]) == 1
