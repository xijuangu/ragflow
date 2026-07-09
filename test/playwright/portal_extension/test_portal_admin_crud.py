import re

from playwright.sync_api import Page, expect

from test.playwright.portal_extension.helpers import (
    api_delete,
    api_get_json,
    api_post_json,
    expect_no_portal_errors,
    find_admin_user,
    login_as_admin,
    quote_path,
    portal_url,
    select_option_containing,
    unique_name,
    wait_until,
    wait_for_audit_action,
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
        share_page_id = page.locator("#grant-share-page").input_value()

        page.locator("#grant-subject-type").select_option("user")
        select_option_containing(page.locator("#grant-subject-id"), username)
        page.get_by_role("button", name="授权").click()

        grant_row = page.get_by_test_id(f"grant-row-user-{created_user_id}")
        expect(grant_row).to_be_visible()
        expect(grant_row).to_contain_text(username)

        grant_row.get_by_role("button", name="撤销").click()
        expect(grant_row).to_have_count(0)

        wait_for_audit_action(page, base_url, "user_disable", target_id=created_user_id)
        wait_for_audit_action(page, base_url, "user_enable", target_id=created_user_id)
        wait_for_audit_action(
            page,
            base_url,
            "grant_create",
            target_id=share_page_id,
            meta_subject_id=created_user_id,
        )
        wait_for_audit_action(
            page,
            base_url,
            "grant_revoke",
            target_id=share_page_id,
            meta_subject_id=created_user_id,
        )

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


def test_admin_group_form_and_member_add_remove(page: Page, base_url: str):
    """Exercises group membership on an existing group without creating undeletable group data."""
    username = unique_name("group-user")
    email = f"{username}@example.test"
    created_user_id = None
    group_id = None

    try:
        login_as_admin(page, base_url)
        created = api_post_json(
            page,
            base_url,
            "/admin/users",
            {"username": username, "email": email, "password": "Pw-test-12345"},
        )
        created_user_id = created["id"]

        groups = api_get_json(page, base_url, "/admin/groups").get("groups", [])
        if not groups:
            page.goto(portal_url(base_url, "/admin/groups"), wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="用户组管理")).to_be_visible()
            expect(page.get_by_label("用户组名称")).to_be_visible()
            expect(page.get_by_role("button", name=re.compile("创建用户组|创建中"))).to_be_visible()
            expect_no_portal_errors(page)
            return

        group = groups[0]
        group_id = group["id"]

        page.goto(portal_url(base_url, "/admin/groups"), wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="用户组管理")).to_be_visible()
        expect(page.get_by_label("用户组名称")).to_be_visible()
        expect(page.get_by_role("button", name=re.compile("创建用户组|创建中"))).to_be_visible()

        group_card = page.get_by_test_id(f"group-row-{group_id}")
        expect(group_card).to_be_visible()

        select_option_containing(group_card.get_by_label("选择用户"), username)
        group_card.get_by_role("button", name="添加").click()
        expect(group_card).to_contain_text(username)

        _wait_for_group_member_state(
            page,
            base_url,
            group_id,
            created_user_id,
            should_contain=True,
        )

        member_item = group_card.locator(".member-item").filter(has_text=username)
        expect(member_item).to_be_visible()
        member_item.get_by_role("button", name="移除").click()
        expect(member_item).to_have_count(0)

        _wait_for_group_member_state(
            page,
            base_url,
            group_id,
            created_user_id,
            should_contain=False,
        )

        expect_no_portal_errors(page)
    finally:
        if created_user_id:
            if group_id:
                api_delete(
                    page,
                    base_url,
                    f"/admin/groups/{quote_path(group_id)}/members/{quote_path(created_user_id)}",
                )
            api_delete(page, base_url, f"/admin/users/{created_user_id}")


def test_admin_create_group_and_share_page_forms_are_available(page: Page, base_url: str):
    """Creation forms are covered without persisting objects that have no delete endpoint."""
    login_as_admin(page, base_url)
    page.goto(portal_url(base_url, "/admin/groups"), wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="用户组管理")).to_be_visible()
    expect(page.get_by_label("用户组名称")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("创建用户组|创建中"))).to_be_visible()

    page.goto(portal_url(base_url, "/admin/share-pages"), wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="分享页管理")).to_be_visible()
    expect(page.get_by_label("分享页名称")).to_be_visible()
    expect(page.get_by_label("RAGFlow 资源 ID")).to_be_visible()
    expect(page.get_by_label("嵌入类型")).to_be_visible()
    expect(page.get_by_label("RAGFlow 类型")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("创建分享页|创建中"))).to_be_visible()
    expect_no_portal_errors(page)


def _wait_for_group_member_state(
    page: Page,
    base_url: str,
    group_id: str,
    user_id: str,
    *,
    should_contain: bool,
) -> dict:
    def find_group() -> dict | None:
        groups = api_get_json(page, base_url, "/admin/groups").get("groups", [])
        target = next((g for g in groups if g.get("id") == group_id), None)
        assert target is not None, f"Group {group_id} not found"
        contains = user_id in target.get("members", [])
        if contains is should_contain:
            return target
        return None

    return wait_until(
        find_group,
        timeout_ms=10_000,
        interval_ms=500,
        description=(
            f"group {group_id} member {user_id} "
            f"{'to be present' if should_contain else 'to be removed'}"
        ),
    )
