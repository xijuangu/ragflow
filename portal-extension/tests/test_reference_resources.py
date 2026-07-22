"""Issues 82/84/85: reference resources use scoped Portal authorization."""

import json
import os
import time as time_module
from urllib.parse import parse_qs, urlparse

import httpx

from portal.gateway import SSEJSONEventParser, TokenStore


def test_sse_json_event_parser_assembles_fragmented_payloads():
    parser = SSEJSONEventParser()
    event = b'data: {"data":{"session_id":"session-fragmented"}}\n\n'

    assert parser.feed(event[:17]) == []
    assert parser.feed(event[17:]) == [{"data": {"session_id": "session-fragmented"}}]


def test_sse_json_event_parser_discards_oversized_event_and_recovers():
    parser = SSEJSONEventParser(max_event_bytes=32)

    assert parser.feed(b"data: " + (b"x" * 40)) == []
    assert parser.feed(b'data: {"code":0}\n\n') == [{"code": 0}]


async def _login_and_get_token(client):
    login = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert login.status_code == 200
    embed = await client.get("/share-pages/sp_default/embed-url")
    assert embed.status_code == 200
    params = parse_qs(urlparse(embed.json()["iframe_url"]).query)
    return params["auth"][0], params["shared_id"][0]


def _mock_history_and_thumbnails(monkeypatch, *, session_id, dialog_id, captured):
    history = {
        "code": 0,
        "data": {
            "session_id": session_id,
            "messages": [{"role": "assistant", "content": "answer"}],
            "reference": [
                {
                    "chunks": [{"document_id": "doc-allowed"}],
                    "doc_aggs": [{"doc_id": "doc-allowed", "doc_name": "劳动法.pdf"}],
                }
            ],
        },
    }
    thumbnails = {
        "code": 0,
        "data": {"doc-allowed": "data:image/png;base64,dGVzdA=="},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization", ""),
            }
        )
        if request.url.path.endswith(f"/chatbots/{dialog_id}/sessions/{session_id}"):
            return httpx.Response(200, content=json.dumps(history).encode(), headers={"content-type": "application/json"})
        if request.url.path.endswith("/api/v1/thumbnails"):
            return httpx.Response(
                200,
                content=json.dumps(thumbnails).encode(),
                headers={"content-type": "application/json"},
            )
        if request.url.path.endswith("/api/v1/documents/doc-allowed/preview"):
            return httpx.Response(
                200,
                content=b"docx-preview-bytes",
                headers={
                    "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "content-disposition": 'inline; filename="labor-law.docx"',
                },
            )
        return httpx.Response(404, json={"code": 404})

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", MockAsyncClient)


def _mock_sse_reference_and_thumbnails(
    monkeypatch,
    *,
    session_id,
    dialog_id,
    captured,
    sse_chunks=None,
):
    reference = {
        "chunks": [{"document_id": "doc-from-sse"}],
        "doc_aggs": [{"doc_id": "doc-from-sse", "doc_name": "劳动法.pdf"}],
    }
    sse_body = (
        "data: "
        + json.dumps(
            {
                "code": 0,
                "data": {
                    "answer": "法定节假日[ID:0]",
                    "session_id": session_id,
                    "reference": reference,
                    "final": True,
                },
            },
            ensure_ascii=False,
        )
        + "\n\n"
    ).encode()
    history = {
        "code": 0,
        "data": {
            "session_id": session_id,
            "messages": [{"role": "assistant", "content": "法定节假日[ID:0]"}],
            "reference": [reference],
        },
    }

    class ChunkedSseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in sse_chunks or [sse_body]:
                yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers.get("Authorization", ""),
            }
        )
        if request.url.path.endswith(f"/chatbots/{dialog_id}/completions"):
            return httpx.Response(
                200,
                stream=ChunkedSseStream(),
                headers={"content-type": "text/event-stream"},
            )
        if request.url.path.endswith(f"/chatbots/{dialog_id}/sessions/{session_id}"):
            return httpx.Response(200, json=history)
        if request.url.path.endswith("/api/v1/thumbnails"):
            requested = request.url.params.get_list("doc_ids")
            return httpx.Response(
                200,
                json={"code": 0, "data": {document_id: "thumbnail" for document_id in requested}},
            )
        return httpx.Response(404, json={"code": 404})

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", MockAsyncClient)


async def _authorize_owned_history_document(client, app, monkeypatch, *, session_id):
    token, dialog_id = await _login_and_get_token(client)
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(
        monkeypatch,
        session_id=session_id,
        dialog_id=dialog_id,
        captured=captured,
    )
    history = await client.get(
        f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history.status_code == 200
    return token, captured


async def test_owned_history_authorizes_its_document_thumbnails(client, app, monkeypatch):
    """Owned history references become the only document IDs available to its pt_ token."""
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue82-owned-history"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(
        monkeypatch,
        session_id=session_id,
        dialog_id=dialog_id,
        captured=captured,
    )

    history = await client.get(
        f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history.status_code == 200

    thumbnails = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert thumbnails.status_code == 200
    assert thumbnails.json() == {
        "code": 0,
        "data": {"doc-allowed": "data:image/png;base64,dGVzdA=="},
    }
    assert captured[-1]["authorization"] == f"Bearer {os.environ['RAGFLOW_BETA_TOKEN']}"


async def test_owned_history_authorizes_its_document_preview(client, app, monkeypatch):
    """Issue 85: a referenced document opens through Portal without a RAGFlow login."""
    token, captured = await _authorize_owned_history_document(
        client,
        app,
        monkeypatch,
        session_id="issue85-owned-history-preview",
    )

    preview = await client.get(
        "/api/v1/documents/doc-allowed/preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert preview.status_code == 200
    assert preview.content == b"docx-preview-bytes"
    assert preview.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert preview.headers["content-disposition"] == 'inline; filename="labor-law.docx"'
    assert captured[-1]["authorization"] == f"Bearer {os.environ['RAGFLOW_BETA_TOKEN']}"


async def test_preview_rejects_document_not_referenced_by_owned_history(client, app, monkeypatch):
    token, captured = await _authorize_owned_history_document(
        client,
        app,
        monkeypatch,
        session_id="issue85-preview-document-scope",
    )

    response = await client.get(
        "/api/v1/documents/doc-not-referenced/preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert len(captured) == 1, "越权 preview 不得发往 RAGFlow"


async def test_preview_rechecks_share_page_grant(client, app, monkeypatch):
    token, captured = await _authorize_owned_history_document(
        client,
        app,
        monkeypatch,
        session_id="issue85-preview-revoked-grant",
    )
    assert app.state.seed.revoke_grant("sp_default", "user", "u_admin") is True

    response = await client.get(
        "/api/v1/documents/doc-allowed/preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert len(captured) == 1


async def test_preview_rejects_unknown_or_revoked_portal_token(client, app):
    unknown = await client.get(
        "/api/v1/documents/doc-allowed/preview",
        headers={"Authorization": "Bearer pt_unknown"},
    )
    assert unknown.status_code == 401

    token, _ = await _login_and_get_token(client)
    app.state.token_store.revoke(token)
    revoked = await client.get(
        "/api/v1/documents/doc-allowed/preview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoked.status_code == 401


async def test_sse_answer_authorizes_its_document_thumbnails(client, app, monkeypatch):
    """Issue 84: a new referenced answer can load its resources without reopening history."""
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue84-sse-reference"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_sse_reference_and_thumbnails(
        monkeypatch,
        session_id=session_id,
        dialog_id=dialog_id,
        captured=captured,
    )

    completion = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "法定节假日有哪些？", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    await completion.aread()
    assert completion.status_code == 200

    referenced = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-from-sse"},
        headers={"Authorization": f"Bearer {token}"},
    )
    unrelated = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-not-referenced"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert referenced.status_code == 200
    assert unrelated.status_code == 403
    thumbnail_calls = [item for item in captured if "/api/v1/thumbnails" in item["url"]]
    assert len(thumbnail_calls) == 1, "unreferenced document IDs must not be sent to RAGFlow"


async def test_fragmented_sse_reference_authorizes_document_thumbnails(client, app, monkeypatch):
    """Issue 84: authorization does not depend on upstream network chunk boundaries."""
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue84-fragmented-sse-reference"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    event = (
        "data: "
        + json.dumps(
            {
                "code": 0,
                "data": {
                    "answer": "带薪年休假[ID:0]",
                    "session_id": session_id,
                    "reference": {
                        "chunks": [{"document_id": "doc-from-sse"}],
                        "doc_aggs": [{"doc_id": "doc-from-sse"}],
                    },
                    "final": True,
                },
            },
            ensure_ascii=False,
        )
        + "\n\n"
    ).encode()
    _mock_sse_reference_and_thumbnails(
        monkeypatch,
        session_id=session_id,
        dialog_id=dialog_id,
        captured=[],
        sse_chunks=[event[:11], event[11:57], event[57:]],
    )

    completion = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "带薪年休假有几天？", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    await completion.aread()
    thumbnail = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-from-sse"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert completion.status_code == 200
    assert thumbnail.status_code == 200


async def test_coalesced_sse_events_authorize_references_from_each_complete_event(client, app, monkeypatch):
    """Issue 84: multiple SSE events in one network chunk are parsed independently."""
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue84-coalesced-sse-events"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    progress_event = b'data: {"code":0,"data":{"answer":"retrieving","final":false}}\n\n'
    reference_event = (
        "data: "
        + json.dumps(
            {
                "code": 0,
                "data": {
                    "answer": "限制延长工作时间[ID:0]",
                    "session_id": session_id,
                    "reference": {"doc_aggs": [{"doc_id": "doc-from-sse"}]},
                    "final": True,
                },
            },
            ensure_ascii=False,
        )
        + "\n\n"
    ).encode()
    _mock_sse_reference_and_thumbnails(
        monkeypatch,
        session_id=session_id,
        dialog_id=dialog_id,
        captured=[],
        sse_chunks=[progress_event + reference_event],
    )

    completion = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "什么情况下可以延长工作时间？", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    await completion.aread()
    thumbnail = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-from-sse"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert completion.status_code == 200
    assert thumbnail.status_code == 200


async def test_public_sse_answer_authorizes_only_its_document_thumbnails(client, app, monkeypatch):
    """Issue 84: the public iframe path applies the same reference scope without a Portal cookie."""
    assert app.state.seed.set_share_page_public("sp_default", True) is True
    embed = await client.get("/public/sp_default/embed-url")
    assert embed.status_code == 200
    params = parse_qs(urlparse(embed.json()["iframe_url"]).query)
    token = params["auth"][0]
    dialog_id = params["shared_id"][0]
    session_id = "issue84-public-sse-reference"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_anonymous",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_sse_reference_and_thumbnails(
        monkeypatch,
        session_id=session_id,
        dialog_id=dialog_id,
        captured=captured,
    )

    completion = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "法定节假日有哪些？", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    await completion.aread()
    referenced = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-from-sse"},
        headers={"Authorization": f"Bearer {token}"},
    )
    unrelated = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-not-referenced"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert completion.status_code == 200
    assert referenced.status_code == 200
    assert unrelated.status_code == 403
    thumbnail_calls = [item for item in captured if "/api/v1/thumbnails" in item["url"]]
    assert len(thumbnail_calls) == 1


async def test_thumbnail_rejects_document_not_referenced_by_owned_history(client, app, monkeypatch):
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue82-document-scope"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(monkeypatch, session_id=session_id, dialog_id=dialog_id, captured=captured)
    assert (
        await client.get(
            f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200

    response = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed,doc-not-referenced"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert len(captured) == 1, "越权文档 ID 不得发往 RAGFlow"


async def test_thumbnail_rejects_unknown_or_revoked_portal_token(client, app):
    unknown = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": "Bearer pt_unknown"},
    )
    assert unknown.status_code == 401

    token, _ = await _login_and_get_token(client)
    app.state.token_store.revoke(token)
    revoked = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoked.status_code == 401


async def test_thumbnail_rechecks_share_page_grant(client, app, monkeypatch):
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue82-revoked-grant"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(monkeypatch, session_id=session_id, dialog_id=dialog_id, captured=captured)
    assert (
        await client.get(
            f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200
    assert app.state.seed.revoke_grant("sp_default", "user", "u_admin") is True

    response = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert len(captured) == 1


async def test_foreign_session_cannot_authorize_its_reference_documents(client, app, monkeypatch):
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue82-foreign-history"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_user2",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(monkeypatch, session_id=session_id, dialog_id=dialog_id, captured=captured)

    history = await client.get(
        f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    thumbnails = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert history.status_code == 403
    assert thumbnails.status_code == 403
    assert captured == []


async def test_session_from_another_share_page_cannot_authorize_documents(
    client, app, monkeypatch
):
    """同一用户/同一 resource 也不能跨分享页复用 grant 与 pt_。"""
    token, dialog_id = await _login_and_get_token(client)
    other_page = app.state.seed.create_share_page(
        name="same-resource-other-page",
        ragflow_resource_id=dialog_id,
    )
    session_id = "issue82-other-share-page"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id=other_page.id,
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(monkeypatch, session_id=session_id, dialog_id=dialog_id, captured=captured)

    response = await client.get(
        f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert captured == []


async def test_history_rejects_portal_token_owned_by_another_logged_in_user(client, app, monkeypatch):
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue82-current-user-mismatch"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    _mock_history_and_thumbnails(monkeypatch, session_id=session_id, dialog_id=dialog_id, captured=captured)
    assert (await client.post("/logout")).status_code == 200
    assert (
        await client.post("/login", json={"username": "user2", "password": "testpass123"})
    ).status_code == 200

    response = await client.get(
        f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert captured == []


async def test_native_ragflow_token_thumbnail_request_is_passed_through(client, monkeypatch):
    captured = []
    _mock_history_and_thumbnails(
        monkeypatch,
        session_id="unused",
        dialog_id="unused",
        captured=captured,
    )

    response = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": "Bearer native-ragflow-token"},
    )

    assert response.status_code == 200
    assert captured[-1]["authorization"] == "Bearer native-ragflow-token"


async def test_native_ragflow_token_document_preview_is_passed_through(client, monkeypatch):
    captured = []
    _mock_history_and_thumbnails(
        monkeypatch,
        session_id="unused",
        dialog_id="unused",
        captured=captured,
    )

    response = await client.get(
        "/api/v1/documents/doc-allowed/preview",
        headers={"Authorization": "Bearer native-ragflow-token"},
    )

    assert response.status_code == 200
    assert response.content == b"docx-preview-bytes"
    assert captured[-1]["authorization"] == "Bearer native-ragflow-token"


async def test_thumbnail_image_url_gets_scoped_ticket_and_loads_without_authorization_header(
    client, app, monkeypatch
):
    token, dialog_id = await _login_and_get_token(client)
    session_id = "issue82-image-ticket"
    image_id = "kb-1-thumbnail.png"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []

    history = {
        "code": 0,
        "data": {
            "session_id": session_id,
            "reference": [{"doc_aggs": [{"doc_id": "doc-allowed"}]}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization", ""),
            }
        )
        if request.url.path.endswith(f"/chatbots/{dialog_id}/sessions/{session_id}"):
            return httpx.Response(200, json=history)
        if request.url.path.endswith("/api/v1/thumbnails"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"doc-allowed": f"/api/v1/documents/images/{image_id}"}},
            )
        if request.url.path.endswith(f"/api/v1/documents/images/{image_id}"):
            return httpx.Response(200, content=b"png-bytes", headers={"content-type": "image/png"})
        return httpx.Response(404)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", MockAsyncClient)
    assert (
        await client.get(
            f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200
    thumbnails = await client.get(
        "/api/v1/thumbnails",
        params={"doc_ids": "doc-allowed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    image_url = thumbnails.json()["data"]["doc-allowed"]

    assert image_url.startswith(f"/api/v1/documents/images/{image_id}?portal_ticket=")
    ticket_query = image_url.split("?", 1)[1]
    mismatched = await client.get(f"/api/v1/documents/images/another-image.png?{ticket_query}")
    assert mismatched.status_code == 403
    image = await client.get(image_url)
    assert image.status_code == 200
    assert image.content == b"png-bytes"
    assert image.headers["content-type"] == "image/png"
    assert captured[-1]["authorization"] == f"Bearer {os.environ['RAGFLOW_BETA_TOKEN']}"

    ticket_value = parse_qs(urlparse(image_url).query)["portal_ticket"][0]
    assert ticket_value in app.state.token_store._reference_image_tickets
    app.state.token_store.revoke(token)
    assert ticket_value not in app.state.token_store._reference_image_tickets
    revoked = await client.get(image_url)
    assert revoked.status_code == 401


async def test_native_ragflow_document_image_request_is_passed_through(client, monkeypatch):
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, content=b"native-image", headers={"content-type": "image/webp"})

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", MockAsyncClient)
    response = await client.get(
        "/api/v1/documents/images/native-image.webp",
        headers={"Authorization": "Bearer native-ragflow-token"},
    )

    assert response.status_code == 200
    assert response.content == b"native-image"
    assert response.headers["content-type"] == "image/webp"
    assert captured == ["Bearer native-ragflow-token"]


def test_issuing_image_ticket_prunes_expired_ticket(monkeypatch):
    store = TokenStore()
    now = 1_000.0
    monkeypatch.setattr("portal.gateway.time.time", lambda: now)
    expired_token = store.issue("user", "share", ttl_seconds=10)
    store.authorize_documents(expired_token, {"doc-old"})
    expired_ticket = store.issue_reference_image_ticket(expired_token, "doc-old", "image-old")

    now = 2_000.0
    active_token = store.issue("user", "share", ttl_seconds=10)
    store.authorize_documents(active_token, {"doc-new"})
    store.issue_reference_image_ticket(active_token, "doc-new", "image-new")

    assert expired_ticket not in store._reference_image_tickets


def _mock_ragflow_rejects_image_without_beta_token(monkeypatch, *, history, image_id, captured):
    """RAGFlow mock:history 原样返回;图片端点无 beta token 时 401(模拟真实鉴权)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization", ""),
            }
        )
        if request.url.path.endswith("/api/v1/thumbnails"):
            return httpx.Response(200, json={"code": 0, "data": {}})
        if request.url.path.endswith(f"/api/v1/documents/images/{image_id}"):
            if request.headers.get("Authorization", "") != f"Bearer {os.environ['RAGFLOW_BETA_TOKEN']}":
                return httpx.Response(401, json={"code": 401, "message": "Unauthorized"})
            return httpx.Response(200, content=b"png-bytes", headers={"content-type": "image/png"})
        return httpx.Response(200, json=history, headers={"content-type": "application/json"})

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", MockAsyncClient)


async def test_history_reference_chunk_image_loads_via_img_tag_without_auth_header(
    client, app, monkeypatch
):
    """引用 chunk 的 image_id 由 <img src> 渲染(无 Authorization header、无 portal_ticket)。

    history 响应须为 chunk.image_id 签发票据并改写,使 <img> 凭 cookie+票据加载,
    与既有缩略图票据链对称(Issue 82/84)。
    """
    token, dialog_id = await _login_and_get_token(client)
    session_id = "bug-history-ref-chunk-image"
    image_id = "chunk-img-001"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    history = {
        "code": 0,
        "data": {
            "session_id": session_id,
            "reference": [
                {
                    "chunks": [{"document_id": "doc-allowed", "image_id": image_id}],
                    "doc_aggs": [{"doc_id": "doc-allowed", "doc_name": "劳动法.pdf"}],
                }
            ],
        },
    }
    _mock_ragflow_rejects_image_without_beta_token(
        monkeypatch, history=history, image_id=image_id, captured=captured
    )

    # 1. axios 式加载 history(带 Authorization)→ 建立引用 + 应改写 image_id 带票据
    history_resp = await client.get(
        f"/api/v1/chatbots/{dialog_id}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history_resp.status_code == 200
    chunk = history_resp.json()["data"]["reference"][0]["chunks"][0]
    assert chunk["image_id"].startswith(
        f"{image_id}?portal_ticket="
    ), f"image_id 应被改写为带 portal_ticket,实际: {chunk['image_id']}"

    # 2. <img> 式请求:无 Authorization header,仅 Portal cookie(客户端自动携带)
    img_resp = await client.get(f"/api/v1/documents/images/{chunk['image_id']}")
    assert img_resp.status_code == 200, f"引用 chunk 图片应可通过 cookie+票据加载,实际: {img_resp.status_code}"
    assert img_resp.content == b"png-bytes"


async def test_sse_reference_chunk_image_gets_ticket_and_loads_without_auth_header(
    client, app, monkeypatch
):
    """新回答(流式)引用 chunk 的 image_id 也须带票据,<img> 凭 cookie+票据加载。"""
    token, dialog_id = await _login_and_get_token(client)
    session_id = "bug-sse-ref-chunk-image"
    image_id = "sse-chunk-img-001"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    captured = []
    reference = {
        "chunks": [{"document_id": "doc-from-sse", "image_id": image_id}],
        "doc_aggs": [{"doc_id": "doc-from-sse", "doc_name": "劳动法.pdf"}],
    }
    sse_body = (
        "data: "
        + json.dumps(
            {
                "code": 0,
                "data": {
                    "answer": "法定节假日[ID:0]",
                    "session_id": session_id,
                    "reference": reference,
                    "final": True,
                },
            },
            ensure_ascii=False,
        )
        + "\n\n"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization", ""),
            }
        )
        if request.url.path.endswith(f"/chatbots/{dialog_id}/completions"):
            return httpx.Response(
                200, content=sse_body, headers={"content-type": "text/event-stream"}
            )
        if request.url.path.endswith(f"/api/v1/documents/images/{image_id}"):
            if request.headers.get("Authorization", "") != f"Bearer {os.environ['RAGFLOW_BETA_TOKEN']}":
                return httpx.Response(401, json={"code": 401, "message": "Unauthorized"})
            return httpx.Response(200, content=b"png-bytes", headers={"content-type": "image/png"})
        return httpx.Response(404, json={"code": 404})

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("portal.gateway.httpx.AsyncClient", MockAsyncClient)

    completion = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "法定节假日有哪些？", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = await completion.aread()
    assert completion.status_code == 200

    # 解析 SSE 响应,提取改写后的 image_id
    parsed_image_id = None
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            for chunk in payload.get("data", {}).get("reference", {}).get("chunks", []):
                if chunk.get("image_id"):
                    parsed_image_id = chunk["image_id"]
    assert parsed_image_id is not None, "SSE 引用应包含 image_id"
    assert parsed_image_id.startswith(
        f"{image_id}?portal_ticket="
    ), f"SSE 引用 chunk image_id 应被改写为带 portal_ticket,实际: {parsed_image_id}"

    # <img> 式请求:无 Authorization,仅 cookie
    img_resp = await client.get(f"/api/v1/documents/images/{parsed_image_id}")
    assert img_resp.status_code == 200, f"流式引用 chunk 图片应可通过 cookie+票据加载,实际: {img_resp.status_code}"
    assert img_resp.content == b"png-bytes"


# ---------------------------------------------------------------------------
# Bug 2: T_short 停留过久后 401 — 滑动过期(touch)修复
# ---------------------------------------------------------------------------


def test_token_store_touch_extends_expiration(monkeypatch):
    """TokenStore.touch 应延长令牌的过期时间(滑动过期)。"""
    store = TokenStore()
    fake_now = [1000.0]
    monkeypatch.setattr("portal.gateway.time.time", lambda: fake_now[0])

    token = store.issue("user", "share", ttl_seconds=100)
    # 原始过期时间 = 1100

    # 50 秒后,touch 延长 100 秒(新过期时间 = 1050 + 100 = 1150)
    fake_now[0] = 1050.0
    store.touch(token, ttl_seconds=100)

    # 120 秒后(超过原始 TTL 1100,但在 touch 延长后的 1150 内)
    fake_now[0] = 1120.0
    assert store.validate(token) is not None, "touch 应使 token 在原始 TTL 过期后仍有效"

    # 160 秒后(超过 touch 延长后的 1150)
    fake_now[0] = 1160.0
    assert store.validate(token) is None, "token 应在 touch 延长后的 TTL 过期后失效"


def test_token_store_touch_does_not_revive_revoked_token(monkeypatch):
    """touch 不应复活已撤销的令牌。"""
    store = TokenStore()
    monkeypatch.setattr("portal.gateway.time.time", lambda: 1000.0)
    token = store.issue("user", "share", ttl_seconds=100)
    store.revoke(token)

    store.touch(token, ttl_seconds=100)
    assert store.validate(token) is None, "已撤销令牌不应被 touch 复活"


async def test_sse_request_touches_t_short_to_extend_ttl(client, app, monkeypatch):
    """活跃 SSE 请求应延长 T_short 有效期(滑动过期),避免停留过久后 401。

    时序(T_short TTL = 2s):
      t=0    签发 token(原始过期 = t+2)
      t=1.5  第一条消息(仍有效 1.5 < 2)→ touch 续期到 t=1.5+2=3.5
      t=3.0  第二条消息:t=3.0 > 原始过期 2.0(无 touch 会 401),
             但 < touch 后的 3.5(有 touch 则 200)
    """
    token, dialog_id = await _login_and_get_token(client)
    session_id = "bug2-sliding-ttl"
    app.state.session_store.bind(
        session_id=session_id,
        share_page_id="sp_default",
        portal_user_id="u_admin",
        ragflow_resource_id=dialog_id,
    )
    # 用极短 TTL 重新签发 token(2 秒)
    short_token = app.state.token_store.issue("u_admin", "sp_default", ttl_seconds=2)
    _mock_sse_reference_and_thumbnails(
        monkeypatch, session_id=session_id, dialog_id=dialog_id, captured=[]
    )

    # 等 1.5 秒:仍处于原始 TTL 内(1.5 < 2),第一条消息应成功并触发 touch
    time_module.sleep(1.5)
    completion1 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "test", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {short_token}"},
    )
    await completion1.aread()
    assert completion1.status_code == 200, f"第一条消息应成功,实际: {completion1.status_code}"

    # 再等 1.5 秒:t=3.0,已超过原始 TTL(2.0)但仍在 touch 续期窗口内(3.5)
    time_module.sleep(1.5)

    # 第二条消息:若 touch 生效,token 仍有效 → 200;否则(无 touch)→ 401
    completion2 = await client.post(
        f"/api/v1/chatbots/{dialog_id}/completions",
        json={"question": "test2", "stream": True, "session_id": session_id},
        headers={"Authorization": f"Bearer {short_token}"},
    )
    await completion2.aread()
    assert completion2.status_code == 200, (
        f"滑动过期应使 T_short 在活跃请求后仍有效,实际: {completion2.status_code}"
    )
