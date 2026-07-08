import re

from playwright.sync_api import Page, expect

from test.playwright.portal_extension.helpers import (
    api_delete,
    expect_no_portal_errors,
    find_admin_user,
    login_as_admin,
    portal_url,
    select_option_containing,
    unique_name,
)


def test_admin_create_grant_revoke_and_delete_user(page: Page, base_url: str):
    """Creates only a temporary user and cleans it up through the admin UI."""
    username = unique_name("user")
    email = f"{username}@example.test"
    password = "Pw-test-12345"
    created_user_id = None

    try:
        login_as_admin(page, base_url)
        page.get_by_role("link", name="管理后台").click()
        expect(page.get_by_role("heading", name="用户管理")).to_be_visible()

        page.get_by_label("用户名").fill(username)
        page.get_by_label("邮箱").fill(email)
        page.get_by_label("初始密码").fill(password)
        page.get_by_role("button", name="创建用户").click()

        row = page.get_by_role("row").filter(has_text=username)
        expect(row).to_be_visible()
        created = find_admin_user(page, base_url, username)
        assert created is not None, f"Created user {username} not found via admin API"
        created_user_id = created["id"]

        row.get_by_role("button", name="禁用").click()
        expect(row).to_contain_text("禁用")
        row.get_by_role("button", name="启用").click()
        expect(row).to_contain_text("启用")

        page.goto(portal_url(base_url, "/admin/grants"), wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="授权管理")).to_be_visible()
        expect(page.locator("#grant-share-page")).not_to_have_value("")

        page.locator("#grant-subject-type").select_option("user")
        select_option_containing(page.locator("#grant-subject-id"), username)
        page.get_by_role("button", name="授权").click()

        grant_row = page.get_by_test_id(f"grant-row-user-{created_user_id}")
        expect(grant_row).to_be_visible()
        expect(grant_row).to_contain_text(username)

        grant_row.get_by_role("button", name="撤销").click()
        expect(grant_row).to_have_count(0)

        page.goto(portal_url(base_url, "/admin/users"), wait_until="domcontentloaded")
        row = page.get_by_role("row").filter(has_text=username)
        expect(row).to_be_visible()
        page.once("dialog", lambda dialog: dialog.accept())
        row.get_by_role("button", name="硬删除").click()
        expect(page.get_by_role("row").filter(has_text=username)).to_have_count(0)

        assert find_admin_user(page, base_url, username) is None
        expect_no_portal_errors(page)
    finally:
        if created_user_id:
            api_delete(page, base_url, f"/admin/users/{created_user_id}")


def test_admin_create_group_form_is_available(page: Page, base_url: str):
    """Group deletion is not exposed, so this verifies the creation surface without persisting data."""
    login_as_admin(page, base_url)
    page.goto(portal_url(base_url, "/admin/groups"), wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="用户组管理")).to_be_visible()
    expect(page.get_by_label("用户组名称")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("创建用户组|创建中"))).to_be_visible()
    expect_no_portal_errors(page)
