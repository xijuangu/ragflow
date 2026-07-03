"""pytest 配置与 fixtures。

所有敏感值(真实 beta Token、密码、IP)只通过环境变量传入,不写入任何文件。
单元测试用占位值;集成测试由外部导出真实环境变量(setdefault 不覆盖真实值)。

Slice 7 新增:统一 mock helper fixture(覆盖 SSE/GET/PATCH/DELETE/预创建),
供 test_e2e_regression.py 与未来 slice 复用,消除各 slice 测试文件中重复的
monkeypatch.setattr 模板。Slice 1-6 测试文件保持原 mock 模式不变(无回归)。
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

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


# ===========================================================================
# Slice 7:统一 mock helper fixture(覆盖 SSE/GET/PATCH/DELETE/预创建)
# ===========================================================================
#
# 设计:每个 fixture 是「工厂」— 测试调用 setup 函数注入 mock,而非直接生效。
# 这样测试可显式控制 mock 起始时机与参数,避免 fixture 副作用跨测试污染。
# Slice 1-6 已有的 _mock_precreate / _mock_ragflow_sse_* helper 仍保留在各 slice
# 测试文件中(不动既有代码);Slice 7 与后续 slice 用此处统一 fixture。


@pytest.fixture
def mock_precreate(monkeypatch):
    """工厂:mock portal.routes.precreate_session_via_ragflow 返回指定 session_id。

    用法: ``mock_precreate("fake-session-id")``
    """

    def _setup(session_id: str):
        monkeypatch.setattr(
            "portal.routes.precreate_session_via_ragflow",
            AsyncMock(return_value=session_id),
        )

    return _setup


@pytest.fixture
def mock_ragflow_sse(monkeypatch):
    """工厂:mock portal.gateway.httpx.AsyncClient,让上游 SSE 返回成功流。

    可指定 session_id 与 message_id(用于「继续流式」验收点验证新 message_id 生成)。
    默认返回包含 ``answer``、``session_id``、``id``、``final:true`` 的单帧 SSE。

    用法: ``mock_ragflow_sse(session_id="sid", message_id="mid")``
    """

    def _setup(*, session_id: str = "mock-session-id", message_id: str = "mock-message-id", answer: str = "流式回答"):
        sse_body = (
            f'data: {{"answer":"{answer}","session_id":"{session_id}","id":"{message_id}","final":true}}\n\n'
        ).encode("utf-8")

        class _MockAsyncClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(
                    lambda req: httpx.Response(
                        200,
                        content=sse_body,
                        headers={"content-type": "text/event-stream"},
                    )
                )
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("portal.gateway.httpx.AsyncClient", _MockAsyncClient)

    return _setup


@pytest.fixture
def mock_fetch_history(monkeypatch):
    """工厂:mock portal.routes.fetch_session_history_via_ragflow 返回指定 history dict。

    用法: ``mock_fetch_history({"session_id": "sid", "messages": [...], "reference": [...]})``
    """

    def _setup(history: dict):
        monkeypatch.setattr(
            "portal.routes.fetch_session_history_via_ragflow",
            AsyncMock(return_value=history),
        )

    return _setup


@pytest.fixture
def mock_delete_session(monkeypatch):
    """工厂:mock RAGFlow DELETE 端点(portal.routes 与 portal.gateway 两处都 mock)。

    传 side_effect(异常或 HTTPException)模拟失败;不传则模拟成功。
    同时 mock routes 与 gateway 两处,因为 SessionStore.cascade_delete_for_user
    直接调 portal.gateway.delete_session_via_ragflow(不经 routes 间接调用)。

    用法:
      ``mock_delete_session()`` — 成功
      ``mock_delete_session(side_effect=HTTPException(502, ...))`` — 失败
    """

    def _setup(side_effect=None):
        if side_effect is not None:
            mock = AsyncMock(side_effect=side_effect)
        else:
            mock = AsyncMock(return_value=None)
        monkeypatch.setattr("portal.routes.delete_session_via_ragflow", mock)
        monkeypatch.setattr("portal.gateway.delete_session_via_ragflow", mock)

    return _setup


@pytest.fixture
def mock_rename_session(monkeypatch):
    """工厂:mock portal.routes.rename_session_via_ragflow。

    传 side_effect 模拟失败;不传则模拟成功。
    """

    def _setup(side_effect=None):
        if side_effect is not None:
            mock = AsyncMock(side_effect=side_effect)
        else:
            mock = AsyncMock(return_value=None)
        monkeypatch.setattr("portal.routes.rename_session_via_ragflow", mock)

    return _setup
