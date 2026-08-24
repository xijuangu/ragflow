"""Slice 12 端到端测试 — SSE 代理 message_count 实时更新。

覆盖验收点(ISSUES.md Issue 12 第 4-7 点):
  4. SSE 代理成功后 message_count 按新消息数更新(不再仅靠 GET history 更新)。
  5. message_count 更新与 last_active_at 更新在同一时机(流成功完成后)。
  6. 失败流(上游不可达)不更新 message_count(与 last_active_at 一致)。
  7. 现有测试全部通过,新增 message_count 更新的测试用例。

设计:
  - mock RAGFlow SSE 上游与 GET history(FakeRagflow transport 层,Issue 97;
    原先按名 patch portal.gateway.httpx.AsyncClient 与 fetch_session_history_via_ragflow)。
  - 失败流:fake 已装但 SSE 路由未配置(handler raise ConnectError),
    与迁移前「不 mock 上游、ragflow-mock.invalid 不可达」语义一致。
"""

import os
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.conftest import get_fake_ragflow

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _extract_iframe_params(url: str) -> dict:
    """从 iframe URL 提取 query 参数。"""
    return parse_qs(urlparse(url).query)


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


def _mock_precreate(app, session_id: str):
    """辅助:配置 FakeRagflow 预创建路由返回给定 session_id。"""
    get_fake_ragflow(app).precreate_session_id = session_id


def _mock_ragflow_sse_success(app, sse_body: bytes = b'data: {"code":0}\n\n'):
    """辅助:配置 FakeRagflow SSE 路由返回 200 成功流。"""
    get_fake_ragflow(app).sse_body = sse_body


def _mock_fetch_history(app, messages: list):
    """辅助:配置 FakeRagflow GET history 路由返回指定 messages。

    Slice 12:流成功后调 GET history 取最新消息数更新 message_count。
    """
    get_fake_ragflow(app).history = {"messages": messages, "reference": {}}


async def _setup_session_and_get_tshort(client, app, session_id: str) -> tuple:
    """辅助:登录 + 预创建 session + 获取 T_short,返回 (t_short, dialog_id)。"""
    await _login(client)
    _mock_precreate(app, session_id)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    t_short = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
    dialog_id = os.environ["RAGFLOW_DIALOG_ID"]
    return t_short, dialog_id


# ---------------------------------------------------------------------------
# 验收点 4 + 5:SSE 代理成功后 message_count 按新消息数更新(与 last_active_at 同时机)。
# ---------------------------------------------------------------------------


async def test_sse_updates_message_count_on_success(client, app):
    """SSE 流成功完成后,message_count 同步为 history.messages 长度。

    验收点 4:不再仅靠恢复会话更新;SSE 代理后也按 GET history 更新。
    验收点 5:message_count 与 last_active_at 在同一时机(流成功完成后)更新。
    Slice 26:message_count 表示用户恢复会话时实际看到的 history.messages 条数。
    """
    fake_session_id = "slice12-sse-msg-count-001"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, fake_session_id)

    # 预创建后 message_count 为 0
    owner_before = app.state.session_store.get(fake_session_id)
    assert owner_before.message_count == 0, "预创建后 message_count 应为 0"

    # 配置 SSE 上游成功流 + GET history
    _mock_ragflow_sse_success(app)
    _mock_fetch_history(app, [{"role": "assistant"}, {"role": "user"}, {"role": "assistant"}])

    time.sleep(0.02)

    # 调 SSE 代理(带 session_id)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试问题", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()  # 消费流式响应体

    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after is not None
    assert owner_after.message_count == 3, f"message_count 应为 3(history messages),实际: {owner_after.message_count}"
    # last_active_at 也被更新(同一时机)
    assert owner_after.last_active_at > owner_before.last_active_at, "last_active_at 也应被更新"


async def test_sse_message_count_uses_get_history_absolute_count(client, app):
    """Slice 26:验证 SSE 流成功后调 GET history,用 messages 绝对长度计数。"""
    fake_session_id = "slice12-sse-msg-count-002"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, fake_session_id)

    fake = get_fake_ragflow(app)
    _mock_ragflow_sse_success(app)
    fake.history = {"messages": [{"role": "user"}, {"role": "assistant"}], "reference": {}}

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    assert len(fake.history_calls) == 1, "GET history 应被调用一次"
    owner = app.state.session_store.get(fake_session_id)
    assert owner.message_count == 2


# ---------------------------------------------------------------------------
# 验收点 6:失败流(上游不可达)不更新 message_count(与 last_active_at 一致)。
# ---------------------------------------------------------------------------


async def test_sse_failed_stream_does_not_update_message_count(client, app):
    """上游不可达(失败流)时不更新 message_count(与 last_active_at 一致)。"""
    fake_session_id = "slice12-sse-msg-count-fail"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, fake_session_id)

    owner_before = app.state.session_store.get(fake_session_id)
    initial_count = owner_before.message_count
    initial_last_active = owner_before.last_active_at

    # SSE 路由不配置(fake.handler raise ConnectError,失败流);
    # history 路由配置上(若失败流意外调 GET history 可被 history_calls 记录)
    fake = get_fake_ragflow(app)
    fake.history = {"messages": [], "reference": {}}

    time.sleep(0.02)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    # 失败流:message_count 不应被更新
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after.message_count == initial_count, "失败流不应更新 message_count"
    # last_active_at 也不更新(与 message_count 一致)
    assert owner_after.last_active_at == initial_last_active, "失败流不应更新 last_active_at"
    # GET history 不应被调用(失败流不进入 message_count 更新分支)
    assert fake.history_calls == [], "失败流不应调 GET history"


# ---------------------------------------------------------------------------
# 边界:不带 session_id 的 SSE 调用不更新 message_count(与 last_active_at 一致)。
# ---------------------------------------------------------------------------


async def test_sse_without_request_session_id_binds_parsed_session_and_syncs_message_count(client, app):
    """不带请求 session_id 但真实首问解析到 RAGFlow session_id 时,绑定并同步 message_count。"""
    fake_session_id = "slice12-sse-msg-count-no-sid"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, fake_session_id)

    owner_before = app.state.session_store.get(fake_session_id)
    initial_count = owner_before.message_count

    new_session_id = "slice12-sse-msg-count-new-sid"
    fake = get_fake_ragflow(app)
    _mock_ragflow_sse_success(app, f'data: {{"session_id":"{new_session_id}"}}\n\n'.encode("utf-8"))
    fake.history = {
        "messages": [{"role": "assistant"}, {"role": "user"}, {"role": "assistant"}],
        "reference": {},
    }

    # SSE 代理不带 session_id(模拟真实首问,RAGFlow 在 SSE 中返回 session_id)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},  # 不带 session_id
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after.message_count == initial_count, "不带 session_id 不应更新 message_count"
    new_owner = app.state.session_store.get(new_session_id)
    assert new_owner is not None
    assert new_owner.message_count == 3
    assert len(fake.history_calls) == 1, "GET history 应被调用一次"


# ---------------------------------------------------------------------------
# 边界:GET history 失败不破坏已成功的流(只 log warning)。
# ---------------------------------------------------------------------------


async def test_sse_get_history_failure_does_not_break_stream(client, app):
    """Slice 26:GET history 失败不破坏已成功的 SSE 流,且不写错误消息数。

    transport handler 对 GET history 抛 httpx.ConnectError(非 HTTPException,
    gateway 不捕获直接传播,由 SSE 路径的 try/except + warning 兜底),
    与原先 patch fetch_session_history_via_ragflow 抛 RuntimeError 语义一致。
    """
    fake_session_id = "slice12-sse-msg-count-history-fail"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, fake_session_id)

    _mock_ragflow_sse_success(app)
    initial_count = app.state.session_store.get(fake_session_id).message_count
    # 配置 GET history 路由抛 ConnectError(模拟网络抖动)
    fake = get_fake_ragflow(app)
    inner_handler = fake.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and "/sessions/" in req.url.path:
            raise httpx.ConnectError("mock: GET history 网络抖动", request=req)
        return inner_handler(req)

    app.state.settings.upstream_transport = httpx.MockTransport(handler)

    # SSE 代理应正常返回(流已成功)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    # 流应正常完成(状态码 200,SSE 流式响应)
    assert resp.status_code == 200, "GET history 失败不应破坏已成功的 SSE 流"
    # last_active_at 仍被更新(流成功)
    owner = app.state.session_store.get(fake_session_id)
    assert owner.last_active_at > 0
    assert owner.message_count == initial_count, (
        f"GET history 失败时 message_count 不应错误 +1,实际: {owner.message_count}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
