"""Issue 91 — 门户 usage_record 表 + 网关 SSE 记账测试。

覆盖 ISSUES.md Issue 91 验收点:
  1. 每次 chat completions 问答落一条 usage_record,字段完整;正文不落库。
  2. 正常完成 / 断流 / 末帧缺 usage(旧 RAGFlow)三种路径,incomplete 语义符合 §12.2:
     - 末帧带 usage → 真实值,incomplete=False;
     - 断流 → output = 已收到回答文本计词、input = 按请求问题估算,incomplete=True;
     - 末帧无 usage → 同一估算口径,incomplete=True。
  3. 会话删除不影响已落账记录;公开分享页(匿名)问答记账,subject = u_anonymous。
  4. greeting(question="")非问答,不记账。
"""
import httpx
import pytest
from sqlalchemy import inspect

from portal.db import PORTAL_TABLES
from portal.gateway import estimate_tokens, extract_usage_from_sse_payload
from portal.models import ANONYMOUS_USER_ID

# ---------------------------------------------------------------------------
# 辅助:SSE mock 与登录/令牌/绑定
# ---------------------------------------------------------------------------


def _mock_ragflow_sse(
    monkeypatch,
    *,
    chunks: list[bytes] | None = None,
    drop_after_chunks: bool = False,
):
    """mock 网关的 httpx.AsyncClient,让上游 SSE 返回指定 chunk 序列。

    drop_after_chunks=True 时在 chunks 用尽后抛 httpx.ReadError,模拟断流。
    """

    class _MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            async def _agen():
                for chunk in chunks or []:
                    yield chunk
                if drop_after_chunks:
                    raise httpx.ReadError("upstream connection dropped mid-stream")

            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    content=_agen(),
                    headers={"content-type": "text/event-stream"},
                )
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)


def _sse_frame(payload: str) -> bytes:
    return f"data: {payload}\n\n".encode("utf-8")


async def _login(client, username="admin", password="testpass123"):
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def _get_t_short(client, share_page_id="sp_default"):
    """登录后调 embed-url 取 T_short,返回 (t_short, dialog_id)。"""
    await _login(client)
    resp = await client.get(f"/share-pages/{share_page_id}/embed-url")
    assert resp.status_code == 200, f"embed-url 失败: {resp.text}"
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(resp.json()["iframe_url"]).query)
    return qs["auth"][0], qs["shared_id"][0]


def _bind_session(app, session_id, dialog_id, portal_user_id="u_admin", share_page_id="sp_default"):
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id=share_page_id,
        portal_user_id=portal_user_id,
        ragflow_resource_id=dialog_id,
    )


# ===========================================================================
# estimate_tokens / extract_usage_from_sse_payload 纯函数
# ===========================================================================


def test_estimate_tokens_empty_is_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_cjk_one_token_per_char():
    assert estimate_tokens("你好世界") == 4


def test_estimate_tokens_narrow_four_chars_per_token_rounded_up():
    # 8 个 ASCII 字符 → 2 token;9 个 → 3(向上取整)
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("abcdefghi") == 3


def test_estimate_tokens_mixed_cjk_and_narrow():
    # 2 个 CJK + "ab"(2 窄字符 → 1 token)= 3
    assert estimate_tokens("你好ab") == 3


def test_extract_usage_nested_data_usage():
    payload = {"code": 0, "data": {"answer": "a", "final": True, "usage": {"input": 12, "output": 34}}}
    assert extract_usage_from_sse_payload(payload) == (12, 34)


def test_extract_usage_flat_usage():
    assert extract_usage_from_sse_payload({"usage": {"input": 1, "output": 2}}) == (1, 2)


def test_extract_usage_missing_returns_none():
    assert extract_usage_from_sse_payload({"data": {"answer": "a", "final": True}}) is None
    assert extract_usage_from_sse_payload({"answer": "a"}) is None
    assert extract_usage_from_sse_payload(None) is None


def test_extract_usage_malformed_returns_none():
    assert extract_usage_from_sse_payload({"data": {"usage": {"input": "x", "output": 1}}}) is None
    assert extract_usage_from_sse_payload({"data": {"usage": {"input": 1}}}) is None
    assert extract_usage_from_sse_payload({"data": {"usage": "not-a-dict"}}) is None


# ===========================================================================
# usage_record 表 schema + UsageStore
# ===========================================================================


def test_usage_record_table_created(app):
    inspector = inspect(app.state.db_engine)
    tables = set(inspector.get_table_names())
    assert "usage_record" in PORTAL_TABLES
    assert "usage_record" in tables
    cols = {c["name"] for c in inspector.get_columns("usage_record")}
    required = {
        "id",
        "created_at",
        "portal_user_id",
        "share_page_id",
        "session_id",
        "model_name",
        "input_tokens",
        "output_tokens",
        "incomplete",
        "org_id",
    }
    assert required <= cols, f"usage_record 缺字段: {required - cols}"
    # 不存问答正文(§12.2):不得出现正文类字段
    forbidden = {"question", "answer", "content", "message", "prompt"}
    assert not (forbidden & cols), f"usage_record 不得包含正文字段: {forbidden & cols}"


def test_usage_store_record_and_list_roundtrip(app):
    store = app.state.usage_store
    rec = store.record(
        portal_user_id="u_admin",
        share_page_id="sp_default",
        session_id="s1",
        model_name="",
        input_tokens=10,
        output_tokens=20,
        incomplete=False,
    )
    assert rec.id
    listed = store.list()
    assert len(listed) == 1
    row = listed[0]
    assert row.portal_user_id == "u_admin"
    assert row.share_page_id == "sp_default"
    assert row.session_id == "s1"
    assert row.input_tokens == 10
    assert row.output_tokens == 20
    assert row.incomplete is False
    assert row.created_at == pytest.approx(rec.created_at)


def test_usage_store_list_filters(app):
    store = app.state.usage_store
    store.record(portal_user_id="u1", share_page_id="sp_a", session_id="s1",
                 model_name="", input_tokens=1, output_tokens=1, incomplete=False,
                 created_at=1000.0)
    store.record(portal_user_id="u2", share_page_id="sp_a", session_id="s2",
                 model_name="", input_tokens=2, output_tokens=2, incomplete=True,
                 created_at=2000.0)
    store.record(portal_user_id="u1", share_page_id="sp_b", session_id="s3",
                 model_name="", input_tokens=3, output_tokens=3, incomplete=False,
                 created_at=3000.0, org_id="org_x")

    assert [r.session_id for r in store.list()] == ["s3", "s2", "s1"]  # created_at 倒序
    assert [r.session_id for r in store.list(portal_user_id="u1")] == ["s3", "s1"]
    assert [r.session_id for r in store.list(share_page_id="sp_a")] == ["s2", "s1"]
    assert [r.session_id for r in store.list(since=1500.0)] == ["s3", "s2"]
    assert [r.session_id for r in store.list(until=2000.0)] == ["s2", "s1"]
    assert [r.session_id for r in store.list(incomplete=True)] == ["s2"]
    assert [r.session_id for r in store.list(org_id="org_x")] == ["s3"]
    assert len(store.list(limit=2)) == 2


# ===========================================================================
# SSE 记账:正常完成(末帧带 usage)
# ===========================================================================


async def test_sse_records_real_usage_from_final_frame(client, app, monkeypatch):
    t_short, dialog_id = await _get_t_short(client)
    _bind_session(app, "s-normal", dialog_id)
    _mock_ragflow_sse(
        monkeypatch,
        chunks=[
            _sse_frame('{"data":{"answer":"你","session_id":"s-normal"}}'),
            _sse_frame(
                '{"code":0,"data":{"answer":"你好","session_id":"s-normal","final":true,'
                '"usage":{"input":111,"output":222}}}'
            ),
        ],
    )

    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "你好", "stream": True, "session_id": "s-normal"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, resp.text

    records = app.state.usage_store.list()
    assert len(records) == 1
    rec = records[0]
    assert rec.portal_user_id == "u_admin"
    assert rec.share_page_id == "sp_default"
    assert rec.session_id == "s-normal"
    assert rec.input_tokens == 111
    assert rec.output_tokens == 222
    assert rec.incomplete is False


# ===========================================================================
# SSE 记账:断流(上游中途 ReadError)
# ===========================================================================


async def test_sse_records_estimated_usage_on_disconnect(client, app, monkeypatch):
    t_short, dialog_id = await _get_t_short(client)
    _bind_session(app, "s-drop", dialog_id)
    _mock_ragflow_sse(
        monkeypatch,
        chunks=[_sse_frame('{"data":{"answer":"部分回答","session_id":"s-drop"}}')],
        drop_after_chunks=True,
    )

    question = "断流时的提问"
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": question, "stream": True, "session_id": "s-drop"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    # 网关把上游断流降级为 SSE 错误事件,客户端仍拿到 200 流
    assert resp.status_code == 200, resp.text

    records = app.state.usage_store.list()
    assert len(records) == 1
    rec = records[0]
    assert rec.session_id == "s-drop"
    # §12.2 断流口径:input 按请求问题估算,output 按已收到回答文本计词,标记 incomplete
    assert rec.input_tokens == estimate_tokens(question)
    assert rec.output_tokens == estimate_tokens("部分回答")
    assert rec.output_tokens > 0
    assert rec.incomplete is True


# ===========================================================================
# SSE 记账:末帧缺 usage(旧 RAGFlow / empty_response 捷径)
# ===========================================================================


async def test_sse_records_estimated_usage_when_final_frame_lacks_usage(client, app, monkeypatch):
    t_short, dialog_id = await _get_t_short(client)
    _bind_session(app, "s-legacy", dialog_id)
    _mock_ragflow_sse(
        monkeypatch,
        chunks=[
            _sse_frame('{"data":{"answer":"完整回答","session_id":"s-legacy","final":true}}'),
        ],
    )

    question = "旧版提问"
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": question, "stream": True, "session_id": "s-legacy"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, resp.text

    records = app.state.usage_store.list()
    assert len(records) == 1
    rec = records[0]
    # 与断流同一估算口径,并标记 incomplete(§12.2)
    assert rec.input_tokens == estimate_tokens(question)
    assert rec.output_tokens == estimate_tokens("完整回答")
    assert rec.incomplete is True


# ===========================================================================
# 边界:greeting 不记账;会话删除不影响记录;公开(匿名)问答记账
# ===========================================================================


async def test_greeting_request_not_recorded(client, app, monkeypatch):
    t_short, dialog_id = await _get_t_short(client)
    _mock_ragflow_sse(
        monkeypatch,
        chunks=[_sse_frame('{"data":{"answer":"","session_id":"s-greet","final":true}}')],
    )
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "", "stream": True, "quote": True},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, resp.text
    assert app.state.usage_store.list() == []


async def test_session_deletion_does_not_affect_usage_records(client, app, monkeypatch):
    t_short, dialog_id = await _get_t_short(client)
    _bind_session(app, "s-del", dialog_id)
    _mock_ragflow_sse(
        monkeypatch,
        chunks=[
            _sse_frame(
                '{"code":0,"data":{"answer":"答","session_id":"s-del","final":true,'
                '"usage":{"input":1,"output":2}}}'
            )
        ],
    )
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "问", "stream": True, "session_id": "s-del"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, resp.text
    assert len(app.state.usage_store.list()) == 1

    # 删除会话(门户侧硬删)后,已落账 usage_record 必须保留
    assert app.state.session_store.delete("s-del") is True
    records = app.state.usage_store.list()
    assert len(records) == 1
    assert records[0].session_id == "s-del"


async def test_public_anonymous_chat_recorded_with_anonymous_subject(client, app, monkeypatch):
    """公开分享页问答同样记账,subject 为匿名标识 u_anonymous(§12.2)。"""
    seed = app.state.seed
    assert seed.set_share_page_public("sp_default", True) is True
    dialog_id = seed.get_share_page("sp_default").ragflow_resource_id

    resp = await client.get("/public/sp_default/embed-url")
    assert resp.status_code == 200, resp.text
    from urllib.parse import parse_qs, urlparse

    t_short = parse_qs(urlparse(resp.json()["iframe_url"]).query)["auth"][0]

    # 公开会话归属匿名锚点(与 public_precreate_session 行为一致)
    _bind_session(app, "s-pub", dialog_id, portal_user_id=ANONYMOUS_USER_ID)
    _mock_ragflow_sse(
        monkeypatch,
        chunks=[
            _sse_frame(
                '{"code":0,"data":{"answer":"公开回答","session_id":"s-pub","final":true,'
                '"usage":{"input":7,"output":8}}}'
            )
        ],
    )
    resp = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "公开提问", "stream": True, "session_id": "s-pub"},
        headers={"Authorization": f"Bearer {t_short}"},
    )
    assert resp.status_code == 200, resp.text

    records = app.state.usage_store.list()
    assert len(records) == 1
    rec = records[0]
    assert rec.portal_user_id == ANONYMOUS_USER_ID
    assert rec.share_page_id == "sp_default"
    assert rec.input_tokens == 7
    assert rec.output_tokens == 8
    assert rec.incomplete is False
