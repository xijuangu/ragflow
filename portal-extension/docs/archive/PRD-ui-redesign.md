# PRD:Portal UI 重设计(D1 Graphite)

> 基于 `portal-ui-redesign/` 设计稿(Open Design 产出)。决策记录见 [CONTEXT.md §6.5](../CONTEXT.md#L149-L161)。设计系统规范见 [`portal-ui-redesign/DESIGN.md`](../../../portal-ui-redesign/DESIGN.md)。

## 1. 目标

将 portal-extension 前端视觉层从现有手写 CSS(687 行,10 个 hex 变量,无系统)升级为 D1 Graphite 设计系统(oklch token 体系 + 排印阶 + 组件 class 全套),消除 Slice 31/38/51 类 CSS 布局 bug 的根因(缺统一滚动/flex 模式),并为后续 UI 迭代建立可维护基础。

## 2. 范围边界(纯视觉换皮)

- **做**:视觉(token/排印/色彩/组件皮)、布局(admin 横向 tab → 左侧栏)、JSX className 对齐
- **不做**:新功能(全局搜索/stats/通知/drawer/批量导入)、后端工作、交互模式变更、移动端全响应式
- **必须保留**:Slice 31 `flex-shrink:0`、Slice 38 `min-height:0` + `.detail-grid flex:1`、Slice 51 `.admin-main > section` 滚动容器(映射到新设计系统)

## 3. 设计系统(基础层,所有屏共享)

以 `portal-ui-redesign/css/styles.css` 为基础替换 `frontend/src/styles.css`。

- **Token**:oklch 色彩(bg/surface×3/fg/muted/border/accent+accent-fg+accent-tint/success/warn/danger)+ 字体三族 + radius 三档 + sidebar-w/topbar-h
- **排印**:字号阶 48/32/24/20/16/13/11 + 行高 + 字距 + 三档字重 + tabular-nums
- **组件 class**:btn(5 变体)/card/stats(砍)/badge/table/form/drawer(砍)/pill/filters/topbar/sidebar/page-head
- **反 AI-slop 约束**(DESIGN.md):禁 indigo/渐变 hero/emoji 图标/左边框强调/暖米底;中性 70-90% + 强调色 5-10% + 语义色 0-5%;每屏强调色至多 2 处;对比度 ≥4.5:1

## 4. 屏级范围

| 屏 | 现有 | 设计稿 | 改动 |
|---|---|---|---|
| `/login` | 单栏表单 | 左右分屏(login-aside + login-main) | 改 JSX 结构 + 套新 CSS。aside 文案保留设计稿(含 pt_/SSE/双删,已贴合项目) |
| `/share-pages` | 列表 | topbar + 卡片网格(grid-cards) | 套 topbar + 卡片网格 CSS,逻辑不动 |
| `/share-pages/:id` | AppHeader + sessions-sidebar + iframe | topbar + detail-bar + detail-shell(conv-list + chat-main) | 视觉升级,会话列表逻辑不动(Slice 10/22/23/33 保留),iframe 容器套新皮 |
| `/admin/*`(6 屏) | AppHeader + 横向 tab + 内联表单 + table | topbar + sidebar + page-head + filter + table | AdminLayout 重构为 sidebar,6 页套新 page-head/filter/table,交互保留(内联表单/prompt/confirm) |

### 砍掉的元素(纯视觉换皮)

- 顶部全局搜索(无后端)
- stats 统计卡(无聚合接口)
- 通知铃铛(无后端)
- drawer 抽屉(交互变更,保留内联表单)
- 批量导入按钮(无后端)
- sidebar "系统-设置" 项(无页)
- 移动端卡片态 / sidebar 抽屉 / 菜单按钮(桌面优先)

### 保留的元素

- sidebar "工作区-分享页" 入口(回用户侧)
- topbar brand + avatar(用户名)+ 登出
- 表格文字操作按钮(重命名/删除,不改 icon-btn)
- 图标用内联 SVG(设计稿已是,不引 icon 库)

## 5. Slice 拆分方向(供 /to-issues 参考)

建议按"基础层 → 布局层 → 逐屏迁移"依赖顺序:

1. **设计系统基础层**:替换 styles.css(token + 排印 + 组件 class + 反 AI-slop),合并 Slice 31/38/51 修复。此时 JSX 未改,页面会"半新半旧"但可用
2. **AppHeader/topbar**:抽 topbar 组件(brand + avatar + 登出),替换 AppHeader,全局生效
3. **AdminLayout sidebar**:横向 tab → topbar + sidebar,6 页自动套新布局
4. **LoginPage**:单栏 → 左右分屏
5. **SharePagesPage**:列表 → 卡片网格
6. **SharePageDetailPage**:detail-shell 视觉升级(会话列表逻辑不动)
7-12. **6 个 admin 页**逐一:page-head + filter + table 视觉迁移(交互不动)

## 6. 验收标准(整体)

- [ ] 9 屏视觉对齐 D1 Graphite 设计稿(允许砍掉元素的差异)
- [ ] `frontend/src/styles.css` 替换为设计系统,含 Slice 31/38/51 修复映射
- [ ] AdminLayout 为 topbar + sidebar,6 页通过 `<Outlet/>` 渲染(逻辑不动)
- [ ] 现有功能全部保留:登录/登出、分享页 CRUD、会话列表(新建/切换/重命名/删除)、iframe Chat、widget snippet、6 个 admin 页 CRUD
- [ ] 桌面 1024+ 无布局 bug,小屏 <768 不崩(内容可滚、不裁切)
- [ ] tsc 0 errors,Vitest 全绿(无回归)
- [ ] 无新后端依赖,无新 npm 依赖

## 7. Deferred 项(后续 issue 候选)

- 移动端全响应式适配(10 断点 + 卡片态 + sidebar 抽屉)
- drawer 抽屉交互(admin 新建/编辑)
- stats 聚合(前端从列表接口聚合,轻功能)
- 顶部全局搜索(需后端)
- 通知系统(需后端)
- 批量导入(需后端)
