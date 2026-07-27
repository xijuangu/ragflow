# 当前 Issue 追踪

本文件记录 `portal-extension` 当前待实施的纵向切片。已完成的历史 Issue 1–84、验收记录和阶段性决策保留在 [archive/ISSUES.md](archive/ISSUES.md)，不在本文件重复维护。

## Issue 85：让引用文档完整预览沿用 Portal 受限授权

> 状态：✅ 已完成（2026-07-14）。后端测试覆盖成功预览、越权文档、撤权及原生 token 透传；nginx 精确 location 已部署；生产 Playwright 验证“劳动法历史回答 → 点击 `.docx` 文档卡片 → preview 200 且 iframe 不进入登录页”。部署踩坑（nginx 配置漏更新）记录在 `CONTEXT.md` §8。

## What to build

用户在 Portal 嵌入的 RAGFlow 会话中点击回答末尾的引用文档卡片时，完整文件预览请求必须经过 Portal 网关，而不是让携带 `pt_` 的请求直接进入 RAGFlow 并因 401 跳转登录页。网关只允许预览当前短期令牌已通过 history 或 SSE 验证的引用文档，并在校验 Portal 会话、分享页授权和令牌状态后使用服务端 beta token 请求 RAGFlow。

## Acceptance criteria

- [x] 已登录 Portal 的用户打开自己有权访问的引用会话并点击引用文档卡片时，`GET /api/v1/documents/{doc_id}/preview` 返回 200 和原始文件内容，iframe 不跳转 `/login`，无需建立 RAGFlow 登录态。
- [x] Portal preview 代理复用现有引用访问校验：有效 `pt_`、当前 Portal 用户、分享页 grant/启用状态以及文档属于该令牌的 history/SSE 引用集合；未引用文档返回 403，失效或撤销状态保持既有语义。
- [x] 无 Portal token 的原生 RAGFlow JWT/API/BETA 预览请求保持透传，不因新增代理回归。
- [x] nginx 仅把 `/api/v1/documents/{doc_id}/preview` 精确分流到 Portal，其他 RAGFlow 文档接口不扩大代理范围。
- [x] 后端自动化测试覆盖成功预览、越权文档、撤权及原生 token 透传；生产 Playwright 覆盖“劳动法历史回答 → 点击 `.docx` 文档卡片 → preview 200 且 iframe 不进入登录页”。
- [x] API、架构、部署与排障文档同步更新。
