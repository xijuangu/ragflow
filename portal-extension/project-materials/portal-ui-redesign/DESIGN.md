# RAGFlow 权限门户 — UI 重设计系统

> 现代极简(Linear / Vercel)调性。三方向对比稿,供选定后全屏 rollout。
> 产出为 HTML 设计稿,落在工作区 `portal-ui-redesign/`,不污染代码仓库,不动原有前端逻辑。

## 真实屏幕清单(来自 portal-extension/frontend/src)

| 路由 | 文件 | 屏幕 |
|---|---|---|
| `/login` | LoginPage.tsx | 登录 |
| `/share-pages` | SharePagesPage.tsx | 分享页列表(用户首页) |
| `/share-pages/:id` | SharePageDetailPage.tsx | 分享页详情(iframe Chat) |
| `/admin/users` | UsersAdminPage.tsx | 用户管理 |
| `/admin/groups` | GroupsAdminPage.tsx | 用户组 |
| `/admin/share-pages` | SharePagesAdminPage.tsx | 分享页(管理) |
| `/admin/grants` | GrantsAdminPage.tsx | 授权 |
| `/admin/sessions` | SessionsAdminPage.tsx | 会话搜索 |
| `/admin/audit` | AuditLogsAdminPage.tsx | 审计日志 |

现有后台为横向 tab(`AdminLayout.tsx`)。重设计建议改为**左侧栏 + 顶栏**,更适合 6+ 项管理后台,属布局层重构,逻辑不动。

## 共享排印规则(三方向通用)

- 字号阶:Display 48 / H1 32 / H2 24 / H3 20 / Body 16 / Small 13 / Caption 11
- 行高:Display/H1(≥32px)1.1–1.2;Body 1.55;Small 1.5
- 字距:Body 0;Small 0.01em;UI 标签/按钮 0.02em;ALL CAPS 0.06–0.1em;≥32px 标题 −0.02em
- 字体:系统无衬线(SF Pro / Segoe UI / system-ui);数字 `font-variant-numeric: tabular-nums`;ID/时间戳用 mono
- 正文限宽 65ch;三档字重(400 读 / 550 强调 / 600 标题)

## 共享色彩规则

- 中性 70–90% 像素;单一强调色 5–10%;语义色 0–5%(success/warn/danger)
- **每屏强调色至多 2 处可见**:典型 = 活跃导航项 + 主 CTA。状态用语义色,不用强调色
- 正文对比度 ≥4.5:1;大字/组件 ≥3:1
- 禁:默认 indigo(#6366f1 等)、信任渐变 hero、emoji 功能图标、圆角卡左边框强调、发明指标、暖米/桃/粉底

## 响应式断点

360 / 390 / 430 / 600 / 768 / 820 / 1024 / 1280 / 1440 / 1920。移动端侧栏收为抽屉/底栏,表格转卡片,44px 命中区。

---

## 方向 D1 — Graphite(浅色 · 冷蓝紫)

最忠于 Linear/Vercel 的稳。近白画布,墨色前景,单一蓝紫强调色,系统无衬线,紧字距,发丝边,毛玻璃粘性顶栏。

```css
:root {
  --bg: oklch(99% 0.002 240);
  --surface: oklch(100% 0 0);
  --surface-2: oklch(97% 0.004 240);
  --fg: oklch(18% 0.012 250);
  --muted: oklch(54% 0.012 250);
  --border: oklch(92% 0.005 250);
  --accent: oklch(58% 0.18 255);
  --accent-fg: oklch(99% 0.002 240);
  --success: oklch(58% 0.14 150);
  --warn: oklch(70% 0.15 70);
  --danger: oklch(56% 0.18 25);
  --font-display: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
  --font-body: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
  --font-mono: ui-monospace, 'SF Mono', Menlo, monospace;
  --radius: 8px;
}
```
姿态:发丝边(1px)、无阴影(除下拉/模态)、紧字距、粘性毛玻璃顶栏、tabular 数字。

## 方向 D2 — Midnight(深色 · 青)

工程/工具气质。深画布浅前景,提亮青蓝强调色,半透明白发丝边。

```css
:root {
  --bg: oklch(17% 0.006 250);
  --surface: oklch(20% 0.008 250);
  --surface-2: oklch(24% 0.01 250);
  --fg: oklch(93% 0.008 250);
  --muted: oklch(62% 0.012 250);
  --border: oklch(30% 0.008 250);
  --border-soft: rgba(255,255,255,0.08);
  --accent: oklch(72% 0.14 220);
  --accent-fg: oklch(15% 0.01 250);
  --success: oklch(70% 0.14 150);
  --warn: oklch(78% 0.15 70);
  --danger: oklch(64% 0.18 25);
  --font-display: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
  --font-body: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
  --font-mono: ui-monospace, 'SF Mono', Menlo, monospace;
  --radius: 8px;
}
```
姿态:半透明白边、轻微 elevation、毛玻璃深顶栏、强调色提亮以保暗底对比。

## 方向 D3 — Coral(浅色 · 暖珊瑚)

同是极简但更"人"。冷中性浅底 + 暖珊瑚强调色,暖冷对比是它的人格。

```css
:root {
  --bg: oklch(98.5% 0.003 240);
  --surface: oklch(100% 0 0);
  --surface-2: oklch(96.5% 0.005 240);
  --fg: oklch(19% 0.015 250);
  --muted: oklch(52% 0.014 250);
  --border: oklch(91% 0.006 240);
  --accent: oklch(64% 0.15 40);
  --accent-fg: oklch(99% 0.003 40);
  --success: oklch(58% 0.14 150);
  --warn: oklch(70% 0.15 70);
  --danger: oklch(56% 0.18 25);
  --font-display: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
  --font-body: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
  --font-mono: ui-monospace, 'SF Mono', Menlo, monospace;
  --radius: 10px;
}
```
姿态:同发丝边,略大圆角(10px),珊瑚强调,其余与 Graphite 同。

## 选定后的 rollout 计划

1. 锁方向 → 绑 `:root` 令牌到所有屏幕
2. 逐屏出 HTML:登录 / 分享页列表 / 分享页详情(Chat iframe 容器)/ 管理后台布局 + 六管理页 / 悬浮组件
3. 每屏含桌面 + 平板 + 移动三态,真实文案,可交互控件(JS tabs/抽屉/筛选/搜索)
4. `index.html` 作启动器链到各屏
5. 自检:排印 + 色彩 + 反 AI-slop + 响应式无横滚
