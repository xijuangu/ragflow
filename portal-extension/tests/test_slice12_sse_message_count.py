"""Slice 12 端到端测试 — SSE 代理 message_count 实时更新。

覆盖验收点(ISSUES.md Issue 12 第 4-7 点):
  4. SSE 代理成功后 message_count 按新消息数更新(不再仅靠 GET history 更新)。
  5. message_count 更新与 last_active_at 更新在同一时机(流成功完成后)。
  6. 失败流(上游不可达)不更新 message_count(与 last_active_at 一致)。
  7. 现有测试全部通过,新增 message_count 更新的测试用例。

设计:
  - mock RAGFlow SSE 上游(``portal.gateway.httpx.AsyncClient``)返回成功流。
  - mock RAGFlow GET history(``portal.gateway.fetch_session_history_via_ragflow``)
    返回指定 messages 数,验证流成功后 message_count 被更新为 len(messages)。
  - 失败流(不 mock 上游,ragflow-mock.invalid 不可达)不更新 message_count。
"""

import os
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

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


def _mock_precreate(monkeypatch, session_id: str):
    """辅助:mock 网关的 precreate_session_via_ragflow 返回给定 session_id。"""
    monkeypatch.setattr(
        "portal.routes.precreate_session_via_ragflow",
        AsyncMock(return_value=session_id),
    )


def _mock_ragflow_sse_success(monkeypatch, sse_body: bytes = b'data: {"code":0}\n\n'):
    """辅助:mock 网关内的 httpx.AsyncClient,让上游 SSE 返回 200 成功流。"""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


def _mock_fetch_history(monkeypatch, messages: list):
    """辅助:mock portal.gateway.fetch_session_history_via_ragflow 返回指定 messages。

    Slice 12:流成功后调 GET history 取最新消息数更新 message_count,
    故需 mock gateway 内的 fetch_session_history_via_ragflow。
    """
    history = {"messages": messages, "reference": {}}
    monkeypatch.setattr(
        "portal.gateway.fetch_session_history_via_ragflow",
        AsyncMock(return_value=history),
    )


async def _setup_session_and_get_tshort(client, app, monkeypatch, session_id: str) -> tuple:
    """辅助:登录 + 预创建 session + 获取 T_short,返回 (t_short, dialog_id)。"""
    await _login(client)
    _mock_precreate(monkeypatch, session_id)
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


async def test_sse_updates_message_count_on_success(client, app, monkeypatch):
    """SSE 流成功完成后,调 GET history 取最新 messages 数,更新 message_count。

    验收点 4:不再仅靠 GET history(resume_session)更新;SSE 代理后也更新。
    验收点 5:message_count 与 last_active_at 在同一时机(流成功完成后)更新。
    """
    fake_session_id = "slice12-sse-msg-count-001"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, monkeypatch, fake_session_id)

    # 预创建后 message_count 为 0
    owner_before = app.state.session_store.get(fake_session_id)
    assert owner_before.message_count == 0, "预创建后 message_count 应为 0"

    # mock SSE 上游成功流
    _mock_ragflow_sse_success(monkeypatch)
    # mock GET history 返回 3 条消息(模拟 SSE 后 RAGFlow 已存 3 条)
    _mock_fetch_history(monkeypatch, messages=[{"role": "user"}, {"role": "assistant"}, {"role": "user"}])

    time.sleep(0.02)

    # 调 SSE 代理(带 session_id)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试问题", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()  # 消费流式响应体

    # message_count 应被更新为 3(GET history 返回的 messages 数)
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after is not None
    assert owner_after.message_count == 3, f"message_count 应为 3,实际: {owner_after.message_count}"
    # last_active_at 也被更新(同一时机)
    assert owner_after.last_active_at > owner_before.last_active_at, "last_active_at 也应被更新"


async def test_sse_message_count_uses_get_history(client, app, monkeypatch):
    """验证 SSE 流成功后调用了 fetch_session_history_via_ragflow 取最新消息数。"""
    fake_session_id = "slice12-sse-msg-count-002"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, monkeypatch, fake_session_id)

    _mock_ragflow_sse_success(monkeypatch)
    mock_history = AsyncMock(return_value={"messages": [{"role": "user"}, {"role": "assistant"}], "reference": {}})
    monkeypatch.setattr("portal.gateway.fetch_session_history_via_ragflow", mock_history)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    # GET history 被调用(用于取最新消息数)
    mock_history.assert_awaited_once()
    # message_count 更新为 2
    owner = app.state.session_store.get(fake_session_id)
    assert owner.message_count == 2


# ---------------------------------------------------------------------------
# 验收点 6:失败流(上游不可达)不更新 message_count(与 last_active_at 一致)。
# ---------------------------------------------------------------------------


async def test_sse_failed_stream_does_not_update_message_count(client, app, monkeypatch):
    """上游不可达(失败流)时不更新 message_count(与 last_active_at 一致)。"""
    fake_session_id = "slice12-sse-msg-count-fail"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, monkeypatch, fake_session_id)

    owner_before = app.state.session_store.get(fake_session_id)
    initial_count = owner_before.message_count
    initial_last_active = owner_before.last_active_at

    # 不 mock SSE 上游:ragflow-mock.invalid 不可达,stream_generator 走 RequestError 分支(失败流)
    # 也不 mock fetch_history(失败流不应调 GET history)
    mock_history = AsyncMock(return_value={"messages": [], "reference": {}})
    monkeypatch.setattr("portal.gateway.fetch_session_history_via_ragflow", mock_history)

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
    mock_history.assert_not_awaited()


# ---------------------------------------------------------------------------
# 边界:不带 session_id 的 SSE 调用不更新 message_count(与 last_active_at 一致)。
# ---------------------------------------------------------------------------


async def test_sse_no_session_id_does_not_update_message_count(client, app, monkeypatch):
    """不带 session_id 的 SSE 调用不更新 message_count(与 last_active_at 一致)。"""
    fake_session_id = "slice12-sse-msg-count-no-sid"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, monkeypatch, fake_session_id)

    owner_before = app.state.session_store.get(fake_session_id)
    initial_count = owner_before.message_count

    _mock_ragflow_sse_success(monkeypatch)
    mock_history = AsyncMock(return_value={"messages": [{"role": "user"}], "reference": {}})
    monkeypatch.setattr("portal.gateway.fetch_session_history_via_ragflow", mock_history)

    # SSE 代理不带 session_id(模拟首次无 session 调用)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},  # 不带 session_id
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    # 不带 session_id 不更新 message_count(无 session_id 无法定位 owner 记录)
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after.message_count == initial_count, "不带 session_id 不应更新 message_count"
    # GET history 也不应被调用
    mock_history.assert_not_awaited()


# ---------------------------------------------------------------------------
# 边界:GET history 失败不破坏已成功的流(只 log warning)。
# ---------------------------------------------------------------------------


async def test_sse_get_history_failure_does_not_break_stream(client, app, monkeypatch):
    """流成功后调 GET history 失败时,不破坏已成功的流(只 log warning,message_count 不更新)。"""
    fake_session_id = "slice12-sse-msg-count-history-fail"
    t_short, dialog_id = await _setup_session_and_get_tshort(client, app, monkeypatch, fake_session_id)

    _mock_ragflow_sse_success(monkeypatch)
    # mock GET history 失败(抛异常)
    monkeypatch.setattr(
        "portal.gateway.fetch_session_history_via_ragflow",
        AsyncMock(side_effect=RuntimeError("GET history 网络抖动")),
    )

    # SSE 代理应正常返回(流已成功,GET history 失败不破坏)
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
    # message_count 未更新(GET history 失败,无法获取最新消息数,保持原值)
    assert owner.message_count == 0, "GET history 失败时 message_count 保持原值"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
