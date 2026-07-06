"""OIDC 客户端 — Slice 14 OAuth/SSO 登录(OIDC 优先)。

封装与外部 IdP(Keycloak / Google / GitHub 等)的交互:
  - discovery:GET {issuer}/.well-known/openid-configuration 拿 endpoints + JWKS URI。
  - 授权 URL 构造:response_type=code,带 state(CSRF 防护)+ nonce(重放防护)。
  - code 换 token:POST token_endpoint,拿 id_token + access_token。
  - id_token 验证:用 JWKS 验签名,校验 iss/aud/exp/nonce(authlib IDToken claims_cls)。

设计(deep module + 可测试):
  - 路由层只调两个高层函数:get_authorization_url / exchange_code_for_claims。
  - 测试在路由层 mock 这两个函数(与 mock_precreate / mock_fetch_history 一致),
    不依赖真实 IdP;mock IdP 的 HTTP 细节封在本模块内部。
  - HTTP 用 httpx(trust_env=False,与 gateway 一致,不走系统代理)。
  - id_token 签名验证用 authlib.jose.jwt + authlib.oidc.core.IDToken(claims_cls)。

OIDC 流程(对应 ISSUES.md Issue 14):
  1. 前端点「SSO 登录」→ GET /sso/login → 生成 state+nonce 存 session → 重定向 IdP。
  2. IdP 认证后回调 GET /sso/callback?code=...&state=...。
  3. 后端校验 state(与 session 中一致),用 code 换 id_token,验证 id_token 拿 claims。
  4. 用 claims.sub 匹配本地用户(sso_provider + sso_external_id);不存在按配置创建/拒绝。
  5. 建立同源会话(与自建账号登录一致),写 login_success 审计(meta 含 provider),跳分享页列表。
"""

import json
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from authlib.jose import jwt
from authlib.oidc.core import IDToken
from fastapi import HTTPException

# OIDC provider 标识(写死 "oidc",meta_json 用此值)
SSO_PROVIDER = "oidc"

# discovery 缓存(进程级,避免每次 SSO 登录都打 IdP discovery)
_discovery_cache: dict[str, dict] = {}


@dataclass
class OIDCConfig:
    """OIDC 配置切片(从 Settings 提取,便于独立测试与传参)。"""

    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_settings(cls, settings) -> "OIDCConfig":
        """从 Settings 构造(sso_login / sso_callback 共用,消除 TD11 内联后回归的重复)。"""
        return cls(
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            redirect_uri=settings.oidc_redirect_uri,
        )


def _build_client(*, timeout: float = 15.0) -> httpx.AsyncClient:
    """构造 httpx.AsyncClient(trust_env=False,与 gateway 一致)。"""
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout=timeout, connect=10.0), trust_env=False)


async def _fetch_discovery(config: OIDCConfig) -> dict:
    """获取并缓存 OIDC discovery 文档。

    GET {issuer}/.well-known/openid-configuration,返回 authorization_endpoint /
    token_endpoint / jwks_uri / issuer 等元数据。按 issuer 缓存(进程级)。
    IdP 不可达 → 502(网关错误,提示「SSO IdP 不可达」)。
    """
    cached = _discovery_cache.get(config.issuer)
    if cached is not None:
        return cached
    discovery_url = f"{config.issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with _build_client() as client:
            resp = await client.get(discovery_url)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"SSO IdP 不可达: {e}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"SSO discovery 失败: HTTP {resp.status_code}")
    doc = resp.json()
    _discovery_cache[config.issuer] = doc
    return doc


async def get_authorization_url(config: OIDCConfig, state: str, nonce: str) -> str:
    """构造 IdP 授权 URL(response_type=code,带 state + nonce)。

    流程步骤 1:门户重定向用户到此 URL,用户在 IdP 完成认证后 IdP 回调 redirect_uri。
    state 防 CSRF(回调时校验与 session 中一致),nonce 防重放(写入 id_token 验证)。
    """
    disc = await _fetch_discovery(config)
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
    }
    auth_endpoint = disc.get("authorization_endpoint")
    if not auth_endpoint:
        raise HTTPException(status_code=502, detail="SSO discovery 缺少 authorization_endpoint")
    return f"{auth_endpoint}?{urlencode(params)}"


async def _fetch_jwks(jwks_uri: str) -> dict:
    """获取 IdP JWKS(用于 id_token 签名验证)。"""
    try:
        async with _build_client() as client:
            resp = await client.get(jwks_uri)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"SSO JWKS 不可达: {e}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"SSO JWKS 获取失败: HTTP {resp.status_code}")
    return resp.json()


async def _exchange_code(config: OIDCConfig, disc: dict, code: str) -> dict:
    """用授权码换 token(grant_type=authorization_code)。返回 IdP token 响应(含 id_token)。"""
    token_endpoint = disc.get("token_endpoint")
    if not token_endpoint:
        raise HTTPException(status_code=502, detail="SSO discovery 缺少 token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    try:
        async with _build_client() as client:
            resp = await client.post(token_endpoint, data=data)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"SSO token 端点不可达: {e}")
    if resp.status_code != 200:
        detail = _extract_idp_error(resp)
        raise HTTPException(status_code=502, detail=f"SSO 换 token 失败: {detail}")
    return resp.json()


def _extract_idp_error(resp: httpx.Response) -> str:
    """从 IdP 错误响应提取可读信息(error / error_description 字段,OAuth2 标准)。"""
    try:
        body = resp.json()
        err = body.get("error", "")
        desc = body.get("error_description", "")
        return f"{err} {desc}".strip() or f"HTTP {resp.status_code}"
    except (json.JSONDecodeError, ValueError):
        return f"HTTP {resp.status_code}"


async def _verify_id_token(config: OIDCConfig, disc: dict, id_token: str, nonce: str) -> dict:
    """验证 id_token 签名 + claims,返回 claims dict。

    验证项(authlib IDToken claims_cls 自动校验):
      - 签名:用 JWKS 公钥验签。
      - iss:与 config.issuer 一致。
      - aud:与 config.client_id 一致。
      - exp:未过期。
      - nonce:与传入 nonce 一致(防重放)。
    """
    jwks_uri = disc.get("jwks_uri")
    if not jwks_uri:
        raise HTTPException(status_code=502, detail="SSO discovery 缺少 jwks_uri")
    jwks = await _fetch_jwks(jwks_uri)
    try:
        claims = jwt.decode(
            id_token,
            key=jwks,
            claims_options={
                "iss": {"value": config.issuer},
                "aud": {"value": config.client_id},
            },
            claims_cls=IDToken,
        )
        claims.validate(nonce=nonce)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SSO id_token 验证失败: {e}")
    return dict(claims)


async def exchange_code_for_claims(config: OIDCConfig, code: str, nonce: str) -> dict:
    """高层函数:用授权码换 token → 验证 id_token → 返回 claims dict。

    路由层调此函数(测试在路由层 mock,与 mock_precreate / mock_fetch_history 一致)。
    返回的 claims 至少含 ``sub``(IdP 唯一标识,用于匹配本地用户),
    可能含 ``email`` / ``preferred_username``(用于自动创建用户)。
    """
    disc = await _fetch_discovery(config)
    token_resp = await _exchange_code(config, disc, code)
    id_token = token_resp.get("id_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="SSO token 响应缺少 id_token")
    return await _verify_id_token(config, disc, id_token, nonce)


def clear_discovery_cache() -> None:
    """清空 discovery 缓存(测试间隔离用)。"""
    _discovery_cache.clear()
