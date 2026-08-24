"""combobox 下拉布局回归(2026-08-24 竖排 bug)。

背景:option 是 flex 行(label + sublabel),sublabel 为不可断行的长串
(32 位 hex 资源 ID / 邮箱)时,可按字换行的 CJK label 被挤到
min-content=1 字宽 → 文字竖排(选项高 81-124px)。修复 = option 两段
nowrap + 省略号,下拉按内容自适应加宽(480px 封顶)。

同一 bug 的姊妹形态:下拉是绝对定位,被所在卡片的 overflow:hidden 裁切
(授权表单卡/组详情卡/带筛选栏的 card-table 卡 — 卡片越矮下拉剩得越窄)。
修复 = 含 combobox 的卡片一律 overflow:visible。

回归断言:① 逐 combobox 选项单行(高 ≤ 44px)且无横向溢出;
② 逐卡片 overflow=visible,且下拉溢出卡片底边的部分真实渲染
(elementFromPoint 在卡片外命中 option)。
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
    return listbox_id


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


def _open_and_get_dropdown_box(page: Page, input_locator):
    input_locator.click()
    listbox_id = input_locator.get_attribute("aria-controls")
    dropdown = page.locator(f"[id='{listbox_id}']")
    expect(dropdown).to_be_visible()
    page.wait_for_timeout(200)
    return dropdown.bounding_box()


def _assert_dropdown_escapes_clipping(page: Page, input_locator, card_selector: str, name: str) -> None:
    """下拉整体必须真实可见:组件按剩余空间自动选向下/向上弹,
    无论朝向,下拉中点必须命中 option(不被任何滚动容器/卡片裁切)。"""
    box = _open_and_get_dropdown_box(page, input_locator)
    input_box = input_locator.bounding_box()
    assert box and input_box, f"{name}: 取不到下拉/input 几何"
    # 中点探测(下拉中部,任何朝向都在下拉实体内)
    probe = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
    hit = page.evaluate(
        "([x, y]) => { const el = document.elementFromPoint(x, y);"
        " return el ? (el.closest('[role=option]') ? 'option' : el.tagName) : 'none'; }",
        probe,
    )
    assert hit == "option", (
        f"{name}: 下拉中点命中 {hit!r}(应为 option)— 下拉仍被裁切"
    )
    # 下拉必须在 input 的上方或下方完整弹出(不与 input 重叠、不半截)
    fully_below = box["y"] >= input_box["y"] + input_box["height"] - 2
    fully_above = box["y"] + box["height"] <= input_box["y"] + 2
    assert fully_below or fully_above, (
        f"{name}: 下拉与 input 重叠/半截(box={box}, input={input_box})"
    )
    # 方向语义(2026-08-24「上拉条」回归锚点):视口剩余空间充足(≥250px)时
    # 必须向下弹;仅空间不足才允许向上翻转
    drop_up = page.evaluate(
        "() => document.querySelector('.combobox-dropdown').classList.contains('drop-up')"
    )
    viewport_h = page.evaluate("() => window.innerHeight")
    space_below = viewport_h - (input_box["y"] + input_box["height"])
    if space_below >= 250:
        assert not drop_up, (
            f"{name}: 视口剩余 {space_below:.0f}px 充足却向上翻转(上拉条回归)"
        )
    page.keyboard.press("Escape")


@pytest.mark.parametrize(
    ("path", "input_name", "card_selector"),
    [
        # 授权表单卡(admin-form card,2026-08-24 首例)
        ("/admin/grants", "#grant-share-page", "form.admin-form.card"),
        # 用量统计筛选栏所在的 card-table 卡(2026-08-24 第二例)
        ("/admin/usage", "分享页", ".card.card-table"),
        # 会话筛选栏(同样在 card-table 卡,防复现)
        ("/admin/sessions", "用户筛选", ".card.card-table"),
    ],
)
def test_combobox_dropdown_not_clipped_by_card(
    page: Page, base_url: str, path: str, input_name: str, card_selector: str
):
    login_as_admin(page, base_url)
    page.goto(portal_url(base_url, path), wait_until="domcontentloaded")
    input_locator = (
        page.locator(input_name) if input_name.startswith("#") else page.get_by_role("combobox", name=input_name)
    )
    expect(input_locator).to_be_visible()
    _assert_dropdown_escapes_clipping(page, input_locator, card_selector, f"{path}·{input_name}")
    # 卡片本体不得再用 overflow:hidden 裁切下拉(样式层锚点)
    overflow = page.eval_on_selector(card_selector, "el => getComputedStyle(el).overflow")
    assert overflow == "visible", f"{path}: 卡片 overflow={overflow!r},下拉会被裁切"
