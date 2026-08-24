"""pytest 配置与 fixtures。

所有敏感值(真实 beta Token、密码、IP)只通过环境变量传入,不写入任何文件。
单元测试用占位值;集成测试由外部导出真实环境变量(setdefault 不覆盖真实值)。

Slice 7 新增:统一 mock helper fixture(覆盖 SSE/GET/PATCH/DELETE/预创建),
供 test_e2e_regression.py 与未来 slice 复用,消除各 slice 测试文件中重复的
monkeypatch.setattr 模板。

Issue 97:mock 接缝从「按名 patch 模块命名空间」迁移到 httpx transport 层 —
``FakeRagflow`` 经 ``settings.upstream_transport`` 注入 ``httpx.MockTransport``,
按 method+path 分发模拟上游响应;未配置的路由 raise httpx.ConnectError,
保持「未 mock 即 ragflow-mock.invalid 不可达」的失败语义。
"""

import json
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
# Slice 8:单元测试用 SQLite in-memory(每测试独立 engine,见 app fixture),
# 不依赖外部 MySQL,不污染生产数据。
os.environ.setdefault("PORTAL_DB_URL", "sqlite://")

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
# Issue 97:FakeRagflow — 在 httpx transport 层模拟 RAGFlow 上游
# ===========================================================================
#
# 设计:FakeRagflow.install(app) 把 ``app.state.settings.upstream_transport``
# 设为 ``httpx.MockTransport(fake.handler)``;handler 按 method+path 分发:
#   POST   */completions   question=="" → 预创建响应;否则 → sse_body 流
#   GET    */sessions/<id> → history(或 history_queue 连续返回)
#   PATCH  */sessions/<id> → rename_status
#   DELETE */sessions/<id> → delete_status / delete_status_by_session
# 未配置的路由 raise httpx.ConnectError — 保持迁移前「未 mock 即不可达」语义。
# ``requests`` 全量记录(供断言 URL / Authorization header / PATCH body);
# ``history_calls`` 单独记录 GET history 次数(替代 assert_awaited_once)。


class FakeRagflow:
    """RAGFlow 上游的 transport 层假实现(默认全部未配置 → ConnectError)。"""

    def __init__(self):
        # None = 路由未配置(请求 → httpx.ConnectError,等同迁移前未 mock)
        self.precreate_session_id: str | None = None
        self.sse_body: bytes | None = None
        self.history: dict | None = None
        # 连续多次 GET history 返回不同值(queue 用尽后重复最后一项)
        self.history_queue: list[dict] | None = None
        self.rename_status = 200
        self.delete_status = 200
        self.delete_status_by_session: dict[str, int] = {}
        # 请求记录
        self.requests: list[httpx.Request] = []
        self.history_calls: list[httpx.Request] = []

    def install(self, app) -> "FakeRagflow":
        """把本 fake 装到 app 的上游 transport 注入点(并记录到 app.state 供复用)。"""
        app.state.settings.upstream_transport = httpx.MockTransport(self.handler)
        app.state.fake_ragflow = self
        return self

    # -- 内部分发 -----------------------------------------------------------

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if req.method == "POST" and path.endswith("/completions"):
            return self._handle_completions(req)
        if "/sessions/" in path:
            if req.method == "GET":
                return self._handle_history(req)
            if req.method == "PATCH":
                return httpx.Response(self.rename_status, json={"code": 0})
            if req.method == "DELETE":
                session_id = path.rsplit("/", 1)[-1]
                status = self.delete_status_by_session.get(session_id, self.delete_status)
                return httpx.Response(status, json={"code": 0} if status == 200 else {"error": "mock"})
        raise httpx.ConnectError(f"FakeRagflow: 未配置的路由 {req.method} {path}", request=req)

    def _handle_completions(self, req: httpx.Request) -> httpx.Response:
        try:
            question = json.loads(req.content).get("question", "")
        except (json.JSONDecodeError, TypeError):
            question = ""
        if question == "":
            # 空 question = 预创建(网关双步行为,NOTES.md H4)
            if self.precreate_session_id is None:
                raise httpx.ConnectError("FakeRagflow: 预创建路由未配置", request=req)
            body = f'data: {{"code":0,"data":{{"session_id":"{self.precreate_session_id}"}}}}\n\n'.encode()
        else:
            if self.sse_body is None:
                raise httpx.ConnectError("FakeRagflow: SSE 路由未配置", request=req)
            body = self.sse_body
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    def _handle_history(self, req: httpx.Request) -> httpx.Response:
        index = len(self.history_calls)
        self.history_calls.append(req)
        if self.history_queue is not None:
            return httpx.Response(200, json=self.history_queue[min(index, len(self.history_queue) - 1)])
        if self.history is None:
            raise httpx.ConnectError("FakeRagflow: history 路由未配置", request=req)
        return httpx.Response(200, json=self.history)


def get_fake_ragflow(app) -> FakeRagflow:
    """取 app 上已装的 FakeRagflow;未装则新建并装上(供测试文件本地 helper 复用)。

    保证同一 app 只有一个 fake 实例 — 多处 helper/fixture 配置互不覆盖。
    """
    fake = getattr(app.state, "fake_ragflow", None)
    if fake is None:
        fake = FakeRagflow().install(app)
    return fake


@pytest.fixture
def fake_ragflow(app):
    """已装到 conftest app 的 FakeRagflow 实例(可直接配置属性做细粒度断言)。"""
    return get_fake_ragflow(app)


# ===========================================================================
# Slice 7:统一 mock helper fixture(覆盖 SSE/GET/PATCH/DELETE/预创建)
# ===========================================================================
#
# 设计:每个 fixture 是「工厂」— 测试调用 setup 函数配置 FakeRagflow,
# 而非直接生效。这样测试可显式控制 mock 起始时机与参数,避免 fixture 副作用
# 跨测试污染。Issue 97 起底层改为 transport 层(见 FakeRagflow),fixture 名与
# 调用方式保持不变。


@pytest.fixture
def mock_precreate(fake_ragflow):
    """工厂:配置 RAGFlow 预创建路由返回指定 session_id。

    用法: ``mock_precreate("fake-session-id")``
    """

    def _setup(session_id: str):
        fake_ragflow.precreate_session_id = session_id

    return _setup


@pytest.fixture
def mock_ragflow_sse(fake_ragflow):
    """工厂:配置上游 SSE 返回成功流。

    可指定 session_id 与 message_id(用于「继续流式」验收点验证新 message_id 生成)。
    默认返回包含 ``answer``、``session_id``、``id``、``final:true`` 的单帧 SSE。

    用法: ``mock_ragflow_sse(session_id="sid", message_id="mid")``
    """

    def _setup(*, session_id: str = "mock-session-id", message_id: str = "mock-message-id", answer: str = "流式回答"):
        fake_ragflow.sse_body = (
            f'data: {{"answer":"{answer}","session_id":"{session_id}","id":"{message_id}","final":true}}\n\n'
        ).encode("utf-8")

    return _setup


@pytest.fixture
def mock_fetch_history(fake_ragflow):
    """工厂:配置 RAGFlow GET history 路由返回指定 history dict。

    用法: ``mock_fetch_history({"session_id": "sid", "messages": [...], "reference": [...]})``
    """

    def _setup(history: dict):
        fake_ragflow.history = history

    return _setup


@pytest.fixture
def mock_delete_session(fake_ragflow):
    """工厂:配置 RAGFlow DELETE 端点响应。

    传 side_effect(异常或 HTTPException)模拟失败(映射为上游 500,网关统一
    包装为 HTTPException 502,与迁移前 patch 抛 502 的语义一致);不传则模拟成功。

    用法:
      ``mock_delete_session()`` — 成功
      ``mock_delete_session(side_effect=HTTPException(502, ...))`` — 失败
    """

    def _setup(side_effect=None):
        if side_effect is not None:
            fake_ragflow.delete_status = 500

    return _setup
