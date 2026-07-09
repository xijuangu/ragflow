import os
import re

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect

from test.playwright.portal_extension.helpers import (
    ADMIN_TABS,
    api_delete,
    api_get_json,
    env_flag,
    expect_no_portal_errors,
    first_accessible_share_page,
    login_as_admin,
    quote_path,
    portal_url,
    wait_until,
)


def test_portal_login_share_page_and_token_shell(page: Page, base_url: str):
    """Covers the normal operator path without sending a real RAGFlow question."""
    login_as_admin(page, base_url)

    expect(page.get_by_role("heading", name="我的分享页")).to_be_visible()
    first_open = page.get_by_role("link", name="打开").first
    expect(first_open).to_be_visible()
    first_open.click()

    expect(page.get_by_role("heading", name=re.compile("分享页对话|悬浮组件嵌入"))).to_be_visible()
    expect(page.get_by_role("complementary", name="我的会话")).to_be_visible()
    expect(page.get_by_role("button", name="新建会话")).to_be_visible()

    iframe = page.locator("iframe[title='RAGFlow 对话']")
    snippet = page.get_by_test_id("widget-snippet-panel")
    expect(iframe.or_(snippet)).to_have_count(1)

    if iframe.count() > 0:
        src = iframe.first.get_attribute("src") or ""
        assert "auth=pt_" in src, f"iframe src should contain portal token auth=pt_: {src}"
        beta_token = os.getenv("RAGFLOW_BETA_TOKEN")
        if beta_token:
            assert beta_token not in src, "iframe src leaked RAGFlow beta token"
        assert "ragflow-" not in src, "iframe src should not contain RAGFlow API token prefix"

    expect_no_portal_errors(page)


def test_portal_admin_tabs_load_without_cache_regression(page: Page, base_url: str):
    """Covers all admin tabs and the Slice 44 API/SPA cache regression surface."""
    login_as_admin(page, base_url)

    page.goto(portal_url(base_url, "/admin/users"), wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="用户管理")).to_be_visible()

    for _ in range(2):
        for path, heading in ADMIN_TABS:
            page.goto(portal_url(base_url, path), wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name=heading)).to_be_visible()
            expect_no_portal_errors(page)

    html_response = page.request.get(
        portal_url(base_url, "/"),
        headers={"Accept": "text/html"},
    )
    assert html_response.ok
    cache_control = html_response.headers.get("cache-control", "")
    vary = html_response.headers.get("vary", "")
    assert "no-store" in cache_control.lower()
    assert "accept" in vary.lower()

    json_response = page.request.get(
        portal_url(base_url, "/admin/users"),
        headers={"Accept": "application/json"},
    )
    assert json_response.ok
    content_type = json_response.headers.get("content-type", "")
    assert "application/json" in content_type.lower()
    assert isinstance(json_response.json().get("users"), list)


def test_portal_share_page_chat_roundtrip_waits_for_reply(page: Page, base_url: str):
    """Optional slow test: sends a real RAGFlow question and waits for the reply to finish."""
    if not env_flag("PORTAL_E2E_RUN_CHAT"):
        pytest.skip("Set PORTAL_E2E_RUN_CHAT=1 to send a real RAGFlow chat message.")

    question = os.getenv("PORTAL_E2E_CHAT_QUESTION", "请用一句话回答：自动化测试消息已收到。")
    chat_timeout_ms = int(os.getenv("PORTAL_E2E_CHAT_TIMEOUT_MS", "180000"))
    created_session_id = None

    login_as_admin(page, base_url)
    share_page = first_accessible_share_page(
        page,
        base_url,
        embed_type="fullscreen",
        ragflow_type="chat",
    )
    share_page_id = share_page["id"]
    before = api_get_json(
        page,
        base_url,
        f"/share-pages/{quote_path(share_page_id)}/sessions",
    )
    previous_session_ids = {s["session_id"] for s in before.get("sessions", [])}

    try:
        page.goto(
            portal_url(base_url, f"/share-pages/{quote_path(share_page_id)}"),
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("heading", name="分享页对话")).to_be_visible()
        expect(page.get_by_role("complementary", name="我的会话")).to_be_visible()

        iframe = page.locator("iframe[title='RAGFlow 对话']")
        expect(iframe).to_be_visible(timeout=60_000)
        chat_frame = page.frame_locator("iframe[title='RAGFlow 对话']")

        textarea = chat_frame.get_by_test_id("chat-textarea")
        expect(textarea).to_be_visible(timeout=60_000)
        textarea.fill(question)

        send = chat_frame.get_by_test_id("chat-detail-send")
        expect(send).to_be_enabled()
        send.click()

        stream_status = chat_frame.get_by_test_id("chat-stream-status")
        saw_stream = False
        try:
            expect(stream_status).to_be_visible(timeout=15_000)
            saw_stream = True
        except PlaywrightTimeoutError:
            # Very short answers can finish before the status button is observable.
            pass
        if saw_stream:
            expect(stream_status).to_have_count(0, timeout=chat_timeout_ms)

        created = _wait_for_new_session_with_reply(
            page,
            base_url,
            share_page_id,
            previous_session_ids,
            timeout_ms=chat_timeout_ms,
        )
        created_session_id = created["session_id"]

        admin_sessions = api_get_json(
            page,
            base_url,
            f"/admin/sessions?share_page_id={quote_path(share_page_id)}&limit=100",
        )
        assert any(
            s.get("session_id") == created_session_id
            for s in admin_sessions.get("sessions", [])
        ), f"Created session {created_session_id} was not visible in admin session search"

        metadata = api_get_json(
            page,
            base_url,
            f"/admin/sessions/{quote_path(created_session_id)}",
        )
        assert metadata["session_id"] == created_session_id
        assert "messages" not in metadata
        assert "reference" not in metadata

        if env_flag("PORTAL_E2E_CHECK_ELEVATED_CHAT"):
            elevated = api_get_json(
                page,
                base_url,
                f"/admin/sessions/{quote_path(created_session_id)}?elevated=true",
            )
            assert isinstance(elevated.get("messages"), list)
            assert elevated.get("messages"), "Elevated session view returned no messages"

        expect_no_portal_errors(page)
    finally:
        if created_session_id:
            api_delete(
                page,
                base_url,
                f"/share-pages/{quote_path(share_page_id)}/sessions/{quote_path(created_session_id)}",
            )


def _wait_for_new_session_with_reply(
    page: Page,
    base_url: str,
    share_page_id: str,
    previous_session_ids: set[str],
    *,
    timeout_ms: int,
) -> dict:
    def find_session() -> dict | None:
        body = api_get_json(
            page,
            base_url,
            f"/share-pages/{quote_path(share_page_id)}/sessions",
        )
        for session in body.get("sessions", []):
            if session.get("session_id") in previous_session_ids:
                continue
            if int(session.get("message_count") or 0) >= 2:
                return session
        return None

    return wait_until(
        find_session,
        timeout_ms=timeout_ms,
        interval_ms=2_000,
        description="new chat session with at least one user message and one reply",
    )
