"""
Red-capable probe for: 后台管理页面没有全局滚动条,内容超出屏幕看不到。

驱动 styles.css 的真实布局规则(probe.html <link> 源 styles.css),用 admin 页面
DOM 骨架(顶栏 + tabs + app-main > section > 表单 + 25 行 table),断言用户报告
的精确症状:

  内容溢出 .app-main,但 .app-main overflow:hidden 把溢出部分裁掉,且页面级
  也无滚动条(.app-layout overflow:hidden)→ 超出内容既不能页面滚动也不能容器
  滚动 → "看不到"。

红灯条件(bug 存在时全部成立):
  1. .app-main scrollHeight > clientHeight  (内容溢出容器)
  2. .app-main overflowY 计算 === 'hidden'  (容器裁切,无法滚动看溢出内容)
  3. documentElement scrollHeight <= innerHeight (无页面级滚动条)
  4. table 最后一行 bottom > innerHeight      (最后一行在视口外)

修复后(给 admin 内容区 flex:1 + min-height:0 + overflow-y:auto):
  条件 2 变 'auto',溢出内容可在容器内滚动 → probe 转 green。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROBE_HTML = Path(__file__).resolve().parent / "probe.html"
STYLES_CSS = Path(__file__).resolve().parents[2] / "portal-extension" / "frontend" / "src" / "styles.css"


def main() -> int:
    if not STYLES_CSS.exists():
        print(f"FATAL: styles.css not found at {STYLES_CSS}", file=sys.stderr)
        return 2

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 800x600 模拟小窗口,CONTEXT.md §8 Issue 38 验收口径
        ctx = browser.new_context(viewport={"width": 800, "height": 600})
        page = ctx.new_page()
        page.goto(PROBE_HTML.as_uri())
        page.wait_for_selector("table.admin-table tbody tr")

        metrics = page.evaluate(
            """
            () => {
              const appMain = document.querySelector('.app-main');
              const cs = window.getComputedStyle(appMain);
              const lastRow = document.querySelector('table.admin-table tbody tr:last-child');
              const lastRect = lastRow.getBoundingClientRect();
              // 实际滚动容器:admin 根 section(修复后)或 app-main(bug 时)
              const section = appMain.querySelector(':scope > section');
              const sectionCs = section ? window.getComputedStyle(section) : null;
              // 滚动容器到底,看最后一行能否进入视口
              const scrollContainer = (section && sectionCs && sectionCs.overflowY === 'auto') ? section : appMain;
              const beforeBottom = lastRect.bottom;
              scrollContainer.scrollTop = scrollContainer.scrollHeight;
              const afterBottom = lastRow.getBoundingClientRect().bottom;
              return {
                appMainOverflowY: cs.overflowY,
                appMainScrollHeight: appMain.scrollHeight,
                appMainClientHeight: appMain.clientHeight,
                sectionOverflowY: sectionCs ? sectionCs.overflowY : null,
                docScrollHeight: document.documentElement.scrollHeight,
                innerHeight: window.innerHeight,
                lastRowBottomBeforeScroll: beforeBottom,
                lastRowBottomAfterScroll: afterBottom,
              };
            }
            """
        )

        print(f"[probe] metrics={metrics}")

        # bug 存在 = 4 条同时成立:app-main overflow:hidden 裁切 + 内容溢出 app-main
        # + 无页面级滚动条 + 最后一行在视口外(且无 section 滚动容器兜底)。
        bug_present = (
            metrics["appMainOverflowY"] == "hidden"
            and metrics["sectionOverflowY"] != "auto"
            and metrics["appMainScrollHeight"] > metrics["appMainClientHeight"]
            and metrics["docScrollHeight"] <= metrics["innerHeight"]
            and metrics["lastRowBottomBeforeScroll"] > metrics["innerHeight"]
        )

        # 修复有效:无页面级滚动条 AND 滚动容器能把最后一行滚入视口(用户能看到)
        fixed = (
            not bug_present
            and metrics["docScrollHeight"] <= metrics["innerHeight"]
            and metrics["lastRowBottomAfterScroll"] <= metrics["innerHeight"]
        )

        browser.close()

    if bug_present:
        print(
            "\n[probe] RED — bug 复现:.app-main overflow:hidden 裁切溢出内容,"
            "无页面级滚动条,超出部分看不到。"
        )
        print(
            f"  appMainOverflowY={metrics['appMainOverflowY']!r} "
            f"sectionOverflowY={metrics['sectionOverflowY']!r} "
            f"appMain scrollHeight={metrics['appMainScrollHeight']} "
            f"clientHeight={metrics['appMainClientHeight']} "
            f"docScrollHeight={metrics['docScrollHeight']} "
            f"innerHeight={metrics['innerHeight']} "
            f"lastRowBottomBefore={metrics['lastRowBottomBeforeScroll']}"
        )
        return 1

    if not fixed:
        print(
            "\n[probe] AMBIGUOUS — 既非 bug 形态也非预期修复形态,需人工核查 metrics。",
            file=sys.stderr,
        )
        return 2

    print(
        "\n[probe] GREEN — admin 内容区可在容器内滚动,无裁切丢失,无页面级滚动条。"
        f" sectionOverflowY={metrics['sectionOverflowY']!r} "
        f"lastRowBottomAfterScroll={metrics['lastRowBottomAfterScroll']} "
        f"<= innerHeight={metrics['innerHeight']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
