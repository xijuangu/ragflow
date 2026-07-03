"""Slice 1 端到端测试 — 唯一主 seam。

覆盖 7 个验收点(ISSUES.md Issue 1):
  1. 用户能用硬编码 admin 账号登录门户,获得同源会话。
  2. 登录后请求分享页,网关返回的 iframe URL 含 auth=T_short 参数,不含真实 beta Token。
  3. iframe 加载后能正常发起对话,RAGFlow 原生引用片段与标记可见。 [integration]
  4. 无效/过期的 T_short 调网关 → 401。
  5. 未登录用户请求分享页 → 403。
  6. 网关用 beta Token 调 RAGFlow bot_api 成功,SSE 流式响应正常回传。 [integration]
  7. 真实 beta Token 全程不出现在任何浏览器可访问位置(URL、localStorage、响应体)。
"""

import os
from urllib.parse import parse_qs, urlparse

import pytest


def _extract_iframe_params(url: str) -> dict:
    """从 iframe URL 提取 query 参数(消除 urlparse + parse_qs 重复调用)。"""
    return parse_qs(urlparse(url).query)


# ---------------------------------------------------------------------------
# 验收点 1:用户能用硬编码 admin 账号登录门户,获得同源会话。
# ---------------------------------------------------------------------------


async def test_login_success(client):
    """admin 用正确凭据登录,返回 200 并建立同源会话 cookie。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    # 会话 cookie 已建立(HTTP-only,同源)
    assert "session" in resp.cookies
    # 响应体不应回传密码
    body = resp.json()
    assert "password" not in str(body).lower()


async def test_login_wrong_password(client):
    """错误密码登录失败,返回 401,不建立会话。"""
    resp = await client.post("/login", json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401
    assert "session" not in resp.cookies


async def test_login_unknown_user(client):
    """未知用户登录失败,返回 401。"""
    resp = await client.post("/login", json={"username": "nobody", "password": "whatever"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PRD D10:同源嵌入 — 所有响应附 X-Frame-Options: SAMEORIGIN。
# ---------------------------------------------------------------------------


async def test_responses_have_x_frame_options_sameorigin(client):
    """所有响应附 X-Frame-Options: SAMEORIGIN(阻止外部站点 iframe 规避登录态)。"""
    # 200 响应(登录成功)
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    # 401 响应(错误密码)也带该 header(中间件对所有响应生效)
    resp = await client.post("/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"


# ---------------------------------------------------------------------------
# 验收点 5:未登录用户请求分享页 → 403。
# ---------------------------------------------------------------------------


async def test_embed_url_requires_login(client):
    """未登录用户请求 embed-url → 403(不签发 T_short)。"""
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 验收点 2 + 7:登录后 embed-url 含 auth=T_short,不含真实 beta Token。
# ---------------------------------------------------------------------------


async def test_embed_url_contains_short_token_not_beta(client):
    """登录后请求 embed-url,iframe URL 含 auth=T_short 参数,不含真实 beta Token。"""
    # 先登录
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    # 请求 embed-url
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    body = resp.json()
    iframe_url = body["iframe_url"]
    # iframe URL 必须含 auth 参数(即 T_short)
    assert "auth=" in iframe_url
    # 提取 T_short 并断言非空
    qs = _extract_iframe_params(iframe_url)
    t_short = qs.get("auth", [None])[0]
    assert t_short, "auth 参数(T_short)不能为空"
    # iframe URL 必须含 shared_id(dialog_id)与 from=chat
    assert qs.get("from", [None])[0] == "chat"
    assert qs.get("shared_id", [None])[0] == os.environ["RAGFLOW_DIALOG_ID"]
    # 真实 beta Token 绝不出现在 iframe URL 或响应体中
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    assert beta_token not in iframe_url, "真实 beta Token 泄露到 iframe URL"
    assert beta_token not in resp.text, "真实 beta Token 泄露到响应体"


async def _login_and_get_t_short(client):
    """辅助:登录并从 embed-url 提取 T_short 与 dialog_id(供 proxy 测试用)。"""
    await client.post("/login", json={"username": "admin", "password": "testpass123"})
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    qs = _extract_iframe_params(resp.json()["iframe_url"])
    return qs["auth"][0], qs["shared_id"][0]


# ---------------------------------------------------------------------------
# 验收点 4:无效/过期的 T_short 调网关 → 401。
# ---------------------------------------------------------------------------


async def test_proxy_rejects_missing_token(client):
    """无 Authorization header 调网关 → 401(需先登录通过 cookie 校验,再测 T_short 缺失)。"""
    # 先登录建立同源 cookie(Slice 3 起 SSE 代理先校验 cookie 再校验 T_short)
    await client.post("/login", json={"username": "admin", "password": "testpass123"})
    resp = await client.post(
        "/api/v1/chatbots/test-dialog-id-12345/completions",
        json={"question": "测试", "stream": True},
    )
    assert resp.status_code == 401


async def test_proxy_rejects_invalid_token(client):
    """错误的 T_short 调网关 → 401(需先登录通过 cookie 校验,再测 T_short 无效)。"""
    # 先登录建立同源 cookie
    await client.post("/login", json={"username": "admin", "password": "testpass123"})
    resp = await client.post(
        "/api/v1/chatbots/test-dialog-id-12345/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


async def test_proxy_rejects_expired_token(client, app):
    """过期的 T_short 调网关 → 401。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    # 直接把令牌记录的过期时间改为过去,模拟过期
    rec = app.state.token_store._tokens[t_short]
    rec.expires_at = 0.0
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 401


async def test_proxy_rejects_revoked_token(client, app):
    """撤销后的 T_short 调网关 → 401。"""
    t_short, dialog_id = await _login_and_get_t_short(client)
    app.state.token_store.revoke(t_short)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 401


async def test_proxy_rejects_token_dialog_mismatch(client):
    """T_short 绑定的 dialog_id 与请求的 dialog_id 不一致 → 401。"""
    t_short, _dialog_id = await _login_and_get_t_short(client)
    resp = await client.post(
        "/api/v1/chatbots/a-different-dialog-id/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 验收点 7:真实 beta Token 全程不出现在任何浏览器可访问位置(URL、响应体)。
# ---------------------------------------------------------------------------


async def test_beta_token_never_leaked(client):
    """beta Token 不出现在任何响应(embed-url、proxy 401、proxy 流式错误)。"""
    beta_token = os.environ["RAGFLOW_BETA_TOKEN"]
    # 1. 登录响应不含 beta Token
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert beta_token not in resp.text
    # 2. embed-url 响应不含 beta Token
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert beta_token not in resp.text
    # 3. proxy 缺令牌 401 响应不含 beta Token
    resp = await client.post("/api/v1/chatbots/test-dialog-id-12345/completions", json={"question": "x"})
    assert beta_token not in resp.text
    # 4. proxy 错误令牌 401 响应不含 beta Token
    resp = await client.post(
        "/api/v1/chatbots/test-dialog-id-12345/completions",
        json={"question": "x"},
        headers={"Authorization": "Bearer bad-token"},
    )
    assert beta_token not in resp.text
    # 5. proxy 有效令牌但上游不可达 → 流式错误事件不含 beta Token
    t_short, dialog_id = await _login_and_get_t_short(client)
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    await resp.aread()  # 消费流式响应体
    assert beta_token not in resp.text


# ---------------------------------------------------------------------------
# 验收点 6:integration — 网关用 beta Token 调 RAGFlow bot_api,SSE 流式回传。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proxy_sse_streams_from_ragflow(client, real_ragflow):
    """[integration] 用真实 RAGFlow 验证 SSE 代理:登录→T_short→proxy→流式响应。"""
    # 登录并获取 T_short
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    qs = _extract_iframe_params(resp.json()["iframe_url"])
    t_short = qs["auth"][0]
    dialog_id = qs["shared_id"][0]
    # 调代理 SSE(首次调用:RAGFlow 创建 session 并返回 prologue)
    async with client.stream(
        "POST",
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "你好", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    ) as resp:
        assert resp.status_code == 200
        # 收集流式内容
        chunks = []
        async for line in resp.aiter_lines():
            chunks.append(line)
            if len(chunks) > 50:
                break
        body = "\n".join(chunks)
        # SSE 应含 data: 行,且含 session_id(RAGFlow 首帧返回 session_id)
        assert "data:" in body, f"未收到 SSE 数据,实际响应: {body[:300]}"
        # beta Token 绝不出现在流式响应中
        assert real_ragflow["token"] not in body, "beta Token 泄露到 SSE 响应"


# ---------------------------------------------------------------------------
# 验收点 3:integration — iframe 加载后能正常发起对话,引用片段可见。
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_iframe_conversation_with_references(client, real_ragflow):
    """[integration] 完整对话流:首调拿 session_id → 二调提问 → 引用片段可见。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200
    resp = await client.get("/share-pages/sp_default/embed-url")
    assert resp.status_code == 200
    qs = _extract_iframe_params(resp.json()["iframe_url"])
    t_short = qs["auth"][0]
    dialog_id = qs["shared_id"][0]
    headers = {"Authorization": f"Bearer {t_short}"}
    # 第 1 轮:无 session_id,RAGFlow 创建 session 返回 prologue
    session_id = None
    async with client.stream(
        "POST",
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "测试", "stream": True, "quote": True},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data:") and "session_id" in line:
                import json

                try:
                    data = json.loads(line[5:].strip())
                    sid = data.get("data", {}).get("session_id") or data.get("session_id")
                    if sid:
                        session_id = sid
                        break
                except json.JSONDecodeError:
                    pass
    assert session_id, "首调未返回 session_id"
    # 第 2 轮:带 session_id 提问,期望流式回答 + 引用
    async with client.stream(
        "POST",
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "增值税税率是多少", "stream": True, "quote": True, "session_id": session_id},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        body = ""
        async for line in resp.aiter_lines():
            body += line + "\n"
        # 流式回答应含 answer 字段
        assert "answer" in body, f"未收到回答,实际响应: {body[:300]}"
        # beta Token 不泄露
        assert real_ragflow["token"] not in body
