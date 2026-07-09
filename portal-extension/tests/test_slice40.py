"""Slice 40 测试 — 会话列表最新在上 + 首问作为标题双侧同步。

覆盖验收点(ISSUES.md Issue 40):
  问题 1:list_for_user 按 last_active_at 倒序(最近活跃在最上)。
  问题 2:首次 bind 后用首问内容(截断 50 字符,取首行)调
         session_store.rename + rename_session_via_ragflow 双侧同步;
         已存在 session 不改 title(保护手动重命名);
         RAGFlow 失败只 warning,不抛 502(SSE 已成功)。
"""
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def _get_t_short(client, share_page_id="sp_default"):
    """辅助:登录后调 embed-url 取 T_short。"""
    await _login(client)
    resp = await client.get(f"/share-pages/{share_page_id}/embed-url")
    assert resp.status_code == 200, f"embed-url 失败: {resp.text}"
    iframe_url = resp.json()["iframe_url"]
    qs = parse_qs(urlparse(iframe_url).query)
    return qs["auth"][0], qs["shared_id"][0]


def _mock_ragflow_sse_with_session_id(monkeypatch, session_id: str, message_id: str = "msg-001"):
    """辅助:mock 网关的 httpx.AsyncClient,让上游 SSE 返回含指定 session_id 的成功流。"""

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            payload = (
                f'{{"answer":"流式","session_id":"{session_id}","id":"{message_id}","final":true}}'
            )
            sse_body = f"data: {payload}\n\n".encode("utf-8")
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(
                    200, content=sse_body, headers={"content-type": "text/event-stream"}
                )
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


# ---------------------------------------------------------------------------
# 问题 1:list_for_user 按 last_active_at 倒序
# ---------------------------------------------------------------------------


def test_list_for_user_orders_by_last_active_at_desc(app):
    """list_for_user 返回结果按 last_active_at 倒序(最近活跃在最上)。"""
    store = app.state.session_store
    # 三个 session,bind 时间顺序:s1 → s2 → s3,但人为把 s2 的 last_active_at 推到最大
    store.bind("s1", "sp", "u1", "d1", "t1")
    store.bind("s2", "sp", "u1", "d1", "t2")
    store.bind("s3", "sp", "u1", "d1", "t3")

    # 推 s2 的 last_active_at 到最大(模拟最近活跃)
    time.sleep(0.01)
    assert store.update_last_active("s2") is True

    sessions = store.list_for_user("u1", "sp")
    ids = [s.session_id for s in sessions]
    assert ids == ["s2", "s3", "s1"], (
        f"list_for_user 应按 last_active_at 倒序(s2 最近活跃在最上),实际: {ids}"
    )


def test_list_for_user_orders_by_last_active_at_desc_multiple_users(app):
    """跨用户隔离下,各用户列表仍按 last_active_at 倒序。"""
    store = app.state.session_store
    store.bind("a1", "sp", "u1", "d1", "t1")
    store.bind("a2", "sp", "u1", "d1", "t2")
    store.bind("b1", "sp", "u2", "d1", "t3")
    store.bind("b2", "sp", "u2", "d1", "t4")

    time.sleep(0.01)
    store.update_last_active("a1")  # u1 的 a1 最近活跃
    store.update_last_active("b2")  # u2 的 b2 最近活跃(已经是最新的,但显式推一次)

    u1_sessions = [s.session_id for s in store.list_for_user("u1", "sp")]
    u2_sessions = [s.session_id for s in store.list_for_user("u2", "sp")]
    assert u1_sessions == ["a1", "a2"], f"u1 列表应按 last_active_at 倒序,实际: {u1_sessions}"
    assert u2_sessions == ["b2", "b1"], f"u2 列表应按 last_active_at 倒序,实际: {u2_sessions}"


# ---------------------------------------------------------------------------
# 问题 2:首次 bind 后用首问作为标题(双侧同步)
# ---------------------------------------------------------------------------


async def test_sse_bind_uses_first_question_as_title(client, app, monkeypatch):
    """首次 bind 后,session_store.rename 与 rename_session_via_ragflow 被调用,title 是首问截断。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice40-first-bind-title-001"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-001")

    # mock rename_session_via_ragflow(避免真实 HTTP 调用)
    mock_rename = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.gateway.rename_session_via_ragflow", mock_rename)

    # 真实首问(无 session_id,RAGFlow 在 SSE 中返回 session_id)
    question = "如何配置 OAuth2 单点登录?"
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": question, "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200, f"真实首问应 200: {resp.text}"

    # 验证 session 已绑定 + title 被改为首问
    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None, "首次 bind 后应绑定 session"
    assert owner.title == question, f"title 应为首问内容,实际: {owner.title}"

    # 验证 RAGFlow 侧也被调用(双侧同步)
    mock_rename.assert_awaited_once()
    call_args = mock_rename.call_args
    # call_args: (settings, dialog_id, session_id, name, ragflow_type)
    assert call_args.args[1] == dialog_id, "rename 应传 dialog_id"
    assert call_args.args[2] == ragflow_session_id, "rename 应传 session_id"
    assert call_args.args[3] == question, f"rename 应传 title=首问,实际: {call_args.args[3]}"
    assert call_args.args[4] == "chat", "rename 应传 ragflow_type=chat"


async def test_sse_bind_truncates_long_first_question(client, app, monkeypatch):
    """首问超 50 字符时,title 截断到 50 字符 + 省略号。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice40-truncate-title-002"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-002")
    mock_rename = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.gateway.rename_session_via_ragflow", mock_rename)

    long_question = "请详细说明如何在多租户 SaaS 应用中实现细粒度的基于角色的访问控制 RBAC," \
                    "包括权限继承、角色层级、资源粒度隔离以及与 OIDC 集成的最佳实践?"
    assert len(long_question) > 50
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": long_question, "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None
    expected_title = long_question[:50] + "..."
    assert owner.title == expected_title, (
        f"长首问应截断到 50 字符 + 省略号,实际: {owner.title!r}"
    )
    mock_rename.assert_awaited_once()
    assert mock_rename.call_args.args[3] == expected_title


async def test_sse_bind_uses_first_line_of_multiline_question(client, app, monkeypatch):
    """首问含换行时,title 取首行(不截断到换行处之外)。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice40-multiline-title-003"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-003")
    mock_rename = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.gateway.rename_session_via_ragflow", mock_rename)

    multiline_question = "这是首行标题\n这是第二行详细说明\n还有第三行"
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": multiline_question, "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None
    assert owner.title == "这是首行标题", f"多行首问应取首行,实际: {owner.title!r}"
    mock_rename.assert_awaited_once()


async def test_sse_existing_session_not_renamed(client, app, monkeypatch):
    """已存在 session 走 else 分支(只 update_last_active),不调 rename(保护手动重命名)。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice40-existing-not-renamed-004"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-004")
    mock_rename = AsyncMock(return_value=None)
    monkeypatch.setattr("portal.gateway.rename_session_via_ragflow", mock_rename)

    # 预先绑定 session 并设置自定义 title(模拟用户手动重命名)
    app.state.session_store.bind(
        session_id=ragflow_session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
        title="我手动命名的标题",
    )

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "这是新问题", "stream": True, "quote": True, "session_id": ragflow_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    assert resp.status_code == 200

    # 已存在 session 不应被 rename(保护手动重命名)
    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None
    assert owner.title == "我手动命名的标题", (
        f"已存在 session 的 title 不应被覆盖,实际: {owner.title!r}"
    )
    mock_rename.assert_not_awaited()


async def test_sse_title_sync_ragflow_failure_does_not_break_stream(client, app, monkeypatch):
    """RAGFlow rename 失败只 warning,不抛 502(SSE 已成功);portal 侧 title 仍被更新。"""
    t_short, dialog_id = await _get_t_short(client)
    ragflow_session_id = "slice40-ragflow-fail-005"
    _mock_ragflow_sse_with_session_id(monkeypatch, ragflow_session_id, message_id="msg-005")
    # mock RAGFlow rename 失败(抛异常)
    monkeypatch.setattr(
        "portal.gateway.rename_session_via_ragflow",
        AsyncMock(side_effect=RuntimeError("RAGFlow 网络抖动")),
    )

    question = "首问内容"
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": question, "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()
    # SSE 已成功 → 200(不应因 title 同步失败让用户看到错误)
    assert resp.status_code == 200, f"RAGFlow rename 失败不应破坏 SSE 流: {resp.text}"

    # portal 侧 title 已更新(rename 在 try 块内先于 RAGFlow 调用)
    owner = app.state.session_store.get(ragflow_session_id)
    assert owner is not None
    assert owner.title == question, (
        f"portal 侧 title 应已更新(RAGFlow 失败不应回滚 portal 侧),实际: {owner.title!r}"
    )


# ---------------------------------------------------------------------------
# 单元测试:_derive_title_from_question 截断逻辑
# ---------------------------------------------------------------------------


def test_derive_title_from_question_empty():
    """空字符串/None → 返回空。"""
    from portal.gateway import _derive_title_from_question

    assert _derive_title_from_question("") == ""
    assert _derive_title_from_question(None) == ""


def test_derive_title_from_question_short():
    """短问题原样返回。"""
    from portal.gateway import _derive_title_from_question

    assert _derive_title_from_question("如何重置密码?") == "如何重置密码?"


def test_derive_title_from_question_long():
    """超 50 字符 → 截断到 50 + 省略号。"""
    from portal.gateway import _derive_title_from_question

    long_q = "a" * 100
    title = _derive_title_from_question(long_q)
    assert title == "a" * 50 + "..."
    assert len(title) == 53  # 50 + 3 个点


def test_derive_title_from_question_exactly_50():
    """恰好 50 字符 → 不截断(无省略号)。"""
    from portal.gateway import _derive_title_from_question

    q = "a" * 50
    title = _derive_title_from_question(q)
    assert title == q
    assert "..." not in title


def test_derive_title_from_question_multiline():
    """多行问题取首行(不截断到换行处之外)。"""
    from portal.gateway import _derive_title_from_question

    q = "首行标题\n第二行\n第三行"
    assert _derive_title_from_question(q) == "首行标题"


def test_derive_title_from_question_multiline_long():
    """多行 + 首行超长 → 取首行后截断。"""
    from portal.gateway import _derive_title_from_question

    q = "a" * 60 + "\n第二行"
    title = _derive_title_from_question(q)
    assert title == "a" * 50 + "..."


def test_derive_title_from_question_strips_whitespace():
    """首行首尾空白被 strip。"""
    from portal.gateway import _derive_title_from_question

    assert _derive_title_from_question("  带空格的标题  ") == "带空格的标题"
    # 首行含尾部空白也被 strip
    assert _derive_title_from_question("带空格的标题   ") == "带空格的标题"
    # 以换行开头 → 首行为空 → 返回空(spec:split("\n",1)[0] 取首行)
    assert _derive_title_from_question("\n第二行") == ""


def test_derive_title_from_question_custom_max_len():
    """支持自定义 max_len。"""
    from portal.gateway import _derive_title_from_question

    q = "a" * 20
    assert _derive_title_from_question(q, max_len=10) == "a" * 10 + "..."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
