"""pytest 配置与 fixtures。

所有敏感值(真实 beta Token、密码、IP)只通过环境变量传入,不写入任何文件。
单元测试用占位值;集成测试由外部导出真实环境变量(setdefault 不覆盖真实值)。
"""

import os
import sys
from pathlib import Path

# 把项目根目录加入 sys.path,确保 portal 包可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 单元测试用占位值(非真实凭据)。setdefault 保证外部真实值优先。
FAKE_BETA_TOKEN = "fake-beta-token-for-unit-tests"
FAKE_RAGFLOW_HOST = "http://ragflow-mock.invalid"

os.environ.setdefault("PORTAL_ADMIN_USERNAME", "admin")
os.environ.setdefault("PORTAL_ADMIN_PASSWORD", "testpass123")
os.environ.setdefault("PORTAL_USER2_USERNAME", "user2")
os.environ.setdefault("PORTAL_USER2_PASSWORD", "testpass123")
os.environ.setdefault("PORTAL_SESSION_SECRET", "test-session-secret-key-for-testing-only")
os.environ.setdefault("RAGFLOW_HOST", FAKE_RAGFLOW_HOST)
os.environ.setdefault("RAGFLOW_DIALOG_ID", "test-dialog-id-12345")
os.environ.setdefault("RAGFLOW_BETA_TOKEN", FAKE_BETA_TOKEN)
os.environ.setdefault("T_SHORT_TTL_SECONDS", "300")

import httpx  # noqa: E402 — 环境变量须先于 portal.main 导入设置
import pytest  # noqa: E402

from portal.main import create_app  # noqa: E402


@pytest.fixture
def app():
    """每个测试用全新的 app 实例(配置从当前环境变量读取)。"""
    return create_app()


@pytest.fixture
async def client(app):
    """带 cookie 持久化的异步测试客户端(同源模拟)。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def real_ragflow():
    """集成测试专用:校验真实 RAGFlow 环境变量是否就绪,否则跳过。

    集成测试需要外部导出真实的 RAGFLOW_HOST 与 RAGFLOW_BETA_TOKEN。
    """
    token = os.environ.get("RAGFLOW_BETA_TOKEN", "")
    host = os.environ.get("RAGFLOW_HOST", "")
    if not token or token == FAKE_BETA_TOKEN or host == FAKE_RAGFLOW_HOST:
        pytest.skip("需要真实 RAGFlow 实例(导出 RAGFLOW_BETA_TOKEN 与 RAGFLOW_HOST 环境变量后运行)")
    return {"token": token, "host": host, "dialog_id": os.environ.get("RAGFLOW_DIALOG_ID", "")}
