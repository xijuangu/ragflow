#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# PROTOTYPE — throwaway
#
# 模拟「门户网关 + RAGFlow 后端」的令牌注入链路,验证 H1:
#   网关签发的短期、可撤销、资源受限嵌入令牌,能否通过 URL 参数 auth
#   注入 RAGFlow 前端(embed-dialog/index.tsx),使前端用它替代 localStorage
#   中的租户 Token 调用后端 SSE 与会话接口。
#
# 本文件不连接任何真实 RAGFlow 服务器,所有数据在内存。
# 启动:python3 mock_gateway.py  然后浏览器打开 http://localhost:8080/portal
#
# 关键源码依据(RAGFlow v0.26.0):
#   - web/src/utils/authorization-util.ts:getAuthorization() 优先读 URL ?auth=xxx
#   - web/src/utils/next-request.ts:axios 拦截器用 getAuthorization() 注入 Authorization header
#   - web/src/hooks/logic-hooks.ts:useSendMessageWithSse 用 fetch + getAuthorization() 发 SSE
#   - web/src/components/embed-dialog/index.tsx:iframe URL 构造时 auth 参数 = 租户 Token

import http.server
import json
import secrets
import threading
import time
from urllib.parse import urlparse, parse_qs

PORT = 8090  # 避开 IDE 占用的 8080
TOKEN_TTL = 300  # 5 分钟,模拟"短期"

# 内存令牌存储:token -> {user, share_page_id, expires_at, revoked}
TOKENS = {}
LOCK = threading.Lock()

# 模拟的"真实租户 Token"——绝不暴露给浏览器,只用于网关内部转发时替换
REAL_TENANT_TOKEN = "ragflow_tenant_secret_TOKEN_do_not_expose_12345"


def issue_token(user, share_page_id):
    token = "embed_" + secrets.token_hex(16)
    expires_at = time.time() + TOKEN_TTL
    with LOCK:
        TOKENS[token] = {
            "user": user,
            "share_page_id": share_page_id,
            "expires_at": expires_at,
            "revoked": False,
        }
    return token, expires_at


def validate_token(token):
    if not token:
        return None, "missing token"
    with LOCK:
        rec = TOKENS.get(token)
        if not rec:
            return None, "unknown token"
        if rec["revoked"]:
            return None, "revoked"
        if time.time() > rec["expires_at"]:
            return None, "expired"
    return rec, None


def extract_token(auth_header):
    # 模拟 RAGFlow 后端解析 Authorization: Bearer xxx
    if not auth_header:
        return None
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return auth_header


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "mock_gateway/0.1 (PROTOTYPE throwaway)"

    def log_message(self, fmt, *args):
        # 简短日志,便于验证时观察请求
        print("[gateway] " + (fmt % args))

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- GET ----------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/portal")
            self.end_headers()
            return

        if path == "/portal":
            self._send_html(PORTAL_HTML)
            return

        if path == "/api/embed-url":
            user = qs.get("user", ["alice"])[0]
            share_page_id = qs.get("share_page_id", ["sp_default"])[0]
            token, exp = issue_token(user, share_page_id)
            # 构造 iframe URL:用 mock 令牌替换真实租户 Token
            # 真实场景下 RAGFlow 这里会放 REAL_TENANT_TOKEN,网关改造后放短期令牌
            iframe_url = (
                f"/embed_page.html?shared_id={share_page_id}"
                f"&auth={token}&from=chat"
            )
            self._send_json(200, {
                "iframe_url": iframe_url,
                "token": token,
                "expires_at": exp,
                "ttl_seconds": TOKEN_TTL,
                "real_tenant_token_exposed": False,
                "note": "iframe URL 中的 auth 参数 = 网关签发的短期令牌,非真实租户 Token",
            })
            return

        if path == "/embed_page.html":
            # 返回模拟 RAGFlow embed-dialog 前端的 HTML
            try:
                with open("embed_page.html", "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                body = b"<h1>embed_page.html not found</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/tokens":
            # 调试用:查看当前令牌状态(不暴露 REAL_TENANT_TOKEN)
            with LOCK:
                snapshot = {
                    k: {
                        "user": v["user"],
                        "share_page_id": v["share_page_id"],
                        "expires_at": v["expires_at"],
                        "remaining_seconds": max(0, int(v["expires_at"] - time.time())),
                        "revoked": v["revoked"],
                    }
                    for k, v in TOKENS.items()
                }
            self._send_json(200, snapshot)
            return

        self._send_json(404, {"error": "not found", "path": path})

    # ---------------- POST ----------------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}

        if path == "/api/login":
            user = payload.get("username", "alice")
            share_page_id = payload.get("share_page_id", "sp_default")
            token, exp = issue_token(user, share_page_id)
            self._send_json(200, {
                "token": token,
                "expires_at": exp,
                "ttl_seconds": TOKEN_TTL,
                "user": user,
                "share_page_id": share_page_id,
            })
            return

        if path == "/api/revoke":
            token = payload.get("token") or parse_qs(parsed.query).get("token", [None])[0]
            with LOCK:
                if token in TOKENS:
                    TOKENS[token]["revoked"] = True
                    ok = True
                else:
                    ok = False
            self._send_json(200, {"revoked": ok, "token": token})
            return

        # 模拟 RAGFlow SSE 端点 + 网关校验
        # 真实路径:/api/v1/chatbots/{conversationId}/completions
        if (
            path == "/sse"
            or path.startswith("/api/v1/chatbots/")
            or path.startswith("/api/v1/agentbots/")
        ):
            auth_header = self.headers.get("Authorization", "")
            token = extract_token(auth_header)
            rec, err = validate_token(token)
            if err:
                # 网关拒绝:令牌无效/过期/撤销
                self._send_json(401, {
                    "code": 401,
                    "message": f"token {err}",
                    "hint": "网关拒绝:令牌无效/过期/撤销",
                    "token_received": token,
                })
                return
            # 令牌有效 → 真实场景网关会用 REAL_TENANT_TOKEN 转发给 RAGFlow
            # 这里直接返回 mock SSE 流
            question = payload.get("question", "")
            self._stream_sse(rec, token, question)
            return

        self._send_json(404, {"error": "not found", "path": path})

    def _stream_sse(self, rec, token, question):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # 模拟 RAGFlow async_iframe_completion 的 SSE 消息序列
        session_id = "sess_" + secrets.token_hex(8)
        events = [
            {"session_id": session_id, "message_id": "msg_1"},
            {"content": "你好,这是来自 mock RAGFlow 的流式回答。"},
            {"content": f"你问的是:{question or '(空问题,初始化会话)'}。"},
            {"content": f"令牌持有者:{rec['user']},分享页:{rec['share_page_id']}。"},
            {"reference": [
                {"doc_name": "mock_doc.pdf", "chunk": "引用片段示例 1"},
                {"doc_name": "mock_doc2.pdf", "chunk": "引用片段示例 2"},
            ]},
            {"done": True},
        ]
        for ev in events:
            line = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.15)
            except Exception:
                break


PORTAL_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>权限门户(模拟)— PROTOTYPE throwaway</title>
<style>
body{font-family:-apple-system,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;color:#222}
.box{border:1px solid #ddd;padding:16px;border-radius:8px;margin:12px 0;background:#fafafa}
button{padding:8px 14px;font-size:14px;cursor:pointer;border:1px solid #888;background:#fff;border-radius:4px}
button:hover{background:#eee}
input{padding:6px;font-size:14px;border:1px solid #aaa;border-radius:4px}
pre{background:#f0f0f0;padding:8px;border-radius:4px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.tag{display:inline-block;padding:2px 8px;background:#2563eb;color:#fff;border-radius:3px;font-size:12px}
.warn{background:#fee;border-color:#c33}
</style></head>
<body>
<h1>权限门户(模拟) <span class="tag">PROTOTYPE throwaway</span></h1>
<p>本页面模拟门户登录后获取分享页 iframe URL 的流程。<br>
真实租户 Token 永远不离开网关,iframe URL 里只有网关签发的短期令牌。</p>

<div class="box">
  <h3>1. 登录并签发短期嵌入令牌(5 分钟过期)</h3>
  <label>用户名 <input id="user" value="alice"></label>
  <label>分享页ID <input id="sp" value="sp_default"></label>
  <button onclick="login()">登录并签发令牌</button>
  <pre id="loginResult">未登录</pre>
</div>

<div class="box">
  <h3>2. 获取 iframe URL(网关用 mock 令牌替换真实租户 Token)</h3>
  <button onclick="getEmbedUrl()">获取 embed URL</button>
  <pre id="urlResult">未获取</pre>
  <button onclick="openEmbed()">在新窗口打开 embed 页(URL 参数注入)</button>
  <button onclick="openEmbedPostMessage()">打开 embed 页并用 postMessage 注入</button>
</div>

<div class="box warn">
  <h3>3. 撤销令牌(测试撤销即失效)</h3>
  <input id="revokeToken" placeholder="粘贴要撤销的 token(留空用上次签发的)" style="width:300px">
  <button onclick="revoke()">撤销</button>
  <pre id="revokeResult">未撤销</pre>
</div>

<div class="box">
  <h3>4. 调试:查看当前所有令牌状态</h3>
  <button onclick="listTokens()">刷新令牌列表</button>
  <pre id="tokensResult">未查询</pre>
</div>

<script>
let lastToken = null;
let lastIframeUrl = null;

async function login(){
  const user = document.getElementById('user').value;
  const sp = document.getElementById('sp').value;
  const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:user, share_page_id:sp})});
  const j = await r.json();
  lastToken = j.token;
  document.getElementById('loginResult').textContent = JSON.stringify(j, null, 2);
}

async function getEmbedUrl(){
  const user = document.getElementById('user').value;
  const sp = document.getElementById('sp').value;
  const r = await fetch(`/api/embed-url?user=${encodeURIComponent(user)}&share_page_id=${encodeURIComponent(sp)}`);
  const j = await r.json();
  if (j.token) lastToken = j.token;
  lastIframeUrl = j.iframe_url;
  document.getElementById('urlResult').textContent = JSON.stringify(j, null, 2);
}

function openEmbed(){
  if (!lastIframeUrl){ alert('请先获取 embed URL'); return; }
  window.open(lastIframeUrl, '_blank');
}

function openEmbedPostMessage(){
  // 打开一个不带 auth 参数的 embed 页,然后用 postMessage 注入令牌
  // 验证 postMessage 路径是否同样有效
  if (!lastToken){ alert('请先登录获取令牌'); return; }
  const sp = document.getElementById('sp').value;
  const url = `/embed_page.html?shared_id=${sp}&from=chat&inject=postmessage`;
  const w = window.open(url, '_blank');
  const send = () => {
    try {
      w.postMessage({type:'INJECT_TOKEN', token: lastToken}, location.origin);
    } catch(e){ console.error(e); }
  };
  // 多发几次,确保子页 load 后能收到
  let n = 0;
  const iv = setInterval(()=>{ send(); if(++n>=10) clearInterval(iv); }, 200);
}

async function revoke(){
  const t = document.getElementById('revokeToken').value || lastToken;
  if (!t){ alert('无 token'); return; }
  const r = await fetch('/api/revoke', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({token:t})});
  const j = await r.json();
  document.getElementById('revokeResult').textContent = JSON.stringify(j, null, 2);
}

async function listTokens(){
  const r = await fetch('/api/tokens');
  const j = await r.json();
  document.getElementById('tokensResult').textContent = JSON.stringify(j, null, 2);
}
</script>
</body></html>
"""


def main():
    print("=" * 64)
    print("PROTOTYPE — throwaway")
    print("mock_gateway:模拟门户网关 + RAGFlow SSE 后端")
    print("=" * 64)
    print(f"打开 http://localhost:{PORT}/portal 开始验证 H1")
    print(f"真实租户 Token(仅网关内部,绝不暴露给浏览器):{REAL_TENANT_TOKEN}")
    print("=" * 64)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭")


if __name__ == "__main__":
    main()
