"""Issue 82: historical-session reference resources use scoped Portal auth."""

import json
import os
from urllib.parse import parse_qs, urlparse

import httpx

from portal.gateway import TokenStore


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
