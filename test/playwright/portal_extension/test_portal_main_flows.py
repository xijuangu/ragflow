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


THEME_CLASS_OBSERVER_SCRIPT = """
(() => {
  const classHistory = [];
  Object.defineProperty(window, '__portalThemeClassHistory', {
    value: classHistory,
    configurable: false,
  });
  const record = () => classHistory.push(document.documentElement.className);
  record();
  new MutationObserver(record).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
  });
})();
"""


def assert_portal_chat_uses_stable_light_theme(iframe) -> None:
    src = iframe.get_attribute("src") or ""
    assert "default_theme=light" in src
    assert not re.search(r"[?&]theme=", src), f"iframe must not force an explicit theme: {src}"

    iframe_handle = iframe.element_handle()
    assert iframe_handle is not None
    chat_frame = iframe_handle.content_frame()
    assert chat_frame is not None
    chat_frame.wait_for_function(
        "document.documentElement.classList.contains('light')",
        timeout=60_000,
    )
    theme_state = chat_frame.evaluate(
        """
        () => ({
          bodyBackground: getComputedStyle(document.body).backgroundColor,
          classHistory: window.__portalThemeClassHistory || [],
          currentClasses: document.documentElement.className,
        })
        """
    )
    rgb_values = [
        int(value)
        for value in re.findall(r"\d+", theme_state["bodyBackground"] or "")[:3]
    ]
    assert len(rgb_values) == 3, f"Could not read iframe body background: {theme_state}"
    assert min(rgb_values) >= 240, f"Iframe body is not light: {theme_state}"
    assert all(
        "dark" not in classes.split()
        for classes in theme_state["classHistory"]
    ), f"Iframe switched through dark theme during initialization: {theme_state}"


def test_portal_login_share_page_and_token_shell(page: Page, base_url: str):
    """Covers the normal operator path without sending a real RAGFlow question."""
    login_as_admin(page, base_url)

    expect(page.get_by_role("heading", name="我的分享页")).to_be_visible()
    first_open = page.get_by_role("link", name="打开").first
    expect(first_open).to_be_visible()
    first_open.click()

    expect(page.get_by_role("link", name="返回列表")).to_be_visible()
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


def test_labor_law_reference_history_loads_without_ragflow_login(page: Page, base_url: str):
    """Issue 82: fresh Portal-only login can restore referenced history and its resources."""
    share_name = os.getenv("PORTAL_E2E_REFERENCE_SHARE_NAME", "劳动法")
    session_title_query = os.getenv(
        "PORTAL_E2E_REFERENCE_SESSION_TITLE",
        "劳动合同到期不续约",
    )
    relevant_responses: list[tuple[str, int]] = []

    page.add_init_script("window.localStorage.clear()")
    page.on(
        "response",
        lambda response: relevant_responses.append((response.url, response.status))
        if any(
            marker in response.url
            for marker in ("/sessions/", "/api/v1/thumbnails", "/api/v1/documents/images/")
        )
        else None,
    )
    login_as_admin(page, base_url)

    share_pages = api_get_json(page, base_url, "/share-pages").get("share_pages", [])
    share_page = next((item for item in share_pages if item.get("name") == share_name), None)
    assert share_page is not None, f"Accessible share page {share_name!r} was not found"
    sessions = api_get_json(
        page,
        base_url,
        f"/share-pages/{quote_path(share_page['id'])}/sessions",
    ).get("sessions", [])
    target = next(
        (item for item in sessions if session_title_query in (item.get("title") or "")),
        None,
    )
    assert target is not None, f"Reference history containing {session_title_query!r} was not found"

    share_card = page.locator(".share-card", has_text=share_name)
    expect(share_card).to_have_count(1)
    share_card.get_by_role("link", name="打开").click()
    expect(page.get_by_role("heading", name=share_name)).to_be_visible()

    history_button = page.locator(
        "[data-session-item] .ci-trigger",
        has_text=session_title_query,
    )
    expect(history_button).to_be_visible()
    with page.expect_response(
        lambda response: "/api/v1/thumbnails" in response.url,
        timeout=60_000,
    ) as thumbnail_info:
        history_button.click()

    thumbnail_response = thumbnail_info.value
    assert thumbnail_response.status == 200, (
        f"thumbnail request failed: {thumbnail_response.status} {thumbnail_response.url}"
    )
    thumbnail_payload = thumbnail_response.json()
    thumbnail_data = thumbnail_payload.get("data", {}) if isinstance(thumbnail_payload, dict) else {}
    image_urls = [
        value
        for value in thumbnail_data.values()
        if isinstance(value, str) and value.startswith("/api/v1/documents/images/")
    ]
    for image_url in image_urls:
        observed_status = next(
            (status for url, status in relevant_responses if image_url in url),
            None,
        )
        if observed_status is None:
            image_response = page.wait_for_event(
                "response",
                predicate=lambda response, expected=image_url: expected in response.url,
                timeout=60_000,
            )
            observed_status = image_response.status
        assert observed_status == 200, f"document image request failed: {observed_status} {image_url}"
    iframe = page.locator("iframe[title='RAGFlow 对话']")
    expect(iframe).to_be_visible(timeout=60_000)
    expect(iframe).to_have_attribute("src", re.compile(r"[?&]session_id="))
    chat_frame = page.frame_locator("iframe[title='RAGFlow 对话']")
    expect(chat_frame.get_by_test_id("chat-textarea")).to_be_visible(timeout=60_000)
    assert all(not frame.url.rstrip("/").endswith("/login") for frame in page.frames)

    unauthorized = [(url, status) for url, status in relevant_responses if status == 401]
    assert not unauthorized, f"Referenced history emitted 401 responses: {unauthorized}"
    expect_no_portal_errors(page)


def test_labor_law_new_and_history_sessions_default_to_light_theme(
    page: Page,
    base_url: str,
):
    """Issue 83: fresh Portal embeds stay light for new and restored sessions."""
    share_name = os.getenv("PORTAL_E2E_REFERENCE_SHARE_NAME", "劳动法")
    session_title_query = os.getenv(
        "PORTAL_E2E_REFERENCE_SESSION_TITLE",
        "劳动合同到期不续约",
    )

    page.add_init_script("window.localStorage.clear()")
    page.add_init_script(THEME_CLASS_OBSERVER_SCRIPT)
    login_as_admin(page, base_url)

    share_card = page.locator(".share-card", has_text=share_name)
    expect(share_card).to_have_count(1)
    share_card.get_by_role("link", name="打开").click()
    expect(page.get_by_role("heading", name=share_name)).to_be_visible()

    iframe = page.locator("iframe[title='RAGFlow 对话']")
    expect(iframe).to_be_visible(timeout=60_000)
    expect(page.frame_locator("iframe[title='RAGFlow 对话']").get_by_test_id("chat-textarea")).to_be_visible(
        timeout=60_000,
    )
    assert_portal_chat_uses_stable_light_theme(iframe)

    history_button = page.locator(
        "[data-session-item] .ci-trigger",
        has_text=session_title_query,
    )
    expect(history_button).to_be_visible()
    history_button.click()
    expect(iframe).to_have_attribute("src", re.compile(r"[?&]session_id="))
    expect(page.frame_locator("iframe[title='RAGFlow 对话']").get_by_test_id("chat-textarea")).to_be_visible(
        timeout=60_000,
    )
    assert_portal_chat_uses_stable_light_theme(iframe)
    expect_no_portal_errors(page)


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
