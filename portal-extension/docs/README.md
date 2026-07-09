# 文档目录

本目录包含两类材料：

- 当前维护文档：用于接手、开发、测试、部署和运维。
- 历史追溯材料：PRD、ISSUES、原型验证、handoff，用于查证设计来源和验收过程。

## 当前维护文档

- [架构文档](architecture.md)：系统边界、请求链路、安全模型。
- [配置文档](configuration.md)：环境变量、默认值、生产建议。
- [API 文档](api.md)：普通用户、网关、管理员、公开分享接口。
- [数据模型](data-model.md)：表结构、事实源、删除策略。
- [开发与测试](development-and-testing.md)：本地运行、后端/前端/Playwright 测试。
- [部署运维](deployment-and-operations.md)：生产拓扑、部署脚本、健康检查、回滚和常见问题。
- [../project-materials/](../project-materials/README.md)：原始 PRD、原型、UI 设计稿和 RAG 验证材料。

## 历史追溯材料

历史 PRD、ISSUES、NOTES、handoff、早期决策和 UI PRD 已移动到 [archive/](archive/README.md)。这些文件保留为可查证材料，不作为当前维护入口。

## 建议阅读顺序

1. 先读 [../README.md](../README.md)。
2. 再读 [架构文档](architecture.md)、[配置文档](configuration.md)、[开发与测试](development-and-testing.md)。
3. 要改接口或排查前后端联调问题时读 [API 文档](api.md)。
4. 要改持久化、删除、会话归属或审计时读 [数据模型](data-model.md)。
5. 要部署或排查生产问题时读 [部署运维](deployment-and-operations.md) 和 [../CONTEXT.md](../CONTEXT.md)。
