import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
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
from flocks.utils.log import Log


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


def test_config_clear_cache_preserves_smg_runtime(monkeypatch):
    from flocks.config.config import Config
    from flocks.provider.provider import Provider

    proxy = object()
    monkeypatch.setattr(Provider, "_smg_providers", {"demo": proxy})
    raw = _RawProvider()
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"demo": raw})
    monkeypatch.setattr(Provider, "_smg_config_stale", False)
    Config.clear_cache()

    assert Provider._smg_providers == {"demo": proxy}
    assert Provider._smg_config_stale is False
    assert Provider._get_raw("demo") is raw




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


@pytest.mark.asyncio
async def test_smg_http_hooks_log_correlated_redacted_metadata():
    proxy = SMGProvider(_RawProvider(), _smg_config())
    request = httpx.Request(
        "POST",
        "https://user:password@smg.example/v1/chat/completions?api_key=query-secret",
        headers={
            "Authorization": "Bearer auth-secret",
            "X-User": "user-secret",
            "X-Span-Id": "span-123",
            "X-Trace-Id": "trace-456",
            "x-stainless-retry-count": "2",
            "content-length": "123",
        },
    )
    response = httpx.Response(
        502,
        headers={"x-request-id": "gateway-request-789"},
        request=request,
    )
    old_writer = Log._writer
    old_level = Log._level
    Log._writer = io.StringIO()
    Log._level = "INFO"
    try:
        await proxy._log_http_attempt(request)
        await proxy._log_http_response(response)
        output = Log._writer.getvalue()
    finally:
        Log._writer = old_writer
        Log._level = old_level

    assert "smg.http.attempt" in output
    assert "smg.http.response" in output
    assert "url=https://smg.example/v1/chat/completions" in output
    assert "span_id=span-123" in output
    assert "trace_id=trace-456" in output
    assert "retry_count=2" in output
    assert "status_code=502" in output
    assert "request_id=gateway-request-789" in output
    assert "password" not in output
    assert "query-secret" not in output
    assert "auth-secret" not in output
    assert "user-secret" not in output


def test_smg_failure_log_redacts_transport_cause():
    proxy = SMGProvider(_RawProvider(), _smg_config())
    request = httpx.Request(
        "POST",
        "https://smg.example/v1/chat/completions",
        headers={
            "X-User": "user-secret",
            "x-stainless-retry-count": "2",
        },
    )
    error = httpx.ConnectError(
        "connection refused: user-secret smg-secret",
        request=request,
    )
    old_writer = Log._writer
    old_level = Log._level
    Log._writer = io.StringIO()
    Log._level = "INFO"
    try:
        proxy._log_request_failure(
            "demo-model",
            {"span_id": "span-123", "trace_id": "trace-456", "call_source": "test"},
            error,
            started_at=0.0,
            stream=True,
        )
        output = Log._writer.getvalue()
    finally:
        Log._writer = old_writer
        Log._level = old_level

    assert "smg.request.failed" in output
    assert "error_chain=['ConnectError']" in output
    assert "cause_detail=connection refused: [redacted] [redacted]" in output
    assert "user-secret" not in output
    assert "smg-secret" not in output


@pytest.mark.asyncio
async def test_gateway_context_uses_one_user_trace_for_all_assistant_steps(monkeypatch):
    from flocks.session.message import Message
    from flocks.session.session import Session

    user_message = SimpleNamespace(id="user-turn", role="user")
    messages = {
        "assistant-step-1": SimpleNamespace(
            id="assistant-step-1", role="assistant", parentID="user-turn"
        ),
        "assistant-step-2": SimpleNamespace(
            id="assistant-step-2", role="assistant", parentID="user-turn"
        ),
        "user-turn": user_message,
    }

    async def get_message(_session_id, message_id):
        return messages.get(message_id)

    monkeypatch.setattr(Session, "resolve_session_chain", AsyncMock(return_value=["session-1"]))
    monkeypatch.setattr(
        Session,
        "get_by_id",
        AsyncMock(return_value=SimpleNamespace(user_context={})),
    )
    monkeypatch.setattr(Message, "get", get_message)

    first = await Session.build_gateway_request_context(
        "session-1", trace_id="assistant-step-1", call_source="test"
    )
    second = await Session.build_gateway_request_context(
        "session-1", trace_id="assistant-step-2", call_source="test"
    )
    original = await Session.build_gateway_request_context(
        "session-1", trace_id="user-turn", call_source="test"
    )

    assert first.trace_id == "user-turn"
    assert second.trace_id == "user-turn"
    assert original.trace_id == "user-turn"


@pytest.mark.asyncio
async def test_two_child_sessions_share_the_parent_user_trace(monkeypatch):
    from flocks.session.message import Message
    from flocks.session.session import Session

    sessions = {
        "root-session": SimpleNamespace(metadata={}, user_context={}),
        "child-1": SimpleNamespace(
            metadata={"smgTraceId": "user-turn"},
            user_context={},
        ),
        "child-2": SimpleNamespace(
            metadata={"smgTraceId": "user-turn"},
            user_context={},
        ),
    }

    async def get_session(session_id):
        return sessions.get(session_id)

    async def resolve_chain(session_id):
        return ["root-session", session_id]

    monkeypatch.setattr(Session, "get_by_id", get_session)
    monkeypatch.setattr(Session, "resolve_session_chain", resolve_chain)
    message_get = AsyncMock(return_value=None)
    monkeypatch.setattr(Message, "get", message_get)

    first = await Session.build_gateway_request_context(
        "child-1", trace_id="child-assistant-1", call_source="session.runner"
    )
    second = await Session.build_gateway_request_context(
        "child-2", trace_id="child-assistant-2", call_source="session.runner"
    )

    assert first.trace_id == "user-turn"
    assert second.trace_id == "user-turn"
    assert first.session_chain_ids == ("root-session", "child-1")
    assert second.session_chain_ids == ("root-session", "child-2")
    message_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_child_session_inherits_parent_user_trace_without_losing_metadata(monkeypatch):
    from flocks.session.message import Message
    from flocks.session.session import Session

    parent_session = SimpleNamespace(metadata={})
    child_session = SimpleNamespace(
        id="child-session",
        project_id="project-1",
        metadata={"existing": "value", "smgTraceId": "previous-turn"},
    )
    messages = {
        "parent-assistant": SimpleNamespace(
            id="parent-assistant",
            role="assistant",
            parentID="user-turn",
        ),
        "user-turn": SimpleNamespace(id="user-turn", role="user"),
    }

    async def get_session(session_id):
        return parent_session if session_id == "parent-session" else child_session

    async def get_message(_session_id, message_id):
        return messages.get(message_id)

    update = AsyncMock(return_value=child_session)
    monkeypatch.setattr(Session, "get_by_id", get_session)
    monkeypatch.setattr(Session, "update", update)
    monkeypatch.setattr(Message, "get", get_message)

    await Session.inherit_gateway_trace(
        child_session,
        "parent-session",
        "parent-assistant",
    )

    update.assert_awaited_once_with(
        "project-1",
        "child-session",
        metadata={"existing": "value", "smgTraceId": "user-turn"},
    )
