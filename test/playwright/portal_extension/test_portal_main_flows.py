import os
import re

from playwright.sync_api import Page, expect

from test.playwright.portal_extension.helpers import (
    ADMIN_TABS,
    expect_no_portal_errors,
    login_as_admin,
    portal_url,
)


def test_portal_login_share_page_and_token_shell(page: Page, base_url: str):
    """Covers the normal operator path without sending a real RAGFlow question."""
    login_as_admin(page, base_url)

    expect(page.get_by_role("heading", name="我的分享页")).to_be_visible()
    first_open = page.get_by_role("link", name="打开").first()
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

    page.get_by_role("link", name="管理后台").click()
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
