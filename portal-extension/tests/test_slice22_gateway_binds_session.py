"""Slice 22 端到端测试 — 网关 SSE 代理时绑定 RAGFlow 实际创建的 session。

覆盖验收点(ISSUES.md Issue 22):
  1. 请求体无 session_id(fetchSessionId 场景)→ 网关透传 → 从 SSE 响应解析 session_id
     → 流成功完成后绑定到当前用户(chat_session_owner)。
  2. 绑定后,后续请求带该 session_id → 归属校验通过 → 200(不 403)。
  3. 请求体有 session_id 且已绑定时,不重复绑定(走既有 update_last_active 路径)。
  4. 请求体有 session_id 但未绑定(越权)→ 仍 403(绑定逻辑不破坏归属隔离)。

根因(Issue 21 方向错误):
  RAGFlow 前端 use-send-shared-message.ts:77 的 session_id 来自 derivedMessages[0].session_id
  (SSE 响应),不从 URL ?session_id= 读。页面加载时 fetchSessionId 发 question='' 请求
  (无 session_id)→ RAGFlow 创建新 session → 前端存其 session_id。后续请求带这个 session_id。
  若网关未绑定该 session → 403。
"""
import httpx


def _mock_ragflow_sse_with_session_id(monkeypatch, session_id: str, message_id: str = "msg-001", nested: bool = False):
    """辅助:mock 网关的 httpx.AsyncClient,让上游 SSE 返回含指定 session_id 的成功流。

    模拟 RAGFlow fetchSessionId 响应:首帧含 session_id(RAGFlow 新建的 session)。
    nested=True 时用嵌套结构 {data:{session_id}}(RAGFlow 部分端点的返回格式),
    nested=False 时用扁平结构 {session_id}(precreate_session_via_ragflow 解析的格式)。
    """

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            if nested:
                payload = f'{{"data":{{"session_id":"{session_id}","id":"{message_id}"}},"final":true}}'
            else:
                payload = f'{{"answer":"流式","session_id":"{session_id}","id":"{message_id}","final":true}}'
            sse_body = f"data: {payload}\n\n".encode("utf-8")
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(
                    200, content=sse_body, headers={"content-type": "text/event-stream"}
                )
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def _get_t_short(client, share_page_id="sp_default"):
    """辅助:登录后调 embed-url 取 T_short(回退 Slice 21 后 fullscreen 走 embed-url)。"""
    await _login(client)
    resp = await client.get(f"/share-pages/{share_page_id}/embed-url")
    assert resp.status_code == 200, f"embed-url 失败: {resp.text}"
    iframe_url = resp.json()["iframe_url"]
    # iframe_url 形如 /chats/share?shared_id=xxx&auth=pt_yyy&from=chat
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(iframe_url).query)
    return qs["auth"][0], qs["shared_id"][0]


# ---------------------------------------------------------------------------
# 验收点 1:请求体无 session_id → 网关从 SSE 响应解析 session_id 并绑定。
# ---------------------------------------------------------------------------


async def test_gateway_binds_session_id_from_sse_response_when_request_has_none(client, app, monkeypatch):
    """greeting 请求(question='')不绑定响应 session_id,避免刷新产生垃圾会话。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice22-session-from-ragflow-001"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id)

    # 请求体无 session_id 且 question=''(模拟 RAGFlow 前端挂载时的 greeting 请求)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"greeting 请求应成功透传: {resp.text}"

    # greeting session 不应绑定到当前用户,否则刷新 iframe 会污染左侧会话列表
    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is None, "greeting session 不应写入 chat_session_owner"


# ---------------------------------------------------------------------------
# 验收点 2:绑定后,后续请求带该 session_id → 200(不 403)。
# ---------------------------------------------------------------------------


async def test_subsequent_request_with_bound_session_id_returns_200(client, app, monkeypatch):
    """greeting 创建的未绑定 session 发真实首问时应放行并在成功后绑定。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice22-session-from-ragflow-002"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-002")

    # 第一次:greeting 只拿到 RAGFlow session_id,但不绑定
    resp1 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp1.status_code == 200
    assert app.state.session_store.get(ragflow_session_id) is None

    # 第二次:真实消息带 greeting session_id,归属校验应允许首次绑定
    resp2 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "继续提问", "stream": True, "quote": True, "session_id": ragflow_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp2.status_code == 200, f"真实首问应 200,实际 {resp2.status_code}: {resp2.text}"
    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None, "真实首问成功后应绑定 session_id 到当前用户"
    assert owner.portal_user_id == "u_admin"
    assert owner.ragflow_resource_id == dialog_id


# ---------------------------------------------------------------------------
# 验收点 3:请求体有 session_id 且已绑定时,不重复绑定(走 update_last_active)。
# ---------------------------------------------------------------------------


async def test_already_bound_session_does_not_rebind(client, app, monkeypatch):
    """请求体有已绑定的 session_id → 走归属校验 + update_last_active,不重复 bind(不抛唯一约束异常)。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice22-session-already-bound-003"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-003")

    app.state.session_store.bind(
        session_id=ragflow_session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    assert app.state.session_store.get(ragflow_session_id) is not None

    # 带同一 session_id → 应 200(不重复 bind,不抛异常)
    resp2 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "二问", "stream": True, "quote": True, "session_id": ragflow_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp2.status_code == 200, f"已绑定 session 二次请求应 200: {resp2.text}"


# ---------------------------------------------------------------------------
# 验收点 4:请求体有 session_id 但未绑定(越权)→ 仍 403(绑定不破坏归属隔离)。
# ---------------------------------------------------------------------------


async def test_unbound_session_id_still_rejected_403(client, app, monkeypatch):
    """请求体有未绑定的 session_id 且 question 为空时仍拒绝,避免空请求抢绑。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "never-bound-session-004"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-004")

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "", "stream": True, "quote": True, "session_id": ragflow_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403, f"空问题不应绑定未归属 session: {resp.status_code}"
    assert app.state.session_store.get(ragflow_session_id) is None


# ---------------------------------------------------------------------------
# 验收点 5:嵌套结构 {data:{session_id}} 也能解析(RAGFlow 部分端点格式)。
# ---------------------------------------------------------------------------


async def test_gateway_binds_session_id_from_nested_sse_response(client, app, monkeypatch):
    """真实消息无 session_id 时,嵌套结构 {data:{session_id}} 也能解析并绑定。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice22-session-nested-005"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, nested=True)

    # 请求体无 session_id 但 question 非空,代表真实首问
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "首问", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"嵌套结构响应应成功: {resp.text}"

    # 嵌套结构的 session_id 也应被绑定
    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None, "网关未从嵌套结构 SSE 响应解析 session_id"
    assert owner.portal_user_id == "u_admin"
