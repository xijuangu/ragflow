# Portal Extension 前端设计验收

## 结论

**passed**

本轮验收未发现 P0、P1 或 P2 级视觉与交互问题。前端可进入生产环境部署与真实 RAGFlow 联调阶段。

## 视觉基准

- 设计规范：`project-materials/portal-ui-redesign/DESIGN.md`
- 登录页基准：`project-materials/portal-ui-redesign/login.html`
- 用户管理基准：`project-materials/portal-ui-redesign/admin-users.html`
- 桌面视口：1440 × 900
- 移动视口：390 × 844

## 对比材料

| 页面 / 状态 | 设计基准 | 实现截图 | 同屏对比 |
|---|---|---|---|
| 登录页，默认配置 | `design-qa-source-login.png` | `design-qa-login-desktop.png` | `design-qa-login-comparison.png` |
| 用户管理，桌面列表 | `design-qa-source-admin-users.png` | `design-qa-admin-users-desktop.png` | `design-qa-admin-users-comparison.png` |
| 用户管理，移动卡片 | — | `design-qa-admin-users-mobile.png` | — |
| 移动侧栏展开 | — | `design-qa-admin-menu-mobile.png` | — |
| 高风险删除确认 | — | `design-qa-delete-dialog-mobile.png` | — |
| 分享页会话历史收起 | — | `design-qa-share-detail-mobile.png` | — |

## 状态与交互验证

- 登录页默认不展示未配置的 SSO 入口；设置 `VITE_SSO_ENABLED=true` 后入口恢复。
- 管理端桌面表格在 1440 × 900 下完整显示，无横向溢出。
- 管理端 390 × 844 下切换为卡片列表；操作按钮、侧栏菜单和遮罩层可用，无横向溢出。
- 永久删除和提权查看统一使用应用内对话框，支持取消、Escape、焦点恢复和处理中状态。
- 分享页移动端历史区默认收起，可展开；收起后主体区域获得更多可用高度。
- 新鲜浏览器会话加载分享页后，控制台错误和警告均为 0。

## 比对与修正记录

1. 首轮登录页对比发现表单外围多出边框、背景和内边距，与设计基准不一致，评级 P2。
2. 删除额外容器视觉后重新截图并同屏对比，表单层级、间距和视觉重心已与基准一致，问题关闭。
3. 管理端保留当前产品合同内的搜索、通知、统计和批量操作边界；未引入缺少后端能力的装饰性控件。布局、色彩、边框、圆角和信息密度遵循现有 Graphite 设计系统。

## 上线前环境验证边界

- 本地没有配置真实 `RAGFLOW_BROWSER_ORIGIN`，因此本轮验证覆盖分享页外壳、会话状态与响应式布局，不代表真实 RAGFlow iframe 已完成生产联调。
- 正式切流前需使用生产 OIDC、API 与 RAGFlow 地址完成一次登录、管理端权限、iframe 加载及退出登录冒烟。
