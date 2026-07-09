import os
import re
import time
from typing import Callable
from urllib.parse import quote
from urllib.parse import urljoin

import pytest
from playwright.sync_api import Page, expect


ADMIN_TABS = [
    ("/admin/users", "用户管理"),
    ("/admin/groups", "用户组管理"),
    ("/admin/share-pages", "分享页管理"),
    ("/admin/grants", "授权管理"),
    ("/admin/sessions", "会话搜索"),
    ("/admin/audit", "审计日志"),
]


def portal_base_url(base_url: str) -> str:
    explicit = os.getenv("PORTAL_E2E_BASE_URL")
    if explicit:
        return explicit.rstrip("/")

    base = (os.getenv("BASE_URL") or base_url).rstrip("/")
    base_path = os.getenv("PORTAL_E2E_BASE_PATH", "/portal").strip()
    if not base_path:
        return base
    if not base_path.startswith("/"):
        base_path = "/" + base_path
    return f"{base}{base_path}".rstrip("/")


def portal_url(base_url: str, path: str) -> str:
    base = portal_base_url(base_url).rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def admin_credentials() -> tuple[str, str]:
    username = (
        os.getenv("PORTAL_E2E_ADMIN_USERNAME")
        or os.getenv("PORTAL_ADMIN_USERNAME")
        or os.getenv("E2E_ADMIN_USERNAME")
        or "admin"
    )
    password = (
        os.getenv("PORTAL_E2E_ADMIN_PASSWORD")
        or os.getenv("PORTAL_ADMIN_PASSWORD")
        or os.getenv("E2E_ADMIN_PASSWORD")
    )
    if not password:
        pytest.skip(
            "Set PORTAL_E2E_ADMIN_PASSWORD or PORTAL_ADMIN_PASSWORD to run portal Playwright tests."
        )
    return username, password


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def login_as_admin(page: Page, base_url: str) -> None:
    username, password = admin_credentials()
    page.goto(portal_url(base_url, "/login"), wait_until="domcontentloaded")
    expect(page.get_by_label("用户名")).to_be_visible()
    page.get_by_label("用户名").fill(username)
    page.get_by_label("密码").fill(password)
    page.get_by_role("button", name=re.compile("^登录$")).click()
    expect(page.get_by_role("heading", name="我的分享页")).to_be_visible()
    expect(page.get_by_role("link", name="管理后台")).to_be_visible()


def expect_no_portal_errors(page: Page) -> None:
    alert_errors = page.locator(".alert-error")
    for idx in range(alert_errors.count()):
        expect(alert_errors.nth(idx)).not_to_contain_text(re.compile("加载.*失败"))
    diag = getattr(page, "_diag", {})
    console_errors = [
        err
        for err in diag.get("console_errors", [])
        if "favicon" not in err
        and "ResizeObserver loop" not in err
        and "Failed to load resource: the server responded with a status of 403" not in err
    ]
    page_errors = diag.get("page_errors", [])
    assert not console_errors, "\n".join(console_errors)
    assert not page_errors, "\n".join(page_errors)


def unique_name(prefix: str) -> str:
    return f"pw-{prefix}-{int(time.time() * 1000)}"


def api_get_json(page: Page, base_url: str, path: str) -> dict:
    response = page.request.get(portal_url(base_url, path))
    assert response.ok, f"GET {path} failed: {response.status} {response.text()[:300]}"
    return response.json()


def api_post_json(page: Page, base_url: str, path: str, body: dict | None = None) -> dict:
    response = page.request.post(portal_url(base_url, path), data=body or {})
    assert response.ok, f"POST {path} failed: {response.status} {response.text()[:300]}"
    return response.json()


def api_delete(page: Page, base_url: str, path: str) -> None:
    response = page.request.delete(portal_url(base_url, path))
    assert response.status in {200, 204, 404}, (
        f"DELETE {path} failed: {response.status} {response.text()[:300]}"
    )


def find_admin_user(page: Page, base_url: str, username: str) -> dict | None:
    body = api_get_json(page, base_url, "/admin/users")
    for user in body.get("users", []):
        if user.get("username") == username:
            return user
    return None


def first_accessible_share_page(
    page: Page,
    base_url: str,
    *,
    embed_type: str | None = None,
    ragflow_type: str | None = None,
) -> dict:
    body = api_get_json(page, base_url, "/share-pages")
    for share_page in body.get("share_pages", []):
        if embed_type and share_page.get("embed_type") != embed_type:
            continue
        if ragflow_type and share_page.get("ragflow_type") != ragflow_type:
            continue
        return share_page
    pytest.skip(
        "No accessible share page matches "
        f"embed_type={embed_type!r}, ragflow_type={ragflow_type!r}."
    )


def quote_path(value: str) -> str:
    return quote(value, safe="")


def wait_until(
    predicate: Callable[[], dict | None],
    *,
    timeout_ms: int,
    interval_ms: int = 1000,
    description: str,
) -> dict:
    deadline = time.time() + (timeout_ms / 1000)
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value is not None:
                return value
        except Exception as exc:  # noqa: BLE001 - keep polling transient API/UI states.
            last_error = exc
        time.sleep(interval_ms / 1000)
    suffix = f" Last error: {last_error}" if last_error else ""
    raise AssertionError(f"Timed out waiting for {description}.{suffix}")


def wait_for_audit_action(
    page: Page,
    base_url: str,
    action: str,
    *,
    target_id: str | None = None,
    meta_subject_id: str | None = None,
    timeout_ms: int = 10_000,
) -> dict:
    def find_log() -> dict | None:
        body = api_get_json(page, base_url, f"/admin/audit-logs?action={quote_path(action)}&limit=50")
        for log in body.get("audit_logs", []):
            if target_id is not None and log.get("target_id") != target_id:
                continue
            meta = log.get("meta") or {}
            if meta_subject_id is not None and meta.get("subject_id") != meta_subject_id:
                continue
            return log
        return None

    return wait_until(
        find_log,
        timeout_ms=timeout_ms,
        interval_ms=500,
        description=f"audit action {action}",
    )


def select_option_containing(select_locator, text: str) -> str:
    option = select_locator.locator("option", has_text=text).first
    expect(option).to_have_count(1)
    value = option.get_attribute("value")
    assert value, f"Option containing {text!r} has no value"
    select_locator.select_option(value)
    return value
