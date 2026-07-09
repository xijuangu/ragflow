# Slice 41 输入框抖动诊断脚本

这些脚本用于复现和分析 RAGFlow iframe 内流式输出期间输入框抖动问题。它们是阶段性诊断材料，不是生产代码或常规测试套件。

## 运行

```bash
cd /Users/xijuangu/Developer/Work/thqh_projects/rag/ragflow/portal-extension/project-materials/diagnostics/slice41-input-jitter
npm install
PORTAL_DIAG_PASSWORD='<admin-password>' node diag_slice41_repro.mjs
```

可选环境变量：

- `PORTAL_DIAG_BASE`：Portal 所在 origin，默认 `http://172.16.10.180`。
- `PORTAL_DIAG_USERNAME`：登录用户名，默认 `admin`。
- `PORTAL_DIAG_PASSWORD`：登录密码，必填。
- `CHROME_BIN`：Playwright Chromium 可执行文件路径。
- `HEADED=1`：部分脚本支持有头模式。
- `PROBE_MODE`：probe 脚本的诊断模式，见各脚本头部注释。

