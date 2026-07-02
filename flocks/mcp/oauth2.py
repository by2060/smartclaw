"""
MCP OAuth2 Client Credentials support.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, Optional

import httpx

from flocks.mcp.auth import McpAuth
from flocks.mcp.utils import build_mcp_headers, resolve_env_var
from flocks.utils.log import Log

log = Log.create(service="mcp.oauth2")

_OAUTH2_CLIENT_CREDENTIALS_TYPES = {
    "oauth2_client_credentials",
    "client_credentials",
}


class McpOAuth2ClientCredentials:
    """Resolve OAuth2 Client Credentials tokens for remote MCP servers."""

    _registrations: Dict[str, Dict[str, str]] = {}

    @classmethod
    def is_config(cls, auth_config: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(auth_config, dict):
            return False
        return str(auth_config.get("type", "")).strip().lower() in _OAUTH2_CLIENT_CREDENTIALS_TYPES

    @classmethod
    async def get_access_token(
        cls,
        server_name: str,
        auth_config: Dict[str, Any],
        timeout: float = 30.0,
    ) -> str:
        entry = await McpAuth.get(server_name)
        if entry and entry.tokens and entry.tokens.get("_auth_type") == "oauth2_client_credentials":
            access_token = entry.tokens.get("access_token")
            if access_token and not await McpAuth.is_token_expired(server_name):
                return str(access_token)

        client_id, client_secret, source = await cls._resolve_client_credentials(
            server_name, auth_config, timeout
        )
        try:
            return await cls._request_token(server_name, auth_config, client_id, client_secret, timeout)
        except RuntimeError as exc:
            if source != "cache" or not _is_token_auth_failure(exc):
                raise
            await cls.remove_registration(server_name)
            await McpAuth.remove(server_name)
            client_id, client_secret, _ = await cls._resolve_client_credentials(
                server_name, auth_config, timeout
            )
            return await cls._request_token(
                server_name, auth_config, client_id, client_secret, timeout
            )

    @classmethod
    async def build_headers(
        cls,
        server_name: str,
        base_headers: Optional[Dict[str, str]],
        auth_config: Optional[Dict[str, Any]],
        timeout: float = 30.0,
    ) -> Optional[Dict[str, str]]:
        headers = build_mcp_headers(base_headers, None) or {}
        if cls.is_config(auth_config):
            token = await cls.get_access_token(server_name, auth_config or {}, timeout)
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers.update(build_mcp_headers(None, auth_config) or {})
        return headers or None

    @classmethod
    async def clear(cls) -> None:
        cls._registrations.clear()

    @classmethod
    async def remove_registration(cls, server_name: str) -> None:
        if cls._registrations.pop(server_name, None) is not None:
            log.info("mcp.oauth2.registration_removed", {"server": server_name})

    @classmethod
    async def _resolve_client_credentials(
        cls,
        server_name: str,
        auth_config: Dict[str, Any],
        timeout: float,
    ) -> tuple[str, str, str]:
        client_id = _resolve_config_value(auth_config, "client_id", "clientId")
        client_secret = _resolve_config_value(auth_config, "client_secret", "clientSecret")
        if client_id and client_secret:
            return client_id, client_secret, "config"

        cached = cls._registrations.get(server_name)
        if cached:
            return cached["client_id"], cached["client_secret"], "cache"

        registration_url = _resolve_config_value(auth_config, "registration_url", "registrationUrl")
        if not registration_url:
            raise RuntimeError("MCP OAuth2 client credentials require client_id/client_secret or registration_url")

        registration = await cls._register_client(server_name, auth_config, registration_url, timeout)
        cls._registrations[server_name] = registration
        return registration["client_id"], registration["client_secret"], "registration"

    @classmethod
    async def _register_client(
        cls,
        server_name: str,
        auth_config: Dict[str, Any],
        registration_url: str,
        timeout: float,
    ) -> Dict[str, str]:
        payload: Dict[str, Any] = {
            "client_name": auth_config.get("client_name") or auth_config.get("clientName") or server_name,
            "grant_types": auth_config.get("grant_types") or auth_config.get("grantTypes") or "client_credentials",
            "token_endpoint_auth_method": auth_config.get("token_endpoint_auth_method")
            or auth_config.get("tokenEndpointAuthMethod")
            or "client_secret_basic",
        }
        if "redirect_uris" in auth_config or "redirectUris" in auth_config:
            payload["redirect_uris"] = auth_config.get("redirect_uris") or auth_config.get("redirectUris")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                registration_url,
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )

        if response.status_code >= 400:
            raise RuntimeError(f"MCP OAuth2 client registration failed with HTTP {response.status_code}")

        data = response.json()
        client_id = data.get("client_id") or data.get("clientId")
        client_secret = data.get("client_secret") or data.get("clientSecret")
        if not client_id or not client_secret:
            raise RuntimeError("MCP OAuth2 client registration response missing client_id/client_secret")

        log.info("mcp.oauth2.registered", {"server": server_name})
        return {"client_id": str(client_id), "client_secret": str(client_secret)}

    @classmethod
    async def _request_token(
        cls,
        server_name: str,
        auth_config: Dict[str, Any],
        client_id: str,
        client_secret: str,
        timeout: float,
    ) -> str:
        token_url = _resolve_config_value(auth_config, "token_url", "tokenUrl")
        if not token_url:
            raise RuntimeError("MCP OAuth2 token_url is required")

        data = {
            "grant_type": auth_config.get("grant_type") or auth_config.get("grantType") or "client_credentials",
        }
        audience = auth_config.get("audience")
        if audience:
            data["audience"] = str(audience)
        scope = auth_config.get("scope")
        if scope:
            data["scope"] = str(scope)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        auth_method = str(
            auth_config.get("token_endpoint_auth_method")
            or auth_config.get("tokenEndpointAuthMethod")
            or "client_secret_basic"
        ).strip().lower()

        auth: Optional[tuple[str, str]] = None
        if auth_method == "client_secret_basic":
            headers["Authorization"] = _basic_authorization(client_id, client_secret)
        elif auth_method == "client_secret_post":
            data["client_id"] = client_id
            data["client_secret"] = client_secret
        elif auth_method == "none":
            data["client_id"] = client_id
        else:
            auth = (client_id, client_secret)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(token_url, data=data, headers=headers, auth=auth)

        if response.status_code >= 400:
            raise RuntimeError(f"MCP OAuth2 token request failed with HTTP {response.status_code}")

        token_response = response.json()
        access_token = token_response.get("access_token") or token_response.get("accessToken")
        if not access_token:
            raise RuntimeError("MCP OAuth2 token response missing access_token")

        expires_in = _as_int(token_response.get("expires_in") or token_response.get("expiresIn"))
        token_response["_auth_type"] = "oauth2_client_credentials"
        token_response["_cached_at"] = time.time()
        await McpAuth.set(server_name, token_response, expires_in=expires_in)

        log.info("mcp.oauth2.token_acquired", {"server": server_name, "expires_in": expires_in})
        return str(access_token)


def _resolve_config_value(config: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return resolve_env_var(text)
    return None


def _basic_authorization(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_token_auth_failure(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "MCP OAuth2 token request failed with HTTP 401" in message
        or "MCP OAuth2 token request failed with HTTP 403" in message
    )


__all__ = ["McpOAuth2ClientCredentials"]
