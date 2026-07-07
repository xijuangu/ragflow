"""Slice 23/25 后端测试 — 网关 SSE 流完成后 message_count += 1(不依赖 GET history)。

覆盖验收点(ISSUES.md Issue 23):
  1. SSE 流成功后,已绑定 session 的 message_count +1(每轮对话 +1)。
  2. 新绑定 session(真实首问场景)的 message_count 也 +1。
  3. 连续 2 轮对话后,message_count = 2(非 0)。
  4. GET history 失败时不影响 message_count +1(increment 不依赖 GET history)。
  5. Issue 25:greeting 请求(question='')不绑定 session,也不增加 message_count。

根因(Issue 23):
  网关 _sync_message_count_after_sse 调 GET history 取 messages 长度同步 count,
  但 RAGFlow 新建空 session 返回空历史 → count 仍 0。改为 SSE 流完成后直接 +1。
"""
import httpx


def _mock_ragflow_sse_success(monkeypatch, session_id: str = "s1", message_id: str = "m1"):
    """辅助:mock 网关 httpx.AsyncClient,上游 SSE 返回成功流(含 session_id)。"""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            sse_body = (
                f'data: {{"answer":"流式","session_id":"{session_id}","id":"{message_id}","final":true}}\n\n'
            ).encode("utf-8")
            # GET history 也走此 client,返回空 messages(RAGFlow 新建空 session 场景)
            def handler(req: httpx.Request) -> httpx.Response:
                if "/sessions/" in str(req.url) and req.method == "GET":
                    # GET history 返回空 messages(模拟 RAGFlow 新建空 session)
                    return httpx.Response(200, json={"session_id": session_id, "messages": []})
                # SSE /completions
                return httpx.Response(
                    200, content=sse_body, headers={"content-type": "text/event-stream"}
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


async def _login_and_get_t_short(client):
    """辅助:登录 + 取 T_short + 取 dialog_id。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    iframe_url = resp.json()["iframe_url"]
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(iframe_url).query)
    return qs["auth"][0], qs["shared_id"][0]


# ---------------------------------------------------------------------------
# 验收点 1:SSE 流成功后,已绑定 session 的 message_count +1。
# ---------------------------------------------------------------------------


async def test_sse_success_increments_message_count_for_bound_session(client, app, monkeypatch):
    """请求体有已绑定 session_id → SSE 流成功后 message_count +1(非依赖 GET history)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    session_id = "slice23-bound-001"
    # 预先绑定 session(message_count=0)
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    _mock_ragflow_sse_success(monkeypatch, session_id=session_id)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "你好", "stream": True, "quote": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    owner = app.state.session_store.get(session_id)
    assert owner is not None
    assert owner.message_count == 1, f"SSE 流成功后 message_count 应 +1,实际 {owner.message_count}"


# ---------------------------------------------------------------------------
# 验收点 2:新绑定 session(真实首问场景)的 message_count 也 +1。
# ---------------------------------------------------------------------------


async def test_sse_success_increments_message_count_for_newly_bound_session(client, app, monkeypatch):
    """请求体无 session_id → 网关从 SSE 响应绑定 session → message_count +1。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    ragflow_session_id = "slice23-new-002"
    _mock_ragflow_sse_success(monkeypatch, session_id=ragflow_session_id)

    # 请求体无 session_id 但 question 非空,代表真实首问
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "首问", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None, "网关应从 SSE 响应绑定 session"
    assert owner.message_count == 1, f"新绑定 session message_count 应 +1,实际 {owner.message_count}"


async def test_greeting_sse_does_not_bind_or_increment_message_count(client, app, monkeypatch):
    """question='' 的 greeting 请求不绑定 session,避免刷新产生消息数 1 的垃圾会话。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    ragflow_session_id = "slice25-greeting-002"
    _mock_ragflow_sse_success(monkeypatch, session_id=ragflow_session_id)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    assert app.state.session_store.get(ragflow_session_id) is None


# ---------------------------------------------------------------------------
# 验收点 3:连续 2 轮对话后,message_count = 2(非 0)。
# ---------------------------------------------------------------------------


async def test_two_rounds_sse_increments_message_count_to_two(client, app, monkeypatch):
    """连续 2 轮对话 → message_count = 2(每轮 +1,非依赖 GET history)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    session_id = "slice23-two-rounds-003"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    _mock_ragflow_sse_success(monkeypatch, session_id=session_id)

    # 第 1 轮
    resp1 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "第一问", "stream": True, "quote": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp1.aread()
    assert resp1.status_code == 200

    # 第 2 轮
    resp2 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "第二问", "stream": True, "quote": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp2.aread()
    assert resp2.status_code == 200

    owner = app.state.session_store.get(session_id)
    assert owner is not None
    assert owner.message_count == 2, f"2 轮对话后 message_count 应 = 2,实际 {owner.message_count}"


# ---------------------------------------------------------------------------
# 验收点 4:GET history 失败时不影响 message_count +1。
# ---------------------------------------------------------------------------


async def test_get_history_failure_does_not_block_increment(client, app, monkeypatch):
    """GET history 失败 → message_count 仍 +1(increment 不依赖 GET history)。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    session_id = "slice23-history-fail-004"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            sse_body = b'data: {"answer":"ok","session_id":"' + session_id.encode() + b'","id":"m1","final":true}\n\n'

            def handler(req: httpx.Request) -> httpx.Response:
                if "/sessions/" in str(req.url) and req.method == "GET":
                    # GET history 失败(500)
                    return httpx.Response(500, json={"error": "internal"})
                # SSE /completions 成功
                return httpx.Response(
                    200, content=sse_body, headers={"content-type": "text/event-stream"}
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "你好", "stream": True, "quote": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    owner = app.state.session_store.get(session_id)
    assert owner is not None
    assert owner.message_count == 1, f"GET history 失败时 message_count 仍应 +1,实际 {owner.message_count}"
