from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from smartclaw.tool.auth_crypto import decrypt_sm4_value


SMART_AUTH_PUBLIC_KEY = (
    "04fff201a34e823e204843835134e8f2e6b122d4521db3ad35daa8e1fe60a343"
    "fa6438bc162a5dc9ff33dfec5faf377e54747c42626e9664c1127bfc70d2e5033a"
)
SMART_AUTH_LOGIN_PATH = "/webapi/api/v1/login"
SMART_AUTH_CACHE_TTL_SECONDS = 600

_SM4_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


class SmartAuthError(ValueError):
    """Safe, user-facing SmartAuth failure without credential material."""


class SmartAuthConfigError(SmartAuthError):
    def __init__(self, path: str, message: str):
        super().__init__(message)
        self.path = path


def sm2_encrypt_password(password: str) -> str:
    if not password:
        raise ValueError("SmartAuth password cannot be empty")

    from gmssl import sm2

    encryptor = sm2.CryptSM2(
        public_key=SMART_AUTH_PUBLIC_KEY[2:],
        private_key="",
        mode=1,
    )
    cipher = encryptor.encrypt(password.encode("utf-8"))
    if not cipher:
        raise ValueError("SmartAuth password SM2 encryption failed")
    return "04" + cipher.hex()


def _normalize_base_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SmartAuthConfigError(
            "provider.defaults.base_url",
            "SmartAuth base_url cannot be empty",
        )

    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise SmartAuthConfigError(
            "provider.defaults.base_url",
            "SmartAuth base_url must be an http or https URL",
        )
    if parsed.username is not None or parsed.password is not None:
        raise SmartAuthConfigError(
            "provider.defaults.base_url",
            "SmartAuth base_url must not contain user information",
        )
    if parsed.query or parsed.fragment:
        raise SmartAuthConfigError(
            "provider.defaults.base_url",
            "SmartAuth base_url must not contain query parameters or fragments",
        )

    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError as e:
        raise SmartAuthConfigError(
            "provider.defaults.base_url",
            "SmartAuth base_url contains an invalid port",
        ) from e
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _credential_entries(provider: Mapping[str, Any]) -> dict[str, tuple[int, Mapping[str, Any]]]:
    raw_fields = provider.get("credential_fields")
    if not isinstance(raw_fields, list):
        raise SmartAuthConfigError(
            "provider.credential_fields",
            "SmartAuth credential_fields must be a list",
        )

    entries: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for index, raw_field in enumerate(raw_fields):
        if not isinstance(raw_field, Mapping):
            continue
        key = raw_field.get("key")
        if key not in {"tenant", "username", "password"}:
            continue
        if key in entries:
            raise SmartAuthConfigError(
                f"provider.credential_fields[{index}].key",
                f"SmartAuth credential field '{key}' is duplicated",
            )
        entries[key] = (index, raw_field)
    return entries


def _decrypt_credential(
    entries: Mapping[str, tuple[int, Mapping[str, Any]]],
    key: str,
    *,
    required: bool,
) -> str:
    entry = entries.get(key)
    if entry is None:
        if required:
            raise SmartAuthConfigError(
                "provider.credential_fields",
                f"SmartAuth {key} credential field is required",
            )
        return ""

    index, raw_field = entry
    path = f"provider.credential_fields[{index}].config_value"
    raw_value = raw_field.get("config_value")
    if raw_value is None or raw_value == "":
        if required:
            raise SmartAuthConfigError(path, f"SmartAuth {key} cannot be empty")
        return ""
    if not isinstance(raw_value, str):
        raise SmartAuthConfigError(path, f"SmartAuth {key} must be an SM4 hex ciphertext")

    cipher_hex = raw_value.strip()
    if (
        not cipher_hex
        or not _SM4_HEX_RE.fullmatch(cipher_hex)
        or len(cipher_hex) % 32 != 0
    ):
        raise SmartAuthConfigError(path, f"SmartAuth {key} must be an SM4 hex ciphertext")

    try:
        plaintext = decrypt_sm4_value(cipher_hex)
    except ValueError as e:
        raise SmartAuthConfigError(path, f"SmartAuth {key} SM4 decryption failed") from e
    if required and plaintext == "":
        raise SmartAuthConfigError(path, f"SmartAuth {key} cannot be empty")
    return plaintext


@dataclass(frozen=True)
class SmartAuthConfig:
    base_url: str
    tenant: str
    username: str
    password: str
    verify_ssl: bool
    header_name: str
    header_prefix: str

    @property
    def login_url(self) -> str:
        return f"{self.base_url}{SMART_AUTH_LOGIN_PATH}"

    @property
    def cache_key(self) -> str:
        material = f"{self.base_url}{self.tenant}{self.username}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_smart_auth_config(provider: Mapping[str, Any]) -> SmartAuthConfig:
    if provider.get("authType") != "smartAuth":
        raise SmartAuthConfigError(
            "provider.authType",
            "SmartAuth provider must use authType smartAuth",
        )

    defaults = provider.get("defaults")
    if not isinstance(defaults, Mapping):
        defaults = {}
    base_url = _normalize_base_url(defaults.get("base_url"))

    entries = _credential_entries(provider)
    tenant = _decrypt_credential(entries, "tenant", required=False)
    username = _decrypt_credential(entries, "username", required=True)
    password = _decrypt_credential(entries, "password", required=True)

    auth = provider.get("auth")
    if auth is None:
        auth = {}
    if not isinstance(auth, Mapping):
        raise SmartAuthConfigError("provider.auth", "SmartAuth auth must be an object")
    if auth.get("inject_as", "header") != "header":
        raise SmartAuthConfigError(
            "provider.auth.inject_as",
            "SmartAuth only supports header token injection",
        )
    header_name = auth.get("header_name", "Authorization")
    if not isinstance(header_name, str) or not header_name.strip():
        raise SmartAuthConfigError(
            "provider.auth.header_name",
            "SmartAuth header_name cannot be empty",
        )
    header_prefix = auth.get("header_prefix", "")
    if header_prefix is None:
        header_prefix = ""
    if not isinstance(header_prefix, str):
        raise SmartAuthConfigError(
            "provider.auth.header_prefix",
            "SmartAuth header_prefix must be a string",
        )

    return SmartAuthConfig(
        base_url=base_url,
        tenant=tenant,
        username=username,
        password=password,
        verify_ssl=_as_bool(defaults.get("verify_ssl"), False),
        header_name=header_name.strip(),
        header_prefix=header_prefix,
    )


@dataclass(frozen=True)
class _TokenEntry:
    token: str
    expires_at: float


class _SmartAuthTokenCache:
    def __init__(self) -> None:
        self._entries: dict[str, _TokenEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _valid_entry(self, key: str) -> Optional[_TokenEntry]:
        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > time.monotonic():
            return entry
        if entry is not None:
            self._entries.pop(key, None)
        return None

    async def get(self, config: SmartAuthConfig, session: Any) -> str:
        entry = self._valid_entry(config.cache_key)
        if entry is not None:
            return entry.token

        lock = self._locks.setdefault(config.cache_key, asyncio.Lock())
        async with lock:
            entry = self._valid_entry(config.cache_key)
            if entry is not None:
                return entry.token
            token = await _login(config, session)
            self._entries[config.cache_key] = _TokenEntry(
                token=token,
                expires_at=time.monotonic() + SMART_AUTH_CACHE_TTL_SECONDS,
            )
            return token

    async def refresh(self, config: SmartAuthConfig, session: Any, rejected_token: str) -> str:
        lock = self._locks.setdefault(config.cache_key, asyncio.Lock())
        async with lock:
            entry = self._valid_entry(config.cache_key)
            if entry is not None and entry.token != rejected_token:
                return entry.token

            self._entries.pop(config.cache_key, None)
            token = await _login(config, session)
            self._entries[config.cache_key] = _TokenEntry(
                token=token,
                expires_at=time.monotonic() + SMART_AUTH_CACHE_TTL_SECONDS,
            )
            return token

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
        self._locks.clear()


_TOKEN_CACHE = _SmartAuthTokenCache()


async def _login(config: SmartAuthConfig, session: Any) -> str:
    try:
        encrypted_password = sm2_encrypt_password(config.password)
    except Exception as e:
        raise SmartAuthError("SmartAuth password SM2 encryption failed") from e

    request_kwargs: dict[str, Any] = {
        "json": {
            "username": config.username,
            "password": encrypted_password,
            "tenant": config.tenant,
        }
    }
    if not config.verify_ssl:
        request_kwargs["ssl"] = False

    try:
        async with session.request("POST", config.login_url, **request_kwargs) as response:
            if response.status < 200 or response.status >= 300:
                await response.read()
                raise SmartAuthError(f"SmartAuth login failed with HTTP {response.status}")
            try:
                payload = await response.json(content_type=None)
            except Exception as e:
                raise SmartAuthError("SmartAuth login returned an invalid JSON response") from e
    except SmartAuthError:
        raise
    except Exception as e:
        raise SmartAuthError("SmartAuth login request failed") from e

    if not isinstance(payload, Mapping):
        raise SmartAuthError("SmartAuth login returned an invalid response")
    if payload.get("success") is False:
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            raise SmartAuthError(message.strip())
        raise SmartAuthError("SmartAuth login failed")

    token: Any = None
    entity = payload.get("entity")
    if isinstance(entity, Mapping):
        token = entity.get("token")
    if not isinstance(token, str) or not token:
        token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise SmartAuthError("SmartAuth login response does not contain a token")
    return token


class SmartAuthBinding:
    def __init__(self, config: SmartAuthConfig):
        self.config = config

    async def get_token(self, session: Any) -> str:
        return await _TOKEN_CACHE.get(self.config, session)

    async def refresh_token(self, session: Any, rejected_token: str) -> str:
        return await _TOKEN_CACHE.refresh(self.config, session, rejected_token)

    def inject(self, headers: dict[str, str], token: str) -> None:
        normalized_name = self.config.header_name.lower()
        for existing_name in list(headers):
            if existing_name.lower() == normalized_name and existing_name != self.config.header_name:
                headers.pop(existing_name, None)
        headers[self.config.header_name] = f"{self.config.header_prefix}{token}"


def build_smart_auth_binding(provider: Mapping[str, Any]) -> SmartAuthBinding:
    return SmartAuthBinding(build_smart_auth_config(provider))


def invalidate_smart_auth_provider(provider: Mapping[str, Any]) -> bool:
    if provider.get("authType") != "smartAuth":
        return False
    config = build_smart_auth_config(provider)
    _TOKEN_CACHE.invalidate(config.cache_key)
    return True


def clear_smart_auth_cache() -> None:
    _TOKEN_CACHE.clear()


if __name__ == "__main__":
    sm2_pd = sm2_encrypt_password("1qaz@WSX")
    print(sm2_pd)
