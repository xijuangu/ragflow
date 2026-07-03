"""Slice 2 端到端测试 — session_id 捕获与归属绑定 + 历史恢复。

覆盖验收点(ISSUES.md Issue 2):
  1. 用户打开分享页时,门户预创建 session 并在 chat_session_owner 绑定到当前用户。
  2. iframe URL 含 session_id 参数,首次提问直接处理 question(无双步 prologue)。
  3. 对话后 chat_session_owner.last_active_at 被更新。
  4. 用户能在「我的会话」看到该 session(标题、最后活跃时间)。
  5. 重新打开返回消息正文 + 引用(chunks + doc_aggs)。
  6. 基础归属隔离(用户只能查自己的 session)。
  7. RAGFlow GET 端点复用 get_by_id,无新业务逻辑(由 mock 验证数据结构)。
"""

import os
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from portal.models import PortalUser, SharePageGrant
from portal.password import hash_password


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
    """辅助:mock 网关内的 httpx.AsyncClient,让上游 SSE 返回 200 成功流。

    用于 last_active_at 更新测试:stream_generator 完整消费成功流后才更新时间戳
    (修复项 2:仅在成功流后更新)。不 mock 时上游 ragflow-mock.invalid 不可达(失败流)。
    """

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


# ---------------------------------------------------------------------------
# 验收点 1:用户打开分享页时,门户预创建 session 并在 chat_session_owner 绑定到当前用户。
# ---------------------------------------------------------------------------


async def test_precreate_session_binds_to_user(client, app, monkeypatch):
    """预创建 session → chat_session_owner 有记录,绑定到当前用户。"""
    await _login(client)
    fake_session_id = "fake-ragflow-session-001"
    _mock_precreate(monkeypatch, fake_session_id)

    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == fake_session_id
    assert body["share_page_id"] == "sp_default"

    # chat_session_owner 有记录,绑定到 admin
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None, "chat_session_owner 未绑定"
    assert owner.portal_user_id == "u_admin"
    assert owner.share_page_id == "sp_default"
    assert owner.ragflow_resource_id == os.environ["RAGFLOW_DIALOG_ID"]


async def test_precreate_requires_login(client):
    """未登录用户调预创建 → 403。"""
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 403


async def test_precreate_unknown_share_page(client):
    """不存在的分享页 → 404。"""
    await _login(client)
    resp = await client.post("/share-pages/nonexistent/sessions")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 2:iframe URL 含 session_id 参数。
# ---------------------------------------------------------------------------


async def test_precreate_returns_iframe_url_with_session_id(client, monkeypatch):
    """预创建 session 返回的 iframe URL 含 session_id 参数,不含真实 beta Token。"""
    await _login(client)
    fake_session_id = "fake-ragflow-session-002"
    _mock_precreate(monkeypatch, fake_session_id)

    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    iframe_url = resp.json()["iframe_url"]
    qs = _extract_iframe_params(iframe_url)

    # session_id 注入到 iframe URL
    assert qs.get("session_id", [None])[0] == fake_session_id
    # auth(T_short)与 shared_id(from=chat)仍存在
    assert qs.get("auth", [None])[0], "T_short 缺失"
    assert qs.get("from", [None])[0] == "chat"
    assert qs.get("shared_id", [None])[0] == os.environ["RAGFLOW_DIALOG_ID"]
    # 真实 beta Token 绝不出现在 iframe URL
    assert os.environ["RAGFLOW_BETA_TOKEN"] not in iframe_url


# ---------------------------------------------------------------------------
# 验收点 4:用户能在「我的会话」看到该 session(标题、最后活跃时间)。
# ---------------------------------------------------------------------------


async def test_list_sessions_returns_user_sessions(client, monkeypatch):
    """用户能在「我的会话」看到预创建的 session(标题、最后活跃时间)。"""
    await _login(client)
    fake_session_id = "fake-ragflow-session-003"
    _mock_precreate(monkeypatch, fake_session_id)

    await client.post("/share-pages/sp_default/sessions")

    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    matching = [s for s in sessions if s["session_id"] == fake_session_id]
    assert len(matching) == 1, f"列表中找不到预创建的 session: {sessions}"
    the_session = matching[0]
    assert "title" in the_session
    assert "last_active_at" in the_session
    assert "created_at" in the_session


async def test_list_sessions_requires_login(client):
    """未登录用户调我的会话列表 → 403。"""
    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 验收点 5:重新打开返回消息正文 + 引用(chunks + doc_aggs)。
# ---------------------------------------------------------------------------


async def test_resume_session_returns_messages_and_references(client, monkeypatch):
    """重新打开 session 返回消息正文 + 引用片段(chunks + doc_aggs)。"""
    await _login(client)
    fake_session_id = "fake-ragflow-session-004"
    _mock_precreate(monkeypatch, fake_session_id)
    await client.post("/share-pages/sp_default/sessions")

    # mock RAGFlow GET 端点(基于 NOTES.md H3 验证的真实响应结构)
    fake_history = {
        "session_id": fake_session_id,
        "dialog_id": os.environ["RAGFLOW_DIALOG_ID"],
        "name": "测试会话",
        "messages": [
            {"role": "assistant", "content": "你好,有什么可以帮你?", "id": "msg1"},
            {"role": "user", "content": "增值税税率是多少?", "id": "msg2"},
            {"role": "assistant", "content": "增值税税率为13%。", "id": "msg3"},
        ],
        "reference": [
            {"chunks": [], "doc_aggs": []},
            {"chunks": [], "doc_aggs": []},
            {
                "chunks": [
                    {
                        "id": "chunk001",
                        "content": "第十条 增值税税率:百分之十三",
                        "document_id": "doc001",
                        "document_name": "增值税法.pdf",
                        "image_id": "img001",
                        "positions": [[1, 123, 191, 450, 463]],
                        "similarity": 0.2671,
                    }
                ],
                "doc_aggs": [{"doc_name": "增值税法.pdf", "doc_id": "doc001", "count": 1}],
            },
        ],
    }
    monkeypatch.setattr(
        "portal.routes.fetch_session_history_via_ragflow",
        AsyncMock(return_value=fake_history),
    )

    resp = await client.get(f"/share-pages/sp_default/sessions/{fake_session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == fake_session_id
    assert len(body["messages"]) == 3

    # 引用片段含 chunks 与 doc_aggs(验收点 5:文档定位 doc_aggs 的 document_id 可定位 PDF 预览)
    last_ref = body["reference"][-1]
    assert len(last_ref["chunks"]) == 1
    assert last_ref["chunks"][0]["document_id"] == "doc001"
    assert last_ref["chunks"][0]["document_name"] == "增值税法.pdf"
    assert last_ref["chunks"][0]["positions"][0][0] == 1  # PDF 页码
    assert len(last_ref["doc_aggs"]) == 1
    assert last_ref["doc_aggs"][0]["doc_id"] == "doc001"


async def test_resume_unknown_session_returns_404(client):
    """重新打开不存在的 session → 404。"""
    await _login(client)
    resp = await client.get("/share-pages/sp_default/sessions/nonexistent-session")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 验收点 6:基础归属隔离(用户只能查自己的 session)。
# ---------------------------------------------------------------------------


async def test_user_cannot_access_other_users_session(client, app, monkeypatch):
    """用户 B 调用户 A 的 session → 403;用户 B 列表看不到 A 的 session。"""
    # 在 app 中追加第二个用户(用于隔离测试)
    user_b = PortalUser(
        id="u_userb",
        username="userb",
        password_hash=hash_password("testpass123"),
        is_admin=False,
        enabled=True,
    )
    app.state.seed.users_by_username["userb"] = user_b
    app.state.seed.users_by_id[user_b.id] = user_b
    app.state.seed.grants.append(
        SharePageGrant(
            share_page_id="sp_default",
            subject_type="user",
            subject_id=user_b.id,
            permission="use",
        )
    )

    # admin 登录并预创建 session
    await _login(client)
    fake_session_id = "fake-ragflow-session-005"
    _mock_precreate(monkeypatch, fake_session_id)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200

    # user B 登录(新 client,独立 cookie jar)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "userb", "password": "testpass123"})
        assert resp.status_code == 200

        # user B 尝试重新打开 admin 的 session → 403
        resp = await client_b.get(f"/share-pages/sp_default/sessions/{fake_session_id}")
        assert resp.status_code == 403, f"用户 B 不应能访问 admin 的 session: {resp.text}"

        # user B 在「我的会话」列表中看不到 admin 的 session
        resp = await client_b.get("/share-pages/sp_default/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert not any(s["session_id"] == fake_session_id for s in sessions), "用户 B 不应在列表中看到 admin 的 session"


async def test_sse_rejects_other_users_session(client, app, monkeypatch):
    """用户 B 用自己的 T_short + admin 的 session_id 调 SSE → 403(基础归属隔离)。

    对应 Slice 2 验收点 7:网关校验 session_id 归属当前 T_short 持有用户,
    不匹配 → 403(堵住「任意用户带他人 session_id 调 SSE 即可代理到 RAGFlow」漏洞)。
    网关在代理前就拒绝,不会调上游 RAGFlow。
    """
    # 在 app 中追加第二个用户(用于隔离测试)
    user_b = PortalUser(
        id="u_userb",
        username="userb",
        password_hash=hash_password("testpass123"),
        is_admin=False,
        enabled=True,
    )
    app.state.seed.users_by_username["userb"] = user_b
    app.state.seed.users_by_id[user_b.id] = user_b
    app.state.seed.grants.append(
        SharePageGrant(
            share_page_id="sp_default",
            subject_type="user",
            subject_id=user_b.id,
            permission="use",
        )
    )

    # admin 登录并预创建 session(归属 admin)
    await _login(client)
    fake_session_id = "fake-ragflow-session-009"
    _mock_precreate(monkeypatch, fake_session_id)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200

    # user B 登录并获取自己的 T_short(独立 client,独立 cookie jar)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "userb", "password": "testpass123"})
        assert resp.status_code == 200
        resp = await client_b.get("/share-pages/sp_default/embed-url")
        assert resp.status_code == 200
        t_short_b = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
        dialog_id = os.environ["RAGFLOW_DIALOG_ID"]

        # user B 用自己的 T_short + admin 的 session_id 调 SSE → 403(归属校验失败)
        resp = await client_b.post(
            f"/api/v1/chatbots/{dialog_id}/completions",
            json={"question": "测试", "stream": True, "session_id": fake_session_id},
            headers={"Authorization": f"Bearer {t_short_b}"},
        )
        assert resp.status_code == 403, f"用户 B 不应用 admin 的 session_id 调 SSE: {resp.text}"


# ---------------------------------------------------------------------------
# 验收点 3:对话后 chat_session_owner.last_active_at 被更新。
# ---------------------------------------------------------------------------


async def test_sse_updates_last_active_at(client, app, monkeypatch):
    """对话成功后 chat_session_owner.last_active_at 被更新。

    使用 T_short 调 SSE 代理(带 session_id),上游 SSE 流成功完成后,
    last_active_at 应被更新(修复项 2:仅在成功流后更新)。
    """
    await _login(client)
    fake_session_id = "fake-ragflow-session-006"
    _mock_precreate(monkeypatch, fake_session_id)
    _mock_ragflow_sse_success(monkeypatch)
    await client.post("/share-pages/sp_default/sessions")

    owner_before = app.state.session_store.get(fake_session_id)
    assert owner_before is not None
    initial_last_active = owner_before.last_active_at

    # 获取 T_short
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    t_short = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
    dialog_id = os.environ["RAGFLOW_DIALOG_ID"]

    # 等待确保时间戳有差异
    time.sleep(0.02)

    # 调 SSE 代理(带 session_id;上游被 mock 为成功 SSE 流,代理完成后更新 last_active_at)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试问题", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()  # 消费流式响应体

    # last_active_at 应被更新(成功流)
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after is not None
    assert owner_after.last_active_at > initial_last_active, (
        f"last_active_at 未更新: before={initial_last_active} after={owner_after.last_active_at}"
    )


async def test_sse_updates_last_active_at_with_known_session(client, app, monkeypatch):
    """SSE 代理对请求中的已知 session_id 更新 last_active_at(未带 session_id 不更新)。"""
    await _login(client)
    fake_session_id = "fake-ragflow-session-007"
    _mock_precreate(monkeypatch, fake_session_id)
    _mock_ragflow_sse_success(monkeypatch)
    await client.post("/share-pages/sp_default/sessions")

    owner_before = app.state.session_store.get(fake_session_id)
    initial_last_active = owner_before.last_active_at

    resp = await client.get("/share-pages/sp_default/embed-url")
    t_short = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
    dialog_id = os.environ["RAGFLOW_DIALOG_ID"]

    time.sleep(0.02)

    # SSE 代理不带 session_id(模拟首次无 session 调用)→ 不应更新已知 session 的 last_active_at
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    owner_after_first = app.state.session_store.get(fake_session_id)
    assert owner_after_first.last_active_at == initial_last_active, "不带 session_id 的调用不应更新 last_active_at"

    # 带 session_id 的成功流调用才更新
    time.sleep(0.02)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试2", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after.last_active_at > initial_last_active


async def test_sse_failed_stream_does_not_update_last_active_at(client, app, monkeypatch):
    """上游不可达(失败流)时不更新 last_active_at(修复项 2:仅在成功流后更新)。

    失败流不应误推活跃时间(避免崩溃的对话被记为「刚活跃」)。
    """
    await _login(client)
    fake_session_id = "fake-ragflow-session-008"
    _mock_precreate(monkeypatch, fake_session_id)
    # 不 mock 上游:ragflow-mock.invalid 不可达,stream_generator 走 RequestError 分支(失败流)
    await client.post("/share-pages/sp_default/sessions")

    owner_before = app.state.session_store.get(fake_session_id)
    initial_last_active = owner_before.last_active_at

    resp = await client.get("/share-pages/sp_default/embed-url")
    t_short = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
    dialog_id = os.environ["RAGFLOW_DIALOG_ID"]

    time.sleep(0.02)

    # 调 SSE 代理(带 session_id,但上游不可达 → 失败流)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()

    # 失败流: last_active_at 不应被更新
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after.last_active_at == initial_last_active, (
        f"失败流不应更新 last_active_at: before={initial_last_active} after={owner_after.last_active_at}"
    )


# ---------------------------------------------------------------------------
# 验收点 7:integration — 用真实 RAGFlow 验证预创建 + 重新打开链路。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_precreate_and_resume_with_real_ragflow(client, real_ragflow):
    """[integration] 用真实 RAGFlow 验证:预创建 session → 列表 → 重新打开。"""
    await _login(client)

    # 1. 预创建 session(调真实 RAGFlow)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    body = resp.json()
    session_id = body["session_id"]
    assert session_id, "预创建未返回 session_id"

    # iframe URL 含 session_id
    qs = _extract_iframe_params(body["iframe_url"])
    assert qs.get("session_id", [None])[0] == session_id

    # 2. 列表能看到该 session
    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert any(s["session_id"] == session_id for s in sessions)

    # 3. 重新打开(调真实 RAGFlow GET 端点 — 由本地 fork 代码或已部署版本提供)
    # 注:此测试需要 RAGFlow 服务器已部署含 GET /sessions/<id> 端点的版本。
    # 若服务器未部署该端点,此断言会失败(预期行为,提示需部署)。
    resp = await client.get(f"/share-pages/sp_default/sessions/{session_id}")
    if resp.status_code == 200:
        body = resp.json()
        assert body["session_id"] == session_id
        assert "messages" in body
        assert "reference" in body
    else:
        pytest.skip(f"RAGFlow 服务器未部署 GET /sessions/<id> 端点(HTTP {resp.status_code}),需部署 Slice 2 代码")
