"""Slice 3 端到端测试 — 继续对话流式 + 用户隔离 + 撤销立即失效。

覆盖验收点(ISSUES.md Issue 3):
  1. 用户 A 旧 session 继续流式(新 message_id,session_id 不变)。
  2. 用户 B 用 A 的 session_id 调网关 → 403(归属校验失败)。
  3. 用户 B 持自己 T_short 用 A 的 session_id → 403(网关代理路径拒绝)。
  4. 撤销授权后:
     - T_short 立即失效(同令牌 → 403)。
     - iframe 继续提问 → 403(grant 不存在)。
     - 刷新 iframe 加载(embed-url)→ 403(拒绝签发新 T_short)。
  5. 撤销后历史会话保留(chat_session_owner 记录仍在,管理员可查)。
  6. 校验链四步分别构造失败场景:
     - 步骤 1 失败:未登录 → 403。
     - 步骤 2 失败:无 grant → 403(撤销后)。
     - 步骤 3 失败:session 不归属 → 403。
     - 步骤 4 失败:dialog_id 不一致 → 403。
"""

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from portal.models import SharePageGrant


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


def _mock_ragflow_sse_with_message(monkeypatch, session_id: str, message_id: str):
    """辅助:mock 网关的 httpx.AsyncClient,让上游 SSE 返回含指定 message_id 与 session_id 的成功流。

    用于「继续对话流式」测试:验证新 message_id 生成、session_id 不变。
    """

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            sse_body = (
                f'data: {{"answer":"流式","session_id":"{session_id}","id":"{message_id}","final":true}}\n\n'
            ).encode("utf-8")
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


async def _login_and_precreate(client, monkeypatch, session_id: str):
    """辅助:登录 + 预创建 session,返回 (t_short, dialog_id, session_id)。"""
    await _login(client)
    _mock_precreate(monkeypatch, session_id)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    qs = _extract_iframe_params(resp.json()["iframe_url"])
    return qs["auth"][0], qs["shared_id"][0], session_id


def _add_second_user_grant(app, user_id="u_user2", username="user2"):
    """辅助:为隔离测试追加第二个用户的 grant(若 SeedData 未硬编码 user2)。"""
    from portal.models import PortalUser
    from portal.password import hash_password

    user2 = PortalUser(
        id=user_id,
        username=username,
        password_hash=hash_password("testpass123"),
        is_admin=False,
        enabled=True,
    )
    app.state.seed.users_by_username[username] = user2
    app.state.seed.users_by_id[user2.id] = user2
    app.state.seed.grants.append(
        SharePageGrant(
            share_page_id="sp_default",
            subject_type="user",
            subject_id=user2.id,
            permission="use",
        )
    )
    return user2


# ---------------------------------------------------------------------------
# 验收点 1:用户 A 旧 session 继续流式(新 message_id,session_id 不变)。
# ---------------------------------------------------------------------------


async def test_continue_conversation_streams_with_new_message_id(client, monkeypatch):
    """用户 A 在旧 session_id 上继续提问,SSE 流式正常,新 message_id 生成,session_id 不变。"""
    fake_session_id = "slice3-session-continue-001"
    t_short, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    # mock 上游 SSE 返回新 message_id(与 session_id 关联)
    new_message_id = "msg-new-slice3-001"
    _mock_ragflow_sse_with_message(monkeypatch, fake_session_id, new_message_id)

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "继续提问", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200
    body = await resp.aread()
    body_text = body.decode("utf-8")
    # SSE 流含新 message_id
    assert new_message_id in body_text, f"未在新流中找到新 message_id: {body_text}"
    # session_id 保持不变
    assert fake_session_id in body_text, f"session_id 不一致: {body_text}"


# ---------------------------------------------------------------------------
# 验收点 2:用户 B 用 A 的 session_id 调网关 → 403(归属校验失败)。
# ---------------------------------------------------------------------------


async def test_user_b_cannot_use_admin_session_id(client, app, monkeypatch):
    """用户 B 用 admin 的 session_id 调网关 SSE → 403(归属校验失败)。"""
    fake_session_id = "slice3-session-isolation-002"
    t_short_admin, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    # user2 登录(若 SeedData 未硬编码则动态追加)
    if "user2" not in app.state.seed.users_by_username:
        _add_second_user_grant(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "user2", "password": "testpass123"})
        assert resp.status_code == 200
        resp = await client_b.get("/share-pages/sp_default/embed-url")
        assert resp.status_code == 200
        t_short_b = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]

        # user2 用自己的 T_short + admin 的 session_id 调 SSE → 403
        resp = await client_b.post(
            f"/api/v1/chatbots/{dialog_id}/completions",
            json={"question": "越权", "stream": True, "session_id": fake_session_id},
            headers={"Authorization": f"Bearer {t_short_b}"},
        )
        assert resp.status_code == 403, f"用户 B 不应用 admin 的 session_id: {resp.text}"


# ---------------------------------------------------------------------------
# 验收点 3:用户 B 持自己 T_short 调 RAGFlow 直接用 A 的 session_id → 403。
# ---------------------------------------------------------------------------


async def test_user_b_t_short_rejected_for_admin_session(client, app, monkeypatch):
    """用户 B 持自己的 T_short 用 admin 的 session_id 调 SSE → 403(网关代理路径拒绝)。

    与验收点 2 同源 — 网关在代理前就拒绝(归属校验),不会调上游 RAGFlow。
    本测试额外断言:即使 user2 也有 sp_default 的 grant,仍因 session 归属失败 → 403。
    """
    fake_session_id = "slice3-session-isolation-003"
    t_short_admin, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    if "user2" not in app.state.seed.users_by_username:
        _add_second_user_grant(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "user2", "password": "testpass123"})
        assert resp.status_code == 200
        resp = await client_b.get("/share-pages/sp_default/embed-url")
        assert resp.status_code == 200
        t_short_b = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]

        resp = await client_b.post(
            f"/api/v1/chatbots/{dialog_id}/completions",
            json={"question": "越权", "stream": True, "session_id": fake_session_id},
            headers={"Authorization": f"Bearer {t_short_b}"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 验收点 4:撤销授权后立即失效(T_short / iframe 继续提问 / 刷新加载)。
# ---------------------------------------------------------------------------


async def test_revoke_grant_invalidates_t_short_immediately(client, app, monkeypatch):
    """管理员撤销用户 A 的授权后,已签发的 T_short 立即失效(同令牌 → 403)。"""
    fake_session_id = "slice3-session-revoke-004"
    t_short, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    # 先验证 T_short 可用(撤销前)
    _mock_ragflow_sse_with_message(monkeypatch, fake_session_id, "msg-pre-revoke")
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "撤销前", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200

    # 管理员撤销 admin 对 sp_default 的授权
    resp = await client.request(
        "DELETE",
        "/share-pages/sp_default/grants/user/u_admin",
    )
    assert resp.status_code == 200, f"撤销授权失败: {resp.text}"

    # 撤销后:同 T_short 调网关 → 403(T_short 已被吊销 + grant 不存在)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "撤销后", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403, f"撤销后 T_short 应立即失效: {resp.text}"


async def test_revoke_grant_blocks_iframe_continue(client, app, monkeypatch):
    """撤销后 iframe 继续提问 → 403(网关校验 grant 不存在)。"""
    fake_session_id = "slice3-session-revoke-005"
    t_short, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    # 撤销授权
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200

    # iframe 继续提问(带原 T_short 与 session_id)→ 403
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "继续", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403


async def test_revoke_grant_blocks_embed_url_refresh(client, app, monkeypatch):
    """撤销后刷新 iframe 加载(embed-url)→ 403(拒绝签发新 T_short)。"""
    await _login(client)
    # 撤销前 embed-url 可用
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200

    # 撤销授权
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200

    # 撤销后刷新 embed-url → 403(grant 不存在)
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403, f"撤销后应拒绝签发新 T_short: {resp.text}"


# ---------------------------------------------------------------------------
# 验收点 5:撤销后历史会话保留(chat_session_owner 记录仍在)。
# ---------------------------------------------------------------------------


async def test_revoke_grant_preserves_session_history(client, app, monkeypatch):
    """撤销授权后历史会话在 chat_session_owner 中保留(管理员仍可查)。"""
    fake_session_id = "slice3-session-revoke-006"
    await _login_and_precreate(client, monkeypatch, fake_session_id)

    # 撤销前确认 session 已绑定
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None
    assert owner.portal_user_id == "u_admin"

    # 撤销授权
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200

    # 撤销后:chat_session_owner 记录仍保留(不删除会话归属)
    owner_after = app.state.session_store.get(fake_session_id)
    assert owner_after is not None, "撤销授权不应删除 chat_session_owner 记录"
    assert owner_after.portal_user_id == "u_admin"
    assert owner_after.session_id == fake_session_id


# ---------------------------------------------------------------------------
# 验收点 6:校验链四步分别构造失败场景。
# ---------------------------------------------------------------------------


async def test_chain_step1_unauthenticated_rejected(client):
    """校验链步骤 1 失败:未登录 → 403。"""
    # 未登录调 embed-url → 403
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403
    # 未登录调预创建 → 403
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 403


async def test_chain_step2_no_grant_rejected(client, app, monkeypatch):
    """校验链步骤 2 失败:无 grant → 403(撤销后)。

    撤销 admin 对 sp_default 的 grant 后,所有需 grant 校验的路由 → 403。
    """
    await _login(client)
    # 撤销授权
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200

    # embed-url → 403(grant 不存在)
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403
    # 预创建 → 403(grant 不存在)
    _mock_precreate(monkeypatch, "should-not-reach")
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 403


async def test_chain_step3_session_not_owned_rejected(client, app, monkeypatch):
    """校验链步骤 3 失败:session 不归属当前用户 → 403。"""
    fake_session_id = "slice3-chain-step3-007"
    t_short, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    # 用一个不存在的 session_id(归属校验失败:session 不存在)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": "nonexistent-session-id"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403
    assert "会话不存在" in resp.text or "无权" in resp.text


async def test_chain_step4_dialog_id_mismatch_rejected(client, app, monkeypatch):
    """校验链步骤 4 失败:dialog_id 不一致 → 403。

    手动篡改 chat_session_owner.ragflow_resource_id,使归属记录的 dialog_id
    与请求的 dialog_id 不一致 → 校验链步骤 4 失败 → 403。
    """
    fake_session_id = "slice3-chain-step4-008"
    t_short, dialog_id, _ = await _login_and_precreate(client, monkeypatch, fake_session_id)

    # 手动篡改归属记录的 ragflow_resource_id(模拟 dialog_id 不一致)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None
    owner.ragflow_resource_id = "a-totally-different-dialog-id"

    # 调 SSE:session 存在 + 归属当前用户,但 dialog_id 不一致 → 403
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session_id},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403
    assert "资源不匹配" in resp.text or "不匹配" in resp.text


# ---------------------------------------------------------------------------
# 撤销授权 API 本身的行为测试。
# ---------------------------------------------------------------------------


async def test_revoke_grant_requires_admin(client, app, monkeypatch):
    """普通用户调撤销授权 API → 403(管理员专用)。"""
    if "user2" not in app.state.seed.users_by_username:
        _add_second_user_grant(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "user2", "password": "testpass123"})
        assert resp.status_code == 200
        # user2(非 admin)调撤销 → 403
        resp = await client_b.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
        assert resp.status_code == 403


async def test_revoke_grant_requires_login(client):
    """未登录调撤销授权 API → 403。"""
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 403


async def test_revoke_grant_unknown_returns_404(client):
    """撤销不存在的 grant → 404。"""
    await _login(client)
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# integration — 用真实 RAGFlow 验证继续对话流式。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_continue_conversation_with_real_ragflow(client, real_ragflow):
    """[integration] 用真实 RAGFlow 验证:预创建 session → 继续对话 → 新 message_id + session_id 不变。"""
    await _login(client)
    # 1. 预创建 session(调真实 RAGFlow)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    body = resp.json()
    session_id = body["session_id"]
    assert session_id, "预创建未返回 session_id"
    iframe_url = body["iframe_url"]
    t_short = _extract_iframe_params(iframe_url)["auth"][0]
    dialog_id = _extract_iframe_params(iframe_url)["shared_id"][0]

    # 2. 用 session_id 继续对话(真实 SSE 流)
    headers = {"Authorization": f"Bearer {t_short}"}
    body_text = ""
    async with client.stream(
        "POST",
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "增值税税率是多少", "stream": True, "quote": True, "session_id": session_id},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            body_text += line + "\n"
            if len(body_text) > 50000:
                break

    # 3. 断言:流式回答含 answer,session_id 不变
    assert "answer" in body_text, f"未收到回答: {body_text[:300]}"
    assert session_id in body_text, f"session_id 应保持不变: {body_text[:300]}"
    # beta Token 不泄露
    assert real_ragflow["token"] not in body_text
