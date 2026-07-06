"""Slice 7 — 端到端测试固化(8 个原型验收点统一回归套件)。

把 PRD Testing Decisions 列出的 8 个原型验收点(prototype/NOTES.md 已验证)
固化为统一的、可独立运行的回归测试套件。所有用例使用 mock(不依赖真实 RAGFlow),
保证 CI 可重复运行;integration 测试保留在各 slice 测试文件中,本文件不重复。

运行方式:
  uv run pytest tests/test_e2e_regression.py -v

8 个验收点(ISSUES.md Issue 7 / handoff 文档):
  AC1 登录受保护 — 未登录访问分享页 → 重定向/拒绝(403)。
  AC2 真实 Token 不暴露 — iframe URL 与 SSE 请求中不含 RAGFlow beta Token,
      只含 T_short。
  AC3 session_id 绑定 — 用户打开分享页后,chat_session_owner 有记录且归属当前用户。
  AC4 重开恢复 — 关闭后重新打开 session,能恢复消息正文。
  AC5 引用完整 — 恢复时引用片段(chunks)、引用标记、文档定位(doc_aggs 的
      document_id)完整。
  AC6 继续流式 — 在旧 session_id 上继续提问,SSE 流式正常,新 message_id 生成,
      session_id 不变。
  AC7 用户隔离 — 用户 B 不能访问 A 的 session(403)。
  AC8 撤销立即失效 — 撤销授权后,已签发 T_short 立即失效,iframe 继续提问 403,
      刷新加载 403。

策略:
  - 不重复造轮子:复用 conftest.py 的统一 mock helper fixture(mock_precreate /
    mock_ragflow_sse / mock_fetch_history / mock_delete_session)。
  - 每个验收点至少有一个明确的、用 mock 的、不跳过的测试(不依赖 integration 标记)。
  - 不修改 portal 业务代码;若测试发现 bug,记录但不在本 slice 修。
  - integration 测试保留在各 slice 文件中,本文件不重复。

Slice 7 review 修复(本文件增量):
  - AC1 加正向用例(登录成功 + 获得分享页权限)。
  - 补 5 项额外分支测试(双删失败标记 / 禁用用户保留会话 / 级联删除无孤儿 /
    elevated 审计 / 8 类审计全记录)。
  - 抽 _login_and_precreate helper 消除 AC3/4/5/8 三段式重复。
  - AC2 测试加注释说明验证范围(客户端侧不暴露 beta token)。
"""

import os
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# 公共辅助(本文件内复用,避免在 8 个验收点测试中重复登录/取 T_short 代码)
# ---------------------------------------------------------------------------


def _extract_iframe_params(url: str) -> dict:
    """从 iframe URL 提取 query 参数(消除 urlparse + parse_qs 重复调用)。"""
    return parse_qs(urlparse(url).query)


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def _login_and_precreate(client, mock_precreate, fake_session: str = "sess_xxx"):
    """辅助:登录 + 预创建 session,返回 precreate 响应(消除 AC3/4/5/8 三段式重复)。

    与 ``_login_precreate_and_get_t_short`` 的区别:不取 embed-url/T_short,
    仅完成「登录 + 预创建」前置,断言 200 后返回响应供调用方做后续断言。
    """
    await _login(client)
    mock_precreate(fake_session)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    return resp


async def _login_precreate_and_get_t_short(client, mock_precreate, session_id: str):
    """辅助:登录 + 预创建 session + 取 T_short,返回 (t_short, dialog_id, session_id)。

    AC2/AC3/AC4/AC5/AC6/AC7/AC8 都需要这套前置流程,提取为单一辅助减少重复。
    """
    await _login(client)
    mock_precreate(session_id)
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200, f"取 embed-url 失败: {resp.text}"
    qs = _extract_iframe_params(resp.json()["iframe_url"])
    return qs["auth"][0], qs["shared_id"][0], session_id


async def _create_user_and_grant(client, username="alice"):
    """辅助:管理员创建普通用户并授予 sp_default use 权限,返回用户 dict。

    Slice 7 额外分支测试(禁用用户/级联删除)需要可登录的普通用户,
    复用 slice5/6 已验证的模式。
    """
    resp = await client.post(
        "/admin/users",
        json={"username": username, "email": f"{username}@example.com", "password": "alicepass123"},
    )
    assert resp.status_code == 201, f"创建用户失败: {resp.text}"
    user = resp.json()
    resp = await client.post(
        "/admin/share-pages/sp_default/grants",
        json={"subject_type": "user", "subject_id": user["id"], "permission": "use"},
    )
    assert resp.status_code == 201, f"授权失败: {resp.text}"
    return user


# ===========================================================================
# AC1 登录受保护 — 未登录访问分享页 → 重定向/拒绝(403)。
# ===========================================================================


async def test_ac1_unauthenticated_access_rejected(client):
    """AC1:未登录 GET /share-pages/{id}/embed-url → 403(不签发 T_short)。

    PRD D3:仅登录用户,无公开分享。get_current_user 依赖未带 session cookie → 403。
    """
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403, f"未登录应被拒绝,实际: {resp.status_code}"


async def test_ac1_unauthenticated_precreate_rejected(client):
    """AC1:未登录 POST /share-pages/{id}/sessions → 403(不预创建 session)。"""
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 403


async def test_ac1_unauthenticated_list_sessions_rejected(client):
    """AC1:未登录 GET /share-pages/{id}/sessions → 403。"""
    resp = await client.get("/share-pages/sp_default/sessions")
    assert resp.status_code == 403


async def test_ac1_authenticated_access_granted(client, mock_precreate):
    """AC1 正向用例:登录后 GET /share-pages/{id}/embed-url → 200,返回含 iframe URL。

    与未登录被拒(403)的负向用例互补,验证「登录成功 + 获得分享页权限」后
    网关正常签发 T_short 并构造 iframe URL。
    """
    await _login(client)
    mock_precreate("ac1-session-001")
    await client.post("/share-pages/sp_default/sessions")

    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200, f"登录后应能访问 embed-url: {resp.text}"
    body = resp.json()
    iframe_url = body["iframe_url"]
    assert iframe_url, "iframe_url 不能为空"

    # iframe URL 必须含 auth(T_short)与 shared_id,证明用户被授权访问该分享页
    qs = _extract_iframe_params(iframe_url)
    assert qs.get("auth"), "登录后 iframe URL 应含 auth=T_short"
    assert qs.get("shared_id"), "登录后 iframe URL 应含 shared_id"


# ===========================================================================
# AC2 真实 Token 不暴露 — iframe URL 与 SSE 请求中不含 beta Token,只含 T_short。
# ===========================================================================
#
# 验证范围说明(Slice 7 review 修复):
#   AC2 验证「客户端侧不暴露 beta token」,具体覆盖两条路径:
#     1. iframe URL 含 auth=T_short,不含 beta token(test_ac2_iframe_url_*)。
#     2. 客户端→网关 SSE 请求的 Authorization 头是 T_short,不是 beta token
#        (test_ac2_sse_request_*),客户端收到的 SSE body 是网关透传的上游流,
#        网关→RAGFlow 上游请求的 beta token 只在服务端这一跳出现,不暴露给客户端
#        (网关代理本身保证不透传 Authorization 头给客户端响应)。
#   注:网关 proxy_sse_to_ragflow 流式透传上游 SSE body,不透传上游 response header;
#       beta token 仅在 _build_upstream_headers(网关→RAGFlow)出现,客户端响应无。


async def test_ac2_iframe_url_contains_no_beta_token(client, mock_precreate):
    """AC2:登录后 GET embed-url,iframe URL 含 auth=T_short,不含真实 beta Token。

    对应原型验收点 2(网关不给浏览器暴露 RAGFlow 真正的 API Token)。
    """
    await _login(client)
    mock_precreate("ac2-session-001")
    # 预创建后取 embed-url(确保 iframe URL 也含 session_id,但不暴露 beta Token)
    await client.post("/share-pages/sp_default/sessions")

    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    body = resp.json()
    iframe_url = body["iframe_url"]

    # 必须含 auth 参数(即 T_short)
    qs = _extract_iframe_params(iframe_url)
    t_short = qs.get("auth", [None])[0]
    assert t_short, "auth 参数(T_short)不能为空"

    # 真实 beta Token 绝不出现在 iframe URL 或响应体中
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert beta_token not in iframe_url, "真实 beta Token 泄露到 iframe URL"
    assert beta_token not in resp.text, "真实 beta Token 泄露到响应体"


async def test_ac2_sse_request_contains_no_beta_token(client, app, mock_precreate, mock_ragflow_sse):
    """AC2:调 SSE 代理(mock 上游)后,客户端响应中不含 beta Token,只含 T_short。

    模拟 iframe 内 RAGFlow 前端发起 SSE:Authorization 头带 T_short,
    网关用 beta Token 调上游(mock 拦截,不真连 RAGFlow),流式回传。
    断言:客户端看到的 SSE 响应中不含 beta Token(只在网关→上游这一跳出现);
          客户端→网关请求的 Authorization 用 T_short(非 beta token)。
    """
    fake_session = "ac2-session-002"
    t_short, dialog_id, _ = await _login_precreate_and_get_t_short(client, mock_precreate, fake_session)
    # mock 上游 SSE 返回成功流(客户端只会看到该 mock 内容)
    mock_ragflow_sse(session_id=fake_session, message_id="ac2-msg-001")

    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    # 客户端→网关请求的 Authorization 用 T_short(网关签发的临时令牌),非 beta token
    assert t_short != beta_token, "T_short 不应等于 beta token(网关签发的临时令牌与上游凭据不同)"

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "session_id": fake_session},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200
    body = await resp.aread()
    body_text = body.decode("utf-8")

    # 客户端 SSE 响应中绝不含 beta Token(网关不透传上游 Authorization 头)
    assert beta_token not in body_text, "beta Token 泄露到 SSE 客户端响应"
    # 客户端 SSE 响应应含 T_short 走的链路产物(answer 字段由 mock 注入)
    assert "answer" in body_text, f"未收到 SSE 数据: {body_text[:300]}"


# ===========================================================================
# AC3 session_id 绑定 — 用户打开分享页后,chat_session_owner 有记录且归属当前用户。
# ===========================================================================


async def test_ac3_precreate_binds_session_to_user(client, app, mock_precreate):
    """AC3:预创建 session 后,chat_session_owner 有记录,portal_user_id 匹配当前用户。

    对应原型验收点 3(新建会话后捕获并绑定 session_id 到 chat_session_owner)。
    """
    fake_session = "ac3-session-001"
    resp = await _login_and_precreate(client, mock_precreate, fake_session)
    assert resp.json()["session_id"] == fake_session

    # chat_session_owner 有记录,归属 admin
    owner = app.state.session_store.get(fake_session)
    assert owner is not None, "chat_session_owner 未绑定"
    assert owner.portal_user_id == "u_admin", "session 未归属当前用户"
    assert owner.share_page_id == "sp_default"
    assert owner.ragflow_resource_id == os.environ["RAGFLOW_DIALOG_ID"]


# ===========================================================================
# AC4 重开恢复 — 关闭后重新打开 session,能恢复消息正文。
# ===========================================================================


async def test_ac4_resume_returns_messages(client, app, mock_precreate, mock_fetch_history):
    """AC4:预创建 + mock RAGFlow GET 返回 messages,恢复端点返回消息数组。

    对应原型验收点 4(关闭页面后从「我的会话」重新打开,用 session_id 重载)。
    """
    fake_session = "ac4-session-001"
    await _login_and_precreate(client, mock_precreate, fake_session)

    # mock RAGFlow GET 端点返回消息正文
    fake_history = {
        "session_id": fake_session,
        "dialog_id": os.environ["RAGFLOW_DIALOG_ID"],
        "name": "AC4 测试会话",
        "messages": [
            {"role": "assistant", "content": "你好,有什么可以帮你?", "id": "msg1"},
            {"role": "user", "content": "增值税税率是多少?", "id": "msg2"},
            {"role": "assistant", "content": "增值税税率为13%。", "id": "msg3"},
        ],
        "reference": [{}, {}, {}],
    }
    mock_fetch_history(fake_history)

    resp = await client.get(f"/share-pages/sp_default/sessions/{fake_session}")
    assert resp.status_code == 200, f"恢复失败: {resp.text}"
    body = resp.json()
    assert body["session_id"] == fake_session
    messages = body["messages"]
    assert len(messages) == 3, f"消息数不符: {len(messages)}"
    assert messages[0]["content"] == "你好,有什么可以帮你?"
    assert messages[2]["content"] == "增值税税率为13%。"


# ===========================================================================
# AC5 引用完整 — 恢复时引用片段(chunks)、引用标记、文档定位(doc_aggs 的 document_id)完整。
# ===========================================================================


async def test_ac5_resume_returns_references(client, app, mock_precreate, mock_fetch_history):
    """AC5:mock RAGFlow GET 返回 reference 含 chunks + doc_aggs,恢复端点返回完整引用。

    断言:
      - chunks 含 document_id、document_name、positions(PDF 页码定位);
      - doc_aggs 含 doc_id(可定位 PDF 预览,对应验收点 5)。
    """
    fake_session = "ac5-session-001"
    await _login_and_precreate(client, mock_precreate, fake_session)

    # 基于 NOTES.md H3 验证的真实 RAGFlow 响应结构构造 mock
    fake_history = {
        "session_id": fake_session,
        "dialog_id": os.environ["RAGFLOW_DIALOG_ID"],
        "name": "AC5 引用测试",
        "messages": [
            {"role": "user", "content": "增值税税率是多少?", "id": "msg1"},
            {"role": "assistant", "content": "增值税税率为13%。", "id": "msg2"},
        ],
        "reference": [
            {},
            {
                "chunks": [
                    {
                        "id": "chunk-ac5-001",
                        "content": "第十条 增值税税率:百分之十三",
                        "document_id": "doc-ac5-001",
                        "document_name": "增值税法.pdf",
                        "image_id": "img-ac5-001",
                        "positions": [[1, 123, 191, 450, 463]],
                        "similarity": 0.2671,
                    }
                ],
                "doc_aggs": [{"doc_name": "增值税法.pdf", "doc_id": "doc-ac5-001", "count": 1}],
            },
        ],
    }
    mock_fetch_history(fake_history)

    resp = await client.get(f"/share-pages/sp_default/sessions/{fake_session}")
    assert resp.status_code == 200
    body = resp.json()

    # 引用完整:chunks 含 document_id、document_name、positions
    last_ref = body["reference"][-1]
    assert len(last_ref["chunks"]) == 1, "chunks 数量不符"
    chunk = last_ref["chunks"][0]
    assert chunk["document_id"] == "doc-ac5-001", "chunk.document_id 缺失(无法定位文档)"
    assert chunk["document_name"] == "增值税法.pdf", "chunk.document_name 缺失"
    assert chunk["positions"][0][0] == 1, "positions 页码缺失(无法定位 PDF 预览)"

    # doc_aggs 含 doc_id(可定位 PDF 预览)
    assert len(last_ref["doc_aggs"]) == 1
    assert last_ref["doc_aggs"][0]["doc_id"] == "doc-ac5-001", "doc_aggs.doc_id 缺失"


# ===========================================================================
# AC6 继续流式 — 在旧 session_id 上继续提问,SSE 流式正常,新 message_id 生成,
#                session_id 不变。
# ===========================================================================


async def test_ac6_continue_session_streams_new_message(client, app, mock_precreate, mock_ragflow_sse):
    """AC6:mock RAGFlow SSE 返回新 message_id,session_id 不变;调 SSE 代理后验证流式响应。

    对应原型验收点 6(在旧 session_id 上继续提问,流式响应正常)。
    """
    fake_session = "ac6-session-001"
    t_short, dialog_id, _ = await _login_precreate_and_get_t_short(client, mock_precreate, fake_session)

    # mock 上游 SSE 返回新 message_id(与原 session_id 关联,模拟「继续对话」)
    new_message_id = "ac6-new-msg-001"
    mock_ragflow_sse(session_id=fake_session, message_id=new_message_id, answer="继续回答")

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "继续提问", "stream": True, "session_id": fake_session},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"SSE 代理失败: {resp.status_code}"
    body = await resp.aread()
    body_text = body.decode("utf-8")

    # SSE 流含新 message_id(证明是新消息而非旧消息回放)
    assert new_message_id in body_text, f"未在新流中找到新 message_id: {body_text[:300]}"
    # session_id 保持不变(继续在同一会话内)
    assert fake_session in body_text, f"session_id 不一致: {body_text[:300]}"


# ===========================================================================
# AC7 用户隔离 — 用户 B 不能访问 A 的 session(403)。
# ===========================================================================


async def test_ac7_user_b_cannot_access_user_a_session(client, app, mock_precreate, mock_fetch_history):
    """AC7:用户 A 预创建 session,用户 B 尝试恢复/继续 → 403。

    覆盖两条路径:
      1. 用户 B GET /share-pages/{id}/sessions/{A 的 session_id} → 403(恢复路径)。
      2. 用户 B 用自己的 T_short + A 的 session_id 调 SSE → 403(继续路径)。

    user2 已由 build_seed_data 硬编码(对 sp_default 有 use grant),无需动态追加。
    """
    fake_session = "ac7-session-001"
    # admin(A)登录并预创建 session
    await _login_and_precreate(client, mock_precreate, fake_session)

    # mock fetch_history(确保若隔离失败也不会因上游不可达误判)
    mock_fetch_history(
        {
            "session_id": fake_session,
            "messages": [{"role": "assistant", "content": "secret", "id": "m1"}],
            "reference": [],
        }
    )

    # user2(B)登录(独立 client,独立 cookie jar)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b:
        resp = await client_b.post("/login", json={"username": "user2", "password": "testpass123"})
        assert resp.status_code == 200

        # 路径 1:用户 B GET 恢复 admin 的 session → 403
        resp = await client_b.get(f"/share-pages/sp_default/sessions/{fake_session}")
        assert resp.status_code == 403, f"用户 B 不应能恢复 admin 的 session: {resp.text}"

        # 路径 2:用户 B 用自己的 T_short + admin 的 session_id 调 SSE → 403
        resp = await client_b.get("/share-pages/sp_default/embed-url")
        assert resp.status_code == 200
        t_short_b = _extract_iframe_params(resp.json()["iframe_url"])["auth"][0]
        dialog_id = os.environ["RAGFLOW_DIALOG_ID"]

        resp = await client_b.post(
            f"/api/v1/chatbots/{dialog_id}/completions",
            json={"question": "越权", "stream": True, "session_id": fake_session},
            headers={"Authorization": f"Bearer {t_short_b}"},
        )
        assert resp.status_code == 403, f"用户 B 不应用 admin 的 session_id 调 SSE: {resp.text}"


# ===========================================================================
# AC8 撤销立即失效 — 撤销授权后,已签发 T_short 立即失效,
#                    iframe 继续提问 403,刷新加载 403。
# ===========================================================================


async def test_ac8_revoke_invalidates_t_short_immediately(client, app, mock_precreate, mock_ragflow_sse):
    """AC8:撤销授权后,同 T_short 调 SSE → 403(已签发 T_short 立即失效)。

    对应原型验收点 8(撤销分享权限后,已有 iframe 与历史链接立即失效)。
    撤销授权 = 删 grant + 吊销 T_short;网关校验链 grant 失败先返回 403。
    """
    fake_session = "ac8-session-001"
    t_short, dialog_id, _ = await _login_precreate_and_get_t_short(client, mock_precreate, fake_session)

    # 撤销前先验证 T_short 可用(mock 上游成功)
    mock_ragflow_sse(session_id=fake_session, message_id="ac8-pre-revoke")
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "撤销前", "stream": True, "session_id": fake_session},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, f"撤销前应可用: {resp.status_code}"

    # 管理员撤销 admin 对 sp_default 的授权(同时吊销已签发 T_short)
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200, f"撤销授权失败: {resp.text}"

    # 撤销后:同 T_short 调网关 → 403(grant 不存在 + T_short 已吊销)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "撤销后", "stream": True, "session_id": fake_session},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 403, f"撤销后 T_short 应立即失效: {resp.text}"


async def test_ac8_revoke_blocks_new_t_short(client, app, mock_precreate):
    """AC8:撤销后 GET embed-url → 403(拒绝签发新 T_short,刷新加载即失效)。

    对应 PRD 用户故事 20:授权撤销后,已打开的 iframe 与历史链接立即失效。
    """
    await _login(client)
    # 撤销前 embed-url 可用
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200

    # 撤销授权
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200

    # 撤销后刷新 embed-url → 403(grant 不存在,拒绝签发新 T_short)
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403, f"撤销后应拒绝签发新 T_short: {resp.text}"


async def test_ac8_revoke_preserves_history(client, app, mock_precreate):
    """AC8:撤销后 chat_session_owner 记录仍在(管理员可查)。

    对应 PRD 用户故事 21:授权撤销后历史会话默认保留(管理员仍可查),
    临时失权不丢历史。撤销 = 删 grant + 吊销 T_short,不删会话归属记录。
    """
    fake_session = "ac8-session-002"
    await _login_and_precreate(client, mock_precreate, fake_session)

    # 撤销前确认 session 已绑定
    owner = app.state.session_store.get(fake_session)
    assert owner is not None
    assert owner.portal_user_id == "u_admin"

    # 撤销授权
    resp = await client.request("DELETE", "/share-pages/sp_default/grants/user/u_admin")
    assert resp.status_code == 200

    # 撤销后:chat_session_owner 记录仍保留(不删除会话归属)
    owner_after = app.state.session_store.get(fake_session)
    assert owner_after is not None, "撤销授权不应删除 chat_session_owner 记录"
    assert owner_after.portal_user_id == "u_admin"
    assert owner_after.session_id == fake_session

    # 管理员仍可通过 /admin/sessions 查到该会话(元数据可见)
    resp = await client.get("/admin/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert any(s["session_id"] == fake_session for s in sessions), "管理员应能在 /admin/sessions 查到撤销后的会话"


# ===========================================================================
# Slice 7 额外分支测试(ISSUES.md L288-303 强制要求,全部 mock,不依赖真实 RAGFlow)
# ===========================================================================
#
# 覆盖 5 个分支:双删事务失败标记 / 禁用用户保留会话 / 级联删除无孤儿 /
# 管理员 elevated 查正文写审计 / 8 类敏感操作全部入审计。


async def test_branch_dual_delete_failure_marks_deleted_at(client, app, mock_precreate, mock_delete_session):
    """分支 1a:用户删除自己的会话,mock RAGFlow DELETE 失败 → 标记 deleted_at,记录仍在。

    对应 ISSUES.md L289 + Slice 5 验收点 4:双删事务策略 — RAGFlow 删除失败时
    门户侧不删,标记 chat_session_owner.deleted_at 待重试;返回 200 不暴露失败。
    """
    fake_session = "branch-dual-delete-001"
    await _login_and_precreate(client, mock_precreate, fake_session)

    # mock RAGFlow DELETE 失败(返回 500,模拟上游故障)
    mock_delete_session(side_effect=HTTPException(status_code=500, detail="RAGFlow 内部错误"))

    resp = await client.delete(f"/share-pages/sp_default/sessions/{fake_session}")
    # 返回 200 不暴露失败(用户视角:删除成功;后台待重试)
    assert resp.status_code == 200, f"双删失败应返回 200 不暴露错误: {resp.text}"

    # chat_session_owner 记录仍在(标记待重试,未硬删除)
    owner = app.state.session_store.get(fake_session)
    assert owner is not None, "记录应保留待重试(未硬删除)"
    assert owner.deleted_at is not None, "deleted_at 应被标记(待重试)"


async def test_branch_disabled_user_cannot_login_but_sessions_preserved(client, app, mock_precreate):
    """分支 1b:管理员禁用用户 → 用户无法登录(403);其 chat_session_owner 记录保留。

    对应 ISSUES.md L290:禁用用户无法登录但会话保留(管理员可查)。
    与硬删除的区别:禁用走 PATCH,会话保留;硬删除走 DELETE,级联删会话。
    """
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_disabled")

    # alice 登录并预创建 session(归属 alice)
    fake_session = "branch-disabled-001"
    mock_precreate(fake_session)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice_disabled", "password": "alicepass123"})
        assert resp.status_code == 200, f"alice 登录失败: {resp.text}"
        resp = await alice_client.post("/share-pages/sp_default/sessions")
        assert resp.status_code == 200

    # 确认 alice 的 session 已绑定
    owner = app.state.session_store.get(fake_session)
    assert owner is not None
    assert owner.portal_user_id == alice["id"]

    # 管理员禁用 alice
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # alice 再次登录 → 403 账号已禁用
    async with httpx.ASGITransport(app=app) as transport_b:
        async with httpx.AsyncClient(transport=transport_b, base_url="http://testserver") as alice_client_b:
            resp = await alice_client_b.post("/login", json={"username": "alice_disabled", "password": "alicepass123"})
            assert resp.status_code == 403, f"禁用用户应无法登录: {resp.text}"
            assert "禁用" in resp.json()["detail"]

    # alice 的 chat_session_owner 记录仍在(禁用不删会话)
    owner_after = app.state.session_store.get(fake_session)
    assert owner_after is not None, "禁用用户后历史会话应保留"
    assert owner_after.portal_user_id == alice["id"]

    # 管理员可通过 /admin/sessions?user_id=... 查到 alice 的会话
    resp = await client.get(f"/admin/sessions?user_id={alice['id']}")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert any(s["session_id"] == fake_session for s in sessions), "管理员应能查到禁用用户的会话"


async def test_branch_hard_delete_user_cascades_sessions(client, app, mock_precreate, mock_delete_session):
    """分支 1c:管理员硬删除用户 → 级联删 chat_session_owner + RAGFlow API4Conversation(无孤儿)。

    对应 ISSUES.md L291 + Slice 5 验收点 6:硬删除用户后,该用户的所有
    chat_session_owner 与对应 RAGFlow API4Conversation 全部清除(无孤儿)。
    mock RAGFlow DELETE 成功;门户侧记录与用户记录都删除。
    """
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_cascade")

    # alice 预创建两个 session(均归属 alice)
    fake_session_1 = "branch-cascade-001"
    fake_session_2 = "branch-cascade-002"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        resp = await alice_client.post("/login", json={"username": "alice_cascade", "password": "alicepass123"})
        assert resp.status_code == 200
        mock_precreate(fake_session_1)
        resp = await alice_client.post("/share-pages/sp_default/sessions")
        assert resp.status_code == 200
        mock_precreate(fake_session_2)
        resp = await alice_client.post("/share-pages/sp_default/sessions")
        assert resp.status_code == 200

    # 确认两个 session 都归属 alice
    assert app.state.session_store.get(fake_session_1).portal_user_id == alice["id"]
    assert app.state.session_store.get(fake_session_2).portal_user_id == alice["id"]

    # mock RAGFlow DELETE 成功(级联删除会调用 gateway.delete_session_via_ragflow)
    mock_delete_session()

    # 管理员硬删除 alice
    resp = await client.delete(f"/admin/users/{alice['id']}")
    assert resp.status_code == 200, f"硬删除失败: {resp.text}"

    # alice 已删除
    assert app.state.seed.get_user(alice["id"]) is None
    assert app.state.seed.get_user_by_username("alice_cascade") is None

    # alice 的所有 chat_session_owner 记录都删除(无孤儿)
    assert app.state.session_store.get(fake_session_1) is None, "硬删除用户应级联删 session_1"
    assert app.state.session_store.get(fake_session_2) is None, "硬删除用户应级联删 session_2"
    # 验证无孤儿:session_store 中无任何 portal_user_id 指向 alice 的记录
    # Slice 8:经公开 API list_all_for_user 查询(含 deleted_at 非空的记录,跨分享页)
    alice_sessions = app.state.session_store.list_all_for_user(alice["id"])
    assert alice_sessions == [], "硬删除用户后不应残留任何会话记录(无孤儿)"


async def test_branch_hard_delete_user_cascades_even_if_ragflow_fails(client, app, mock_precreate, monkeypatch):
    """分支 1c 变体:RAGFlow DELETE 部分失败时,门户侧仍全删(无孤儿),用户也删除。

    对应 ISSUES.md L217「无孤儿」:用户硬删除后,所有会话(含 RAGFlow 失败的)
    都硬删除门户侧记录;RAGFlow 侧残留由管理员后续手动清理。
    理由:用户已不存在,保留 orphan 会话无法后续重试(无用户上下文)。
    """
    await _login(client)
    alice = await _create_user_and_grant(client, username="alice_cascade_fail")

    # alice 预创建两个 session
    fake_session_ok = "branch-cascade-fail-ok"
    fake_session_fail = "branch-cascade-fail-fail"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice_client:
        await alice_client.post("/login", json={"username": "alice_cascade_fail", "password": "alicepass123"})
        mock_precreate(fake_session_ok)
        await alice_client.post("/share-pages/sp_default/sessions")
        mock_precreate(fake_session_fail)
        await alice_client.post("/share-pages/sp_default/sessions")

    # mock RAGFlow DELETE:对 fail session 抛 502,对 ok session 成功
    # 注:cascade_delete_for_user 在 models.py 内延迟导入 portal.gateway.delete_session_via_ragflow,
    # 故 mock gateway 模块的绑定(而非 routes 模块)
    async def _fake_delete(settings, dialog_id, session_id):
        if session_id == fake_session_fail:
            raise HTTPException(status_code=502, detail="RAGFlow 上游失败")

    monkeypatch.setattr("portal.gateway.delete_session_via_ragflow", AsyncMock(side_effect=_fake_delete))

    # 硬删除 alice(RAGFlow 部分失败)
    resp = await client.delete(f"/admin/users/{alice['id']}")
    assert resp.status_code == 200, f"硬删除失败: {resp.text}"

    # alice 已删除(无论 RAGFlow 是否失败)
    assert app.state.seed.get_user(alice["id"]) is None
    assert app.state.seed.get_user_by_username("alice_cascade_fail") is None

    # 所有会话门户侧记录都硬删除(无孤儿)— 包括 RAGFlow 失败的
    assert app.state.session_store.get(fake_session_ok) is None, "RAGFlow 成功的会话应硬删除"
    assert app.state.session_store.get(fake_session_fail) is None, (
        "RAGFlow 失败的会话也应硬删除(无孤儿,用户已不存在无法重试)"
    )
    # 验证无孤儿
    # Slice 8:经公开 API list_all_for_user 查询(含 deleted_at 非空的记录,跨分享页)
    alice_sessions = app.state.session_store.list_all_for_user(alice["id"])
    assert alice_sessions == [], "硬删除用户后不应残留任何会话记录(无孤儿)"


async def test_branch_admin_elevated_view_writes_audit(client, app, mock_precreate, mock_fetch_history):
    """分支 1d:管理员 GET /admin/sessions/{id}?elevated=true → 返回正文 + 写审计。

    对应 ISSUES.md L292 + Slice 6 验收点 4:管理员 elevated 查正文时,
    audit_log 写入 session_view_elevated 记录(含 actor、target session_id、时间)。
    """
    fake_session = "branch-elevated-001"
    await _login_and_precreate(client, mock_precreate, fake_session)

    # mock RAGFlow GET 返回 messages + reference
    fake_history = {
        "session_id": fake_session,
        "messages": [
            {"role": "user", "content": "增值税税率?", "id": "m1"},
            {"role": "assistant", "content": "13%。", "id": "m2"},
        ],
        "reference": {"chunks": [{"id": "c1", "content": "第十条"}], "doc_aggs": []},
    }
    mock_fetch_history(fake_history)

    resp = await client.get(f"/admin/sessions/{fake_session}?elevated=true")
    assert resp.status_code == 200, f"elevated 查正文失败: {resp.text}"
    body = resp.json()
    # 返回正文(messages + reference)
    assert body["messages"] == fake_history["messages"]
    assert body["reference"] == fake_history["reference"]

    # audit_log 有 session_view_elevated 记录
    # Slice 8:经公开 API audit_store.list(action=...) 查询(DB 后端,按 at 倒序)
    elevated_logs = app.state.audit_store.list(action="session_view_elevated")
    assert len(elevated_logs) >= 1, "session_view_elevated 审计记录缺失"
    assert any(log.target_id == fake_session and log.target_type == "session" for log in elevated_logs), (
        "审计记录 target_id 应为 session_id"
    )


async def test_branch_all_8_audit_actions_recorded(
    client, app, mock_precreate, mock_delete_session, mock_fetch_history
):
    """分支 1e:依次触发 8 类敏感操作,验证 audit_log 每类至少一条记录。

    对应 ISSUES.md L293 + Slice 6 验收点 5:8 类敏感操作全部入审计:
      login_success / login_failure / grant_create / grant_revoke /
      session_delete / session_view_elevated / user_enable / user_disable。
    用 audit_store.list() 查询验证每类至少一条。
    """
    # 1. login_success(管理员登录)
    await _login(client)
    # 2. login_failure(错误密码登录)
    resp = await client.post("/login", json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401
    # 3. grant_create(管理员创建用户并授权)
    alice = await _create_user_and_grant(client, username="alice_audit_all8")
    # 4. grant_revoke(管理员撤销授权)
    resp = await client.delete(f"/share-pages/sp_default/grants/user/{alice['id']}")
    assert resp.status_code == 200
    # 重新授权(为后续 user_disable/user_enable 测试 alice 仍可登录)
    resp = await client.post(
        "/admin/share-pages/sp_default/grants",
        json={"subject_type": "user", "subject_id": alice["id"], "permission": "use"},
    )
    assert resp.status_code == 201

    # 5. session_delete(用户删自己的会话,mock RAGFlow 成功)
    fake_session = "branch-audit8-001"
    await _login_and_precreate(client, mock_precreate, fake_session)
    mock_delete_session()  # mock RAGFlow DELETE 成功(双删成功才记 session_delete)
    resp = await client.delete(f"/share-pages/sp_default/sessions/{fake_session}")
    assert resp.status_code == 200

    # 6. session_view_elevated(管理员 elevated 查正文)
    fake_session_2 = "branch-audit8-002"
    await _login_and_precreate(client, mock_precreate, fake_session_2)
    mock_fetch_history({"messages": [{"role": "user", "content": "hi"}], "reference": {}})
    resp = await client.get(f"/admin/sessions/{fake_session_2}?elevated=true")
    assert resp.status_code == 200

    # 7. user_disable(管理员禁用 alice)
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": False})
    assert resp.status_code == 200
    # 8. user_enable(管理员启用 alice)
    resp = await client.patch(f"/admin/users/{alice['id']}", json={"enabled": True})
    assert resp.status_code == 200

    # 验证 audit_log 8 类 action 每类至少一条
    audit_store = app.state.audit_store
    expected_actions = {
        "login_success",
        "login_failure",
        "grant_create",
        "grant_revoke",
        "session_delete",
        "session_view_elevated",
        "user_enable",
        "user_disable",
    }
    recorded_actions = {log.action for log in audit_store.list(limit=1000)}
    missing = expected_actions - recorded_actions
    assert not missing, f"缺少审计 action: {missing}, 已有: {recorded_actions}"
    # 每类至少一条(用 list(action=...) 验证)
    for action in expected_actions:
        logs = audit_store.list(action=action, limit=1000)
        assert len(logs) >= 1, f"action={action} 应至少有一条审计记录,实际: {len(logs)}"


# ===========================================================================
# 回归套件入口说明(便于 CI 单独运行本文件作为回归门禁)
# ===========================================================================
#
# 运行方式:
#   uv run pytest tests/test_e2e_regression.py -v
#
# 本文件所有测试均不依赖真实 RAGFlow(全部 mock),可在 CI 直接运行;
# integration 测试(连真实 RAGFlow)保留在各 slice 测试文件中,
# 由 @pytest.mark.integration 标记,无环境变量时自动跳过。
#
# 不使用 pytestmark 装饰:测试已通过参数显式声明所需 fixture(client/app/mock_*),
# 无需额外的 usefixtures;所有测试默认执行(无 skip 标记),作为 CI 门禁。
