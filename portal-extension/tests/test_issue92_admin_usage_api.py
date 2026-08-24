"""Issue 92 后端 — GET /admin/usage-records 管理员用量查询接口测试。

覆盖 ISSUES.md Issue 92 验收点(接口侧):
  - 管理员可列出 usage_record(字段完整,与时间/用户/分享页/incomplete/org 过滤);
  - 非 admin 用户访问 → 403;
  - 匿名(公开分享页)用量记录对管理员可见(subject = u_anonymous)。
"""
from portal.models import ANONYMOUS_USER_ID


def _seed_records(app):
    """落三条用量记录:admin 两条(其一 incomplete)、匿名一条。"""
    store = app.state.usage_store
    store.record(
        portal_user_id="u_admin", share_page_id="sp_default", session_id="s1",
        model_name="", input_tokens=10, output_tokens=20, incomplete=False,
        created_at=1000.0,
    )
    store.record(
        portal_user_id="u_admin", share_page_id="sp_default", session_id="s2",
        model_name="", input_tokens=3, output_tokens=4, incomplete=True,
        created_at=2000.0,
    )
    store.record(
        portal_user_id=ANONYMOUS_USER_ID, share_page_id="sp_default", session_id="s3",
        model_name="", input_tokens=5, output_tokens=6, incomplete=False,
        created_at=3000.0,
    )


async def _login(client, username, password="testpass123"):
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


async def test_admin_lists_usage_records_with_full_fields(client, app):
    _seed_records(app)
    await _login(client, "admin")
    resp = await client.get("/admin/usage-records")
    assert resp.status_code == 200, resp.text
    records = resp.json()["usage_records"]
    assert len(records) == 3
    # created_at 倒序(最新在前)
    assert [r["session_id"] for r in records] == ["s3", "s2", "s1"]
    rec = records[0]
    for field in (
        "id", "created_at", "portal_user_id", "share_page_id", "session_id",
        "model_name", "input_tokens", "output_tokens", "incomplete", "org_id",
    ):
        assert field in rec, f"响应缺字段 {field}"
    # 匿名(公开分享页)用量对管理员可见,subject 为匿名标识
    assert records[0]["portal_user_id"] == ANONYMOUS_USER_ID
    assert records[1]["incomplete"] is True
    assert records[2]["incomplete"] is False


async def test_admin_usage_records_filters(client, app):
    _seed_records(app)
    await _login(client, "admin")

    resp = await client.get(f"/admin/usage-records?user_id={ANONYMOUS_USER_ID}")
    assert [r["session_id"] for r in resp.json()["usage_records"]] == ["s3"]

    resp = await client.get("/admin/usage-records?incomplete=true")
    assert [r["session_id"] for r in resp.json()["usage_records"]] == ["s2"]

    resp = await client.get("/admin/usage-records?since=1500.0&until=2500.0")
    assert [r["session_id"] for r in resp.json()["usage_records"]] == ["s2"]

    resp = await client.get("/admin/usage-records?share_page_id=sp_nonexistent")
    assert resp.json()["usage_records"] == []


async def test_non_admin_gets_403_on_usage_records(client, app):
    _seed_records(app)
    await _login(client, "user2")
    resp = await client.get("/admin/usage-records")
    assert resp.status_code == 403


async def test_anonymous_request_gets_403_on_usage_records(client, app):
    resp = await client.get("/admin/usage-records")
    assert resp.status_code == 403
