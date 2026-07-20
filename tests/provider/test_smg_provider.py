from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from flocks.provider.provider import (
    BaseProvider,
    ChatMessage,
    ModelCapabilities,
    ModelInfo,
    ProviderConfig,
)
from flocks.provider.sdk.openai_base import OpenAIBaseProvider
from flocks.provider.smg_provider import GatewayRequestContext, SMGProvider


class _RawProvider(BaseProvider):
    def __init__(self):
        super().__init__("demo", "Demo")
        self._config_models = [
            ModelInfo(
                id="demo-model",
                name="Demo Model",
                provider_id="demo",
                capabilities=ModelCapabilities(),
            )
        ]

    def get_models(self):
        return list(self._config_models)

    async def chat(self, model_id, messages, **kwargs):
        raise NotImplementedError

    async def chat_stream(self, model_id, messages, **kwargs):
        raise NotImplementedError
        yield


def _smg_config():
    return SimpleNamespace(
        enabled=True,
        base_url="https://smg.example/v1",
        api_key="smg-secret",
        platform_identifier="smartclaw",
    )


def test_proxy_creation_does_not_mutate_original_models():
    raw = _RawProvider()
    original_models = list(raw._config_models)
    raw.get_embedding_models = lambda: ["demo-embedding"]

    proxy = SMGProvider(raw, _smg_config())

    assert raw._config_models == original_models
    assert proxy.get_models() == original_models
    assert proxy.get_model_definitions()[0].id == "demo-model"

    assert proxy.get_embedding_models() == ["demo-embedding"]

def test_management_config_is_delegated_without_overwriting_gateway_config():
    raw = _RawProvider()
    proxy = SMGProvider(raw, _smg_config())
    gateway_config = proxy._config
    original_config = ProviderConfig(
        provider_id="demo",
        api_key="original-secret",
        base_url="https://original.example/v1",
    )

    proxy.configure(original_config)

    assert raw._config is original_config
    assert proxy._config is gateway_config
    assert proxy._config.base_url == "https://smg.example/v1"


def test_gateway_headers_are_protected_and_span_is_per_http_request():
    proxy = SMGProvider(_RawProvider(), _smg_config())
    context = GatewayRequestContext(
        session_chain_ids=("root", "child"),
        user_token="current-token",
        trace_id="message-id",
        call_source="test.smg",
    )

    kwargs, state = proxy._request_kwargs({
        "gateway_context": context,
        "extra_headers": {
            "Authorization": "Bearer attacker",
            "X-User": "attacker",
            "Accept": "application/json",
        },
    })
    first = kwargs["_extra_headers_factory"]()
    second = kwargs["_extra_headers_factory"]()

    assert first["X-Platform"] == "smartclaw"
    assert first["X-User"] == "current-token"
    assert first["X-Session-Id"] == "root-child"
    assert first["X-Trace-Id"] == "message-id"
    assert first["Accept"] == "application/json"
    assert "Authorization" not in first
    assert first["X-Span-Id"] != second["X-Span-Id"]
    assert state["span_id"] == second["X-Span-Id"]


def test_stream_fallback_context_is_preserved():
    proxy = SMGProvider(_RawProvider(), _smg_config())
    context = GatewayRequestContext(
        session_chain_ids=("root",),
        user_token="current-token",
        trace_id="message-id",
        call_source="test.fallback",
    )
    first_kwargs, _ = proxy._request_kwargs({"gateway_context": context})

    fallback_kwargs, _ = proxy._request_kwargs(first_kwargs)
    headers = fallback_kwargs["_extra_headers_factory"]()

    assert headers["X-User"] == "current-token"
    assert headers["X-Session-Id"] == "root"
    assert headers["X-Trace-Id"] == "message-id"

def test_model_manager_uses_original_provider_when_smg_is_enabled(monkeypatch):
    from flocks.provider.model_manager import ModelManager
    from flocks.provider.provider import Provider

    raw = _RawProvider()
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_models", {"demo-model": raw._config_models[0]})
    monkeypatch.setattr(Provider, "_smg_config", _smg_config())
    monkeypatch.setattr(Provider, "_smg_config_stale", False)
    monkeypatch.setattr(Provider, "_smg_providers", {})

    assert isinstance(Provider.get("demo"), SMGProvider)
    definitions = ModelManager().list_models(provider_id="demo")

    assert [definition.id for definition in definitions] == ["demo-model"]
    assert [model.id for model in raw._config_models] == ["demo-model"]




def test_custom_model_runtime_updates_original_provider(monkeypatch):
    from flocks.provider.provider import Provider
    from flocks.server.routes.custom_provider import CreateModelReq, _add_model_to_runtime

    raw = _RawProvider()
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_models", {"demo-model": raw._config_models[0]})
    monkeypatch.setattr(Provider, "_smg_config", _smg_config())
    monkeypatch.setattr(Provider, "_smg_config_stale", False)
    monkeypatch.setattr(Provider, "_smg_providers", {})

    _add_model_to_runtime(
        "demo",
        CreateModelReq(
            model_id="new-model",
            name="New Model",
            context_window=128000,
            max_output_tokens=4096,
            supports_vision=False,
            supports_tools=True,
            supports_streaming=True,
            supports_reasoning=False,
            input_price=0.0,
            output_price=0.0,
            currency="USD",
        ),
    )

    assert {model.id for model in raw._config_models} == {"demo-model", "new-model"}
    assert isinstance(Provider.get("demo"), SMGProvider)


@pytest.mark.asyncio
async def test_smg_proxy_cache_only_invalidates_when_gateway_config_changes(monkeypatch):
    from flocks.provider.provider import Provider

    raw = _RawProvider()
    config = SimpleNamespace(smg=_smg_config(), provider={})
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_models", {"demo-model": raw._config_models[0]})
    monkeypatch.setattr(Provider, "_smg_config", None)
    monkeypatch.setattr(Provider, "_smg_config_stale", False)
    monkeypatch.setattr(Provider, "_smg_providers", {})

    await Provider.apply_config(config)
    first = Provider.get("demo")
    await Provider.apply_config(config)
    second = Provider.get("demo")

    assert first is second

    changed = SimpleNamespace(
        smg=SimpleNamespace(
            enabled=True,
            base_url="https://new-smg.example/v1",
            api_key="new-secret",
            platform_identifier="smartclaw",
        ),
        provider={},
    )
    await Provider.apply_config(changed)
    third = Provider.get("demo")

    assert third is not first
    assert third._config.base_url == "https://new-smg.example/v1"


def test_config_clear_cache_invalidates_smg_proxy_cache(monkeypatch):
    from flocks.config.config import Config
    from flocks.provider.provider import Provider

    monkeypatch.setattr(Provider, "_smg_providers", {"demo": object()})
    raw = _RawProvider()
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_smg_config_stale", False)
    Config.clear_cache()

    assert Provider._smg_providers == {}
    assert Provider._smg_config_stale is True
    assert Provider._get_raw("demo") is raw
    with pytest.raises(RuntimeError, match="reload is pending"):
        Provider.get("demo")




@pytest.mark.asyncio
async def test_smg_error_is_redacted():
    proxy = SMGProvider(_RawProvider(), _smg_config())
    context = GatewayRequestContext(
        user_token="sensitive-current-token",
        trace_id="message-id",
        call_source="test.error",
    )

    with patch.object(
        OpenAIBaseProvider,
        "chat",
        new=AsyncMock(side_effect=RuntimeError("sensitive-current-token smg-secret")),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await proxy.chat(
                "demo-model",
                [ChatMessage(role="user", content="hello")],
                gateway_context=context,
            )

    message = str(exc_info.value)
    assert "sensitive-current-token" not in message
    assert exc_info.value.__cause__ is None
    assert "smg-secret" not in message
    assert "provider_id=demo" in message
    assert "model_id=demo-model" in message
    assert "call_source=test.error" in message
    assert "trace_id=message-id" in message



@pytest.mark.asyncio
async def test_smg_client_is_reused_within_same_event_loop():
    proxy = SMGProvider(_RawProvider(), _smg_config())
    clients = []

    def create_client(_self):
        client = object()
        clients.append(client)
        return client

    with patch.object(OpenAIBaseProvider, "_get_client", create_client):
        first = proxy._get_client()
        second = proxy._get_client()

    assert first is second
    assert len(clients) == 1


def test_smg_client_is_isolated_between_event_loops():
    import asyncio

    proxy = SMGProvider(_RawProvider(), _smg_config())
    clients = []

    def create_client(_self):
        client = object()
        clients.append(client)
        return client

    async def get_client():
        return proxy._get_client()

    with patch.object(OpenAIBaseProvider, "_get_client", create_client):
        first = asyncio.run(get_client())
        second = asyncio.run(get_client())

    assert first is not second
    assert len(clients) == 2


def test_provider_get_creates_single_proxy_under_thread_contention(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from flocks.provider.provider import Provider

    raw = _RawProvider()
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_smg_config", _smg_config())
    monkeypatch.setattr(Provider, "_smg_config_stale", False)
    monkeypatch.setattr(Provider, "_smg_providers", {})

    with ThreadPoolExecutor(max_workers=8) as executor:
        proxies = list(executor.map(lambda _: Provider.get("demo"), range(64)))

    assert len({id(proxy) for proxy in proxies}) == 1


def test_invalidate_smg_cache_closes_proxy_clients(monkeypatch):
    from flocks.provider.provider import Provider

    proxy = SimpleNamespace(close_clients=Mock())
    monkeypatch.setattr(Provider, "_smg_providers", {"demo": proxy})

    Provider.invalidate_smg_cache("demo")

    assert Provider._smg_providers == {}
    proxy.close_clients.assert_called_once_with()


def test_unregister_removes_raw_provider_and_closes_proxy(monkeypatch):
    from flocks.provider.provider import Provider

    raw = _RawProvider()
    proxy = SimpleNamespace(close_clients=Mock())
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_smg_providers", {"demo": proxy})

    Provider.unregister("demo")

    assert "demo" not in Provider._providers
    assert "demo" not in Provider._smg_providers
    proxy.close_clients.assert_called_once_with()