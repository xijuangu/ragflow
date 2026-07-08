"""Slice 44 测试 — 后端中间件给 text/html 响应加 no-store + Vary: Accept。

方案 B(根治 Slice 43 诊断的浏览器缓存污染):
  - text/html 响应(StaticFiles 导航 fallback)带 Cache-Control: no-store + Vary: Accept,
    防止浏览器缓存导航 HTML 后污染同 URL 的 API fetch。
  - JSON API 响应不受影响(无 no-store),中间件只针对 text/html。
"""
from pathlib import Path

import pytest

# 前端 dist 路径(与 portal/main.py 中 _frontend_dist 一致)
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@pytest.fixture
def dist_index_html():
    """创建最小化 frontend/dist/index.html,让 StaticFiles(html=True) 挂载并返回 text/html。

    根因(Slice 43)是 StaticFiles 的 HTML 响应无缓存头;本 fixture 造最小 dist
    让该路径可被测试覆盖(生产由前端构建产出 dist,测试不依赖完整构建)。
    只清理本 fixture 创建的产物,不碰已有的 dist 构建产物。
    """
    created_dir = False
    created_file = False
    if not _FRONTEND_DIST.is_dir():
        _FRONTEND_DIST.mkdir(parents=True)
        created_dir = True
    index = _FRONTEND_DIST / "index.html"
    if not index.exists():
        index.write_text(
            "<!DOCTYPE html><html><head><title>SPA</title></head><body>root</body></html>",
            encoding="utf-8",
        )
        created_file = True
    yield index
    # 清理(只删本 fixture 创建的,不碰已有构建产物)
    if created_file:
        index.unlink(missing_ok=True)
    if created_dir:
        try:
            _FRONTEND_DIST.rmdir()
        except OSError:
            pass  # 目录非空(有其他文件),保留


@pytest.fixture
def app(dist_index_html):
    """覆盖 conftest.app:先确保 dist 存在再创建 app,让 StaticFiles(html=True) 挂载。"""
    from portal.main import create_app

    return create_app()


async def _login(client, username="admin", password="testpass123"):
    """辅助:登录并断言成功。"""
    resp = await client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"


# ---------------------------------------------------------------------------
# 用例 1:text/html 响应(StaticFiles 导航)带 no-store + Vary: Accept
# ---------------------------------------------------------------------------


async def test_staticfiles_html_response_has_no_store_cache_control(client):
    """StaticFiles 对导航请求(GET / + Accept: text/html)返回 index.html(200 text/html),
    NoCacheHtmlMiddleware 应给该响应加 Cache-Control: no-store + Vary: Accept。"""
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200, f"GET / 应返回 index.html,实际: {resp.status_code} {resp.text[:100]}"
    ct = resp.headers.get("content-type", "")
    assert "text/html" in ct, f"应返回 text/html,实际 content-type: {ct!r}"
    cc = resp.headers.get("cache-control", "")
    assert "no-store" in cc, f"text/html 响应应含 Cache-Control: no-store,实际: {cc!r}"
    assert "no-cache" in cc and "must-revalidate" in cc, (
        f"Cache-Control 应含 no-cache + must-revalidate,实际: {cc!r}"
    )
    vary = resp.headers.get("vary", "")
    assert "accept" in vary.lower(), f"text/html 响应应含 Vary: Accept,实际 Vary: {vary!r}"


# ---------------------------------------------------------------------------
# 用例 2:JSON API 响应不受影响(无 no-store)
# ---------------------------------------------------------------------------


async def test_json_api_response_not_affected_by_middleware(client):
    """登录后 GET /admin/users 返回 application/json,中间件不应加 no-store(API 响应可正常缓存策略)。"""
    await _login(client)
    resp = await client.get("/admin/users")
    assert resp.status_code == 200, f"GET /admin/users 失败: {resp.text}"
    ct = resp.headers.get("content-type", "")
    assert "application/json" in ct, f"API 响应应为 application/json,实际 content-type: {ct!r}"
    cc = resp.headers.get("cache-control", "")
    assert "no-store" not in cc, (
        f"JSON API 响应不应含 no-store(中间件只针对 text/html),实际 Cache-Control: {cc!r}"
    )


# ---------------------------------------------------------------------------
# 用例 3:POST /login 返回 JSON,无 no-store
# ---------------------------------------------------------------------------


async def test_login_response_json_without_no_store(client):
    """POST /login 返回 JSON(登录成功),中间件不影响该响应(无 no-store)。"""
    resp = await client.post("/login", json={"username": "admin", "password": "testpass123"})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    ct = resp.headers.get("content-type", "")
    assert "application/json" in ct, f"/login 响应应为 application/json,实际 content-type: {ct!r}"
    cc = resp.headers.get("cache-control", "")
    assert "no-store" not in cc, f"/login JSON 响应不应含 no-store,实际 Cache-Control: {cc!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
