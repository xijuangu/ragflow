"""Slice 12 端到端测试 — 双删重试定时任务。

覆盖验收点(ISSUES.md Issue 12 第 1-3、7 点):
  1. 双删失败的会话(`deleted_at` 非空)被定时任务重试,RAGFlow 成功后门户侧硬删除(无残留)。
  2. 管理员手动触发 retry-delete 端点仍可用(Slice 6 已实现,本 slice 不破坏 — 回归)。
  3. 定时任务可配置间隔,默认 5 分钟,文档说明配置方式。
  4. 现有测试全部通过,新增重试任务测试用例。

设计:
  - 直接调 ``retry_delete_pending_sessions`` 函数(不等待定时器触发),验证单次执行行为。
  - mock RAGFlow DELETE 端点(FakeRagflow transport 层,Issue 97;原先按名 patch
    portal.tasks/portal.routes 的 delete_session_via_ragflow)。
  - lifecycle 测试用 ``asyncio.sleep`` 推进定时器,验证 startup/shutdown 行为。
"""

import pytest

from portal.config import load_settings
from portal.tasks import retry_delete_pending_sessions
from tests.conftest import get_fake_ragflow

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def _precreate_session(client, app, session_id: str):
    """辅助:登录 + 预创建 session,返回响应体。"""
    await _login(client)
    get_fake_ragflow(app).precreate_session_id = session_id
    resp = await client.post("/share-pages/sp_default/sessions")
    assert resp.status_code == 200, f"预创建失败: {resp.text}"
    return resp.json()


def _delete_request_count(fake, session_id: str) -> int:
    """统计 fake 收到的对该 session 的 DELETE 请求数(替代原 AsyncMock 断言)。"""
    return sum(
        1
        for r in fake.requests
        if r.method == "DELETE" and r.url.path.endswith(f"/sessions/{session_id}")
    )


# ---------------------------------------------------------------------------
# 验收点 1:定时任务重试 — RAGFlow 成功后门户侧硬删除(无残留)。
# ---------------------------------------------------------------------------


async def test_retry_task_deletes_session_when_ragflow_succeeds(client, app):
    """RAGFlow DELETE 成功 → 门户侧硬删除,session 不在 list_pending_deletion 中。"""
    fake_session_id = "slice12-retry-ok"
    await _precreate_session(client, app, fake_session_id)
    app.state.session_store.mark_deleted(fake_session_id)
    # FakeRagflow DELETE 路由默认 200(成功)
    fake = get_fake_ragflow(app)

    settings = app.state.settings
    await retry_delete_pending_sessions(settings, app.state.session_store)

    # 门户记录已硬删除
    assert app.state.session_store.get(fake_session_id) is None, "RAGFlow 成功后门户侧应硬删除"
    # RAGFlow DELETE 被调用一次
    assert _delete_request_count(fake, fake_session_id) == 1
    # 不在 pending_deletion 列表中(已硬删除)
    pending = app.state.session_store.list_pending_deletion()
    assert all(s.session_id != fake_session_id for s in pending)


async def test_retry_task_preserves_session_when_ragflow_fails(client, app):
    """RAGFlow DELETE 失败 → 门户侧记录保留(仍待重试),deleted_at 仍非空。"""
    fake_session_id = "slice12-retry-fail"
    await _precreate_session(client, app, fake_session_id)
    app.state.session_store.mark_deleted(fake_session_id)
    # 配置 DELETE 全部失败(500 → 网关统一包装为 HTTPException 502)
    get_fake_ragflow(app).delete_status = 500

    settings = app.state.settings
    # 不应抛异常(单个 session 失败不阻塞任务)
    await retry_delete_pending_sessions(settings, app.state.session_store)

    # 门户记录保留(仍待重试)
    owner = app.state.session_store.get(fake_session_id)
    assert owner is not None, "RAGFlow 失败时门户侧应保留(仍待重试)"
    assert owner.deleted_at is not None, "deleted_at 应仍非空(待重试)"


async def test_retry_task_no_pending_does_nothing(client, app):
    """无待重试会话时,任务不调 RAGFlow(空跑)。"""
    fake = get_fake_ragflow(app)

    settings = app.state.settings
    await retry_delete_pending_sessions(settings, app.state.session_store)

    assert not [r for r in fake.requests if r.method == "DELETE"], "无待重试时不应调 RAGFlow DELETE"


async def test_retry_task_partial_failure(client, app):
    """多个待重试:部分成功部分失败 — 成功的硬删除,失败的保留。"""
    sid_ok = "slice12-retry-partial-ok"
    sid_fail = "slice12-retry-partial-fail"
    await _precreate_session(client, app, sid_ok)
    await _precreate_session(client, app, sid_fail)
    app.state.session_store.mark_deleted(sid_ok)
    app.state.session_store.mark_deleted(sid_fail)

    # 配置 DELETE:sid_ok 成功(默认 200),sid_fail 失败(500)
    get_fake_ragflow(app).delete_status_by_session = {sid_fail: 500}

    settings = app.state.settings
    await retry_delete_pending_sessions(settings, app.state.session_store)

    # sid_ok 已硬删除
    assert app.state.session_store.get(sid_ok) is None, "成功的应硬删除"
    # sid_fail 保留(仍待重试)
    owner_fail = app.state.session_store.get(sid_fail)
    assert owner_fail is not None, "失败的不应删除"
    assert owner_fail.deleted_at is not None


# ---------------------------------------------------------------------------
# 验收点 2:管理员手动触发 retry-delete 端点仍可用(Slice 6 已实现 — 回归)。
# ---------------------------------------------------------------------------


async def test_manual_retry_delete_endpoint_still_works(client, app):
    """Slice 6 的 POST /admin/sessions/{id}/retry-delete 端点回归测试。"""
    await _login(client)
    fake_session_id = "slice12-manual-retry"
    await _precreate_session(client, app, fake_session_id)
    app.state.session_store.mark_deleted(fake_session_id)
    # FakeRagflow DELETE 路由默认 200(成功)

    resp = await client.post(f"/admin/sessions/{fake_session_id}/retry-delete")
    assert resp.status_code == 200, f"手动重试应成功: {resp.text}"
    assert app.state.session_store.get(fake_session_id) is None


# ---------------------------------------------------------------------------
# 验收点 3:定时任务可配置间隔,默认 5 分钟。
# ---------------------------------------------------------------------------


def test_config_retry_delete_interval_default(monkeypatch):
    """retry_delete_interval_seconds 默认 300(5 分钟)。"""
    monkeypatch.delenv("RETRY_DELETE_INTERVAL_SECONDS", raising=False)
    settings = load_settings()
    assert settings.retry_delete_interval_seconds == 300


def test_config_retry_delete_interval_from_env(monkeypatch):
    """retry_delete_interval_seconds 从环境变量读取。"""
    monkeypatch.setenv("RETRY_DELETE_INTERVAL_SECONDS", "60")
    settings = load_settings()
    assert settings.retry_delete_interval_seconds == 60


def test_config_retry_delete_interval_disable(monkeypatch):
    """retry_delete_interval_seconds <=0 表示禁用定时任务(管理员仍可手动触发)。"""
    monkeypatch.setenv("RETRY_DELETE_INTERVAL_SECONDS", "0")
    settings = load_settings()
    assert settings.retry_delete_interval_seconds == 0


# ---------------------------------------------------------------------------
# 验收点:定时任务生命周期(startup 启动 task,shutdown 取消 task)。
# ---------------------------------------------------------------------------


async def test_retry_task_lifecycle_starts_and_stops(app):
    """FastAPI startup 启动 retry task,shutdown 取消(无残留 task)。"""
    # 用极短间隔让任务循环可观察(0.01s)
    app.state.settings.retry_delete_interval_seconds = 1
    # 装 FakeRagflow(避免真实调用;无 pending 时任务空跑)
    get_fake_ragflow(app)

    # 模拟 startup:启动任务
    from portal.main import start_retry_delete_task

    start_retry_delete_task(app)
    task = getattr(app.state, "retry_delete_task", None)
    assert task is not None, "startup 后 app.state.retry_delete_task 应存在"
    assert not task.done(), "task 应在运行"

    # 模拟 shutdown:取消任务
    from portal.main import stop_retry_delete_task

    await stop_retry_delete_task(app)
    assert task.cancelled() or task.done(), "shutdown 后 task 应已取消或完成"
    # app.state.retry_delete_task 已清空
    assert getattr(app.state, "retry_delete_task", None) is None


async def test_retry_task_disabled_when_interval_zero(app):
    """retry_delete_interval_seconds <=0 时不启动定时任务(禁用)。"""
    app.state.settings.retry_delete_interval_seconds = 0
    from portal.main import start_retry_delete_task

    start_retry_delete_task(app)
    # 禁用时不创建 task
    assert getattr(app.state, "retry_delete_task", None) is None, "interval<=0 时不应启动 task"


# ---------------------------------------------------------------------------
# 边界:retry_delete_pending_sessions 异常隔离(单个失败不影响其他)。
# ---------------------------------------------------------------------------


async def test_retry_task_exception_does_not_crash_loop(client, app):
    """单个 session 处理抛非 HTTPException 异常时,任务不崩溃(继续处理其他)。

    transport handler 对 sid_a 的 DELETE 抛 httpx.ConnectError(非 HTTPException,
    gateway 不捕获直接传播),与原先 patch 抛 RuntimeError 的异常路径语义一致。
    """
    sid_a = "slice12-retry-exception-a"
    sid_b = "slice12-retry-exception-b"
    await _precreate_session(client, app, sid_a)
    await _precreate_session(client, app, sid_b)
    app.state.session_store.mark_deleted(sid_a)
    app.state.session_store.mark_deleted(sid_b)

    import httpx

    fake = get_fake_ragflow(app)
    inner_handler = fake.handler

    def handler(req: httpx.Request) -> httpx.Response:
        fake.requests.append(req)
        if req.method == "DELETE":
            if req.url.path.endswith(f"/sessions/{sid_a}"):
                raise httpx.ConnectError("mock: 上游连接异常", request=req)
            return httpx.Response(200, json={"code": 0})
        return inner_handler(req)

    app.state.settings.upstream_transport = httpx.MockTransport(handler)

    settings = app.state.settings
    # 不应抛异常
    await retry_delete_pending_sessions(settings, app.state.session_store)

    # 两个 session 都被尝试(DELETE 请求各发一次)
    assert _delete_request_count(fake, sid_a) == 1
    assert _delete_request_count(fake, sid_b) == 1
    # sid_a 处理失败仍保留,sid_b 处理成功已删除
    assert app.state.session_store.get(sid_a) is not None, "异常时 sid_a 保留"
    assert app.state.session_store.get(sid_b) is None, "sid_b 应正常删除"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
