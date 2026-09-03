import asyncio
import runpy
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartclaw.tool import smart_auth as module
from smartclaw.tool import auth_crypto


def _provider(**overrides):
    provider = {
        "authType": "smartAuth",
        "defaults": {"base_url": "HTTPS://Example.COM:443/api", "verify_ssl": "true"},
        "credential_fields": [
            {"key": "tenant", "config_value": "a" * 32},
            {"key": "username", "config_value": "b" * 32},
            {"key": "password", "config_value": "c" * 32},
        ],
        "auth": {"header_name": " X-Token ", "header_prefix": "Token "},
    }
    for key, value in overrides.items():
        provider[key] = value
    return provider


def _response(*, status=200, payload=None, json_error=None):
    response = MagicMock()
    response.status = status
    response.read = AsyncMock(return_value=b"private response")
    response.json = AsyncMock(return_value=payload)
    if json_error is not None:
        response.json.side_effect = json_error
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _session(response):
    session = MagicMock()
    session.request = MagicMock(return_value=response)
    return session


@pytest.fixture(autouse=True)
def clear_cache():
    module.clear_smart_auth_cache()
    yield
    module.clear_smart_auth_cache()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" HTTPS://Example.COM:443/api/// ", "https://example.com/api"),
        ("http://Example.COM:80", "http://example.com"),
        ("http://Example.COM:8080/", "http://example.com:8080"),
        ("https://[2001:DB8::1]:443/api", "https://[2001:db8::1]/api"),
    ],
)
def test_normalize_base_url_canonicalizes_scheme_host_port_and_path(value, expected):
    assert module._normalize_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "example.com/api",
        "ftp://example.com",
        "http://",
        "http://user:password@example.com",
        "https://example.com/api?tenant=secret",
        "https://example.com/api#fragment",
        "https://example.com:invalid",
    ],
)
def test_normalize_base_url_rejects_unsafe_or_invalid_values(value):
    with pytest.raises(module.SmartAuthConfigError) as exc_info:
        module._normalize_base_url(value)
    assert exc_info.value.path == "provider.defaults.base_url"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (" YES ", True),
        ("off", False),
        ("unknown", True),
        (None, True),
    ],
)
def test_as_bool_accepts_supported_string_values(value, expected):
    assert module._as_bool(value, True) is expected


def test_build_config_decrypts_credentials_and_applies_auth_defaults():
    decrypted = {"a" * 32: "tenant-a", "b" * 32: "alice", "c" * 32: "password"}
    with patch.object(module, "decrypt_sm4_value", side_effect=decrypted.__getitem__) as decrypt:
        config = module.build_smart_auth_config(_provider())

    assert config.base_url == "https://example.com/api"
    assert config.tenant == "tenant-a"
    assert config.username == "alice"
    assert config.password == "password"
    assert config.verify_ssl is True
    assert config.header_name == "X-Token"
    assert config.header_prefix == "Token "
    assert config.login_url == "https://example.com/api/webapi/api/v1/login"
    assert len(config.cache_key) == 64
    assert decrypt.call_count == 3


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ({"authType": "bearerToken"}, "authType"),
        ({"authType": "smartAuth", "defaults": {"base_url": "https://example.com"}, "credential_fields": None}, "credential_fields"),
        ({"authType": "smartAuth", "defaults": {"base_url": "https://example.com"}, "credential_fields": []}, "username credential field is required"),
    ],
)
def test_build_config_rejects_wrong_type_or_missing_required_credentials(provider, message):
    with pytest.raises(module.SmartAuthConfigError, match=message):
        module.build_smart_auth_config(provider)


@pytest.mark.parametrize(
    "field",
    [
        {"key": "username", "config_value": ""},
        {"key": "username", "config_value": 123},
        {"key": "username", "config_value": "not-hex"},
        {"key": "username", "config_value": "a" * 31},
    ],
)
def test_build_config_validates_required_sm4_ciphertext(field):
    provider = _provider()
    provider["credential_fields"][1] = field
    with pytest.raises(module.SmartAuthConfigError):
        module.build_smart_auth_config(provider)


def test_build_config_rejects_duplicate_credential_keys_and_invalid_auth_options():
    provider = _provider()
    provider["credential_fields"].append({"key": "username", "config_value": "d" * 32})
    with pytest.raises(module.SmartAuthConfigError, match="duplicated"):
        module.build_smart_auth_config(provider)

    for auth in (
        {"inject_as": "query_param"},
        {"header_name": ""},
        {"header_prefix": 1},
        "invalid",
    ):
        provider = _provider(auth=auth)
        with pytest.raises(module.SmartAuthConfigError):
            module.build_smart_auth_config(provider)


def test_credential_helpers_cover_ignored_fields_and_validation_edges():
    entries = module._credential_entries(
        {
            "credential_fields": [
                None,
                "invalid",
                {"key": "ignored", "config_value": "value"},
                {"key": "username", "config_value": "a" * 32},
            ]
        }
    )
    assert list(entries) == ["username"]

    with pytest.raises(module.SmartAuthConfigError, match="cannot be empty"):
        module._decrypt_credential(
            {"username": (0, {"config_value": ""})},
            "username",
            required=True,
        )
    assert module._decrypt_credential(
        {"tenant": (0, {"config_value": None})},
        "tenant",
        required=False,
    ) == ""

    with pytest.raises(module.SmartAuthConfigError, match="SM4 hex ciphertext"):
        module._decrypt_credential(
            {"username": (0, {"config_value": 123})},
            "username",
            required=True,
        )
    with pytest.raises(module.SmartAuthConfigError, match="SM4 hex ciphertext"):
        module._decrypt_credential(
            {"username": (0, {"config_value": "not-hex"})},
            "username",
            required=True,
        )
    with patch.object(module, "decrypt_sm4_value", return_value=""):
        with pytest.raises(module.SmartAuthConfigError, match="cannot be empty"):
            module._decrypt_credential(
                {"username": (0, {"config_value": "a" * 32})},
                "username",
                required=True,
            )


def test_build_config_covers_defaults_and_auth_edge_cases():
    with pytest.raises(module.SmartAuthConfigError) as exc_info:
        module.build_smart_auth_config(_provider(defaults=[]))
    assert exc_info.value.path == "provider.defaults.base_url"

    decrypted = {"a" * 32: "tenant", "b" * 32: "user", "c" * 32: "password"}
    with patch.object(module, "decrypt_sm4_value", side_effect=decrypted.__getitem__):
        config = module.build_smart_auth_config(_provider(auth=None))
        assert config.header_name == "Authorization"
        assert config.header_prefix == ""

        for auth, path in (
            ("invalid", "provider.auth"),
            ({"inject_as": "query_param"}, "provider.auth.inject_as"),
            ({"header_name": ""}, "provider.auth.header_name"),
            ({"header_prefix": 123}, "provider.auth.header_prefix"),
        ):
            with pytest.raises(module.SmartAuthConfigError) as auth_error:
                module.build_smart_auth_config(_provider(auth=auth))
            assert auth_error.value.path == path

        config = module.build_smart_auth_config(_provider(auth={"header_prefix": None}))
        assert config.header_prefix == ""


def test_optional_tenant_can_be_omitted_and_unknown_fields_are_ignored():
    provider = _provider()
    provider["credential_fields"] = [
        {"key": "username", "config_value": "b" * 32},
        {"key": "password", "config_value": "c" * 32},
        {"key": "unrelated", "config_value": "secret"},
    ]
    with patch.object(module, "decrypt_sm4_value", side_effect={"b" * 32: "alice", "c" * 32: "pwd"}.__getitem__):
        config = module.build_smart_auth_config(provider)
    assert config.tenant == ""


def test_sm2_encrypt_password_validates_input_and_formats_ciphertext():
    with pytest.raises(ValueError, match="cannot be empty"):
        module.sm2_encrypt_password("")

    crypt = MagicMock()
    crypt.encrypt.return_value = b"\x01\x02"
    with patch("gmssl.sm2.CryptSM2", return_value=crypt) as constructor:
        assert module.sm2_encrypt_password("p@ss") == "040102"
    constructor.assert_called_once_with(
        public_key=module.SMART_AUTH_PUBLIC_KEY[2:], private_key="", mode=1
    )
    crypt.encrypt.assert_called_once_with("p@ss".encode("utf-8"))

    crypt.encrypt.return_value = b""
    with patch("gmssl.sm2.CryptSM2", return_value=crypt):
        with pytest.raises(ValueError, match="encryption failed"):
            module.sm2_encrypt_password("p@ss")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{"success": True, "entity": {"token": "entity-token"}}, {"success": True, "token": "top-token"}],
)
async def test_login_accepts_entity_or_top_level_token(payload):
    response = _response(payload=payload)
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "Bearer ")
    session = _session(response)
    with patch.object(module, "sm2_encrypt_password", return_value="encrypted"):
        token = await module._login(config, session)

    assert token in {"entity-token", "top-token"}
    session.request.assert_called_once_with(
        "POST",
        "https://example.com/webapi/api/v1/login",
        json={"username": "user", "password": "encrypted", "tenant": "tenant"},
        ssl=False,
    )


@pytest.mark.asyncio
async def test_login_omits_ssl_flag_when_ssl_verification_is_enabled():
    response = _response(payload={"token": "secure-token"})
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", True, "Authorization", "")
    session = _session(response)
    with patch.object(module, "sm2_encrypt_password", return_value="encrypted"):
        assert await module._login(config, session) == "secure-token"
    assert "ssl" not in session.request.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(status=503, payload={}), "HTTP 503"),
        (_response(payload={"success": False, "message": "bad credentials"}), "bad credentials"),
        (_response(payload={"success": False}), "login failed"),
        (_response(payload=[]), "invalid response"),
        (_response(payload={"success": True}), "does not contain a token"),
        (_response(json_error=RuntimeError("bad json")), "invalid JSON"),
    ],
)
async def test_login_reports_safe_failures(response, message):
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "")
    with patch.object(module, "sm2_encrypt_password", return_value="encrypted"):
        with pytest.raises(module.SmartAuthError, match=message):
            await module._login(config, _session(response))


@pytest.mark.asyncio
async def test_login_wraps_transport_and_encryption_errors():
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "")
    session = MagicMock()
    session.request.side_effect = OSError("network secret")
    with patch.object(module, "sm2_encrypt_password", return_value="encrypted"):
        with pytest.raises(module.SmartAuthError, match="request failed"):
            await module._login(config, session)

    with patch.object(module, "sm2_encrypt_password", side_effect=RuntimeError("private")):
        with pytest.raises(module.SmartAuthError, match="encryption failed"):
            await module._login(config, _session(_response(payload={"token": "unused"})))


@pytest.mark.asyncio
async def test_token_cache_hits_and_serializes_concurrent_logins():
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "")
    calls = 0

    async def login(_config, _session):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "cached-token"

    with patch.object(module, "_login", side_effect=login):
        result = await asyncio.gather(
            module._TOKEN_CACHE.get(config, object()),
            module._TOKEN_CACHE.get(config, object()),
        )
        assert await module._TOKEN_CACHE.get(config, object()) == "cached-token"

    assert result == ["cached-token", "cached-token"]
    assert calls == 1


@pytest.mark.asyncio
async def test_token_cache_refresh_reuses_newer_token_and_refreshes_rejected_token():
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "")
    login = AsyncMock(side_effect=["old-token", "new-token"])
    with patch.object(module, "_login", login):
        assert await module._TOKEN_CACHE.get(config, object()) == "old-token"
        module._TOKEN_CACHE._entries[config.cache_key] = module._TokenEntry("newer-token", module.time.monotonic() + 30)
        assert await module._TOKEN_CACHE.refresh(config, object(), "old-token") == "newer-token"
        assert await module._TOKEN_CACHE.refresh(config, object(), "newer-token") == "new-token"
    assert login.await_count == 2


def test_token_cache_removes_expired_entry():
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "")
    module._TOKEN_CACHE._entries[config.cache_key] = module._TokenEntry(
        "expired-token",
        module.time.monotonic() - 1,
    )

    assert module._TOKEN_CACHE._valid_entry(config.cache_key) is None
    assert config.cache_key not in module._TOKEN_CACHE._entries


@pytest.mark.asyncio
async def test_binding_token_methods_delegate_to_shared_cache():
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "Authorization", "")
    binding = module.SmartAuthBinding(config)
    session = object()

    with (
        patch.object(module._TOKEN_CACHE, "get", AsyncMock(return_value="token-1")) as get_token,
        patch.object(module._TOKEN_CACHE, "refresh", AsyncMock(return_value="token-2")) as refresh_token,
    ):
        assert await binding.get_token(session) == "token-1"
        assert await binding.refresh_token(session, "rejected") == "token-2"

    get_token.assert_awaited_once_with(config, session)
    refresh_token.assert_awaited_once_with(config, session, "rejected")


def test_binding_inject_replaces_case_variant_and_cache_control_helpers():
    config = module.SmartAuthConfig("https://example.com", "tenant", "user", "pwd", False, "X-Token", "Token ")
    binding = module.SmartAuthBinding(config)
    headers = {"x-token": "old", "Accept": "json"}
    binding.inject(headers, "abc")
    assert headers == {"Accept": "json", "X-Token": "Token abc"}

    assert module.invalidate_smart_auth_provider({"authType": "bearerToken"}) is False
    provider = _provider()
    with patch.object(module, "decrypt_sm4_value", side_effect=lambda value: value):
        assert module.invalidate_smart_auth_provider(provider) is True


def test_build_binding_uses_validated_config():
    with patch.object(module, "build_smart_auth_config") as build:
        config = SimpleNamespace(username="alice")
        build.return_value = config
        binding = module.build_smart_auth_binding({"authType": "smartAuth"})
    assert binding.config is config


def test_sm4_key_prefers_environment_and_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.setenv("SMARTCLAW_API_TOOL_SM4_KEY_HEX", "  env-key  ")
    assert auth_crypto.get_api_tool_sm4_key_hex() == "env-key"

    monkeypatch.setenv("SMARTCLAW_API_TOOL_SM4_KEY_HEX", "")
    with patch(
        "smartclaw.security.get_secret_manager",
        return_value=SimpleNamespace(get=lambda key: " secret-key " if key == "api_tool_sm4_key" else None),
    ):
        assert auth_crypto.get_api_tool_sm4_key_hex() == "secret-key"

    with patch("smartclaw.security.get_secret_manager", side_effect=RuntimeError("unavailable")):
        assert auth_crypto.get_api_tool_sm4_key_hex() is None


def test_decrypt_sm4_value_decodes_pkcs7_like_padding(monkeypatch):
    from gmssl.sm4 import CryptSM4, SM4_ENCRYPT

    key = "0123456789abcdeffedcba9876543210"
    plaintext = b"smart-auth"
    padding = 16 - len(plaintext) % 16
    padded = plaintext + bytes([padding]) * padding
    encryptor = CryptSM4()
    encryptor.set_key(bytes.fromhex(key), SM4_ENCRYPT)
    cipher_hex = encryptor.crypt_ecb(padded).hex()

    monkeypatch.setenv("SMARTCLAW_API_TOOL_SM4_KEY_HEX", key)
    assert auth_crypto.decrypt_sm4_value(cipher_hex) == "smart-auth"


def test_decrypt_sm4_value_returns_safe_errors_for_missing_or_invalid_key(monkeypatch):
    monkeypatch.delenv("SMARTCLAW_API_TOOL_SM4_KEY_HEX", raising=False)
    with patch("smartclaw.security.get_secret_manager", return_value=SimpleNamespace(get=lambda _key: None)):
        with pytest.raises(ValueError, match="not configured"):
            auth_crypto.decrypt_sm4_value("00" * 16)

    monkeypatch.setenv("SMARTCLAW_API_TOOL_SM4_KEY_HEX", "not-a-key")
    with pytest.raises(ValueError, match="Failed to decrypt"):
        auth_crypto.decrypt_sm4_value("00" * 16)


def test_smart_auth_module_main_prints_encrypted_smoke_value(capsys):
    encryptor = MagicMock()
    encryptor.encrypt.return_value = b"\x01\x02"

    with patch("gmssl.sm2.CryptSM2", return_value=encryptor):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            runpy.run_module("smartclaw.tool.smart_auth", run_name="__main__")

    assert capsys.readouterr().out.strip() == "040102"
