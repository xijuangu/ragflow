"""combobox 下拉布局回归(2026-08-24 竖排 bug)。

背景:option 是 flex 行(label + sublabel),sublabel 为不可断行的长串
(32 位 hex 资源 ID / 邮箱)时,可按字换行的 CJK label 被挤到
min-content=1 字宽 → 文字竖排(选项高 81-124px)。修复 = option 两段
nowrap + 省略号,下拉按内容自适应加宽(480px 封顶)。

回归断言(逐个 combobox):所有选项单行(高 ≤ 44px)且无横向溢出。
"""
import pytest
from playwright.sync_api import Page, expect

from test.playwright.portal_extension.helpers import login_as_admin, portal_url

# 每项:(页面路径, combobox input 定位名)
COMBOBOX_SITES = [
    ("/admin/grants", "#grant-share-page"),
    ("/admin/grants", "#grant-subject-id"),
    ("/admin/sessions", "用户筛选"),
    ("/admin/sessions", "分享页筛选"),
    ("/admin/usage", "分享页"),
]


def _open_and_assert_single_line(page: Page, input_locator, name: str) -> None:
    input_locator.click()
    listbox_id = input_locator.get_attribute("aria-controls")
    assert listbox_id, f"{name}: combobox input has no aria-controls"
    dropdown = page.locator(f"[id='{listbox_id}']")
    expect(dropdown).to_be_visible()
    page.wait_for_timeout(200)  # 等下拉完成布局

    stats = page.evaluate(
        """(listboxId) => {
          const dropdown = document.getElementById(listboxId);
          return [...dropdown.querySelectorAll('[role=option]')].map(li => ({
            text: (li.querySelector('.combobox-option-label')?.textContent || '').slice(0, 16),
            height: Math.round(li.getBoundingClientRect().height),
            hOverflow: li.scrollWidth > li.clientWidth + 2,
          }));
        }""",
        listbox_id,
    )
    assert stats, f"{name}: 下拉无选项(数据缺失?)"
    for o in stats:
        assert o["height"] <= 44, (
            f"{name}: 选项 {o['text']!r} 高 {o['height']}px(>44)— 文字竖排/换行回归"
        )
        assert not o["hOverflow"], (
            f"{name}: 选项 {o['text']!r} 横向溢出 — 下拉宽度未容纳内容"
        )


@pytest.mark.parametrize(("path", "locator_name"), COMBOBOX_SITES)
def test_combobox_options_stay_single_line(page: Page, base_url: str, path: str, locator_name: str):
    login_as_admin(page, base_url)
    page.goto(portal_url(base_url, path), wait_until="domcontentloaded")
    if locator_name.startswith("#"):
        input_locator = page.locator(locator_name)
    else:
        input_locator = page.get_by_role("combobox", name=locator_name)
    expect(input_locator).to_be_visible()
    _open_and_assert_single_line(page, input_locator, f"{path}·{locator_name}")
    page.keyboard.press("Escape")


def test_group_member_combobox_options_stay_single_line(page: Page, base_url: str):
    login_as_admin(page, base_url)
    page.goto(portal_url(base_url, "/admin/groups"), wait_until="domcontentloaded")
    first_group = page.locator("[data-testid^='group-row-']").first
    first_group.click()
    member = page.locator("[id^='member-select-']").first
    expect(member).to_be_visible()
    _open_and_assert_single_line(page, member, "admin/groups·添加成员")
    page.keyboard.press("Escape")
