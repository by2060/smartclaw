"""SMG chat proxy with request-scoped, protected gateway headers."""
from __future__ import annotations
import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional
from uuid import uuid4
from flocks.provider.provider import BaseProvider, ChatMessage, ChatResponse, ProviderConfig, StreamChunk
from flocks.provider.sdk.openai_base import OpenAIBaseProvider

@dataclass(frozen=True)
class GatewayRequestContext:
    session_chain_ids: tuple[str, ...] = ()
    root_session_id: Optional[str] = None
    current_session_id: Optional[str] = None
    user_token: Optional[str] = None
    trace_id: Optional[str] = None
    call_source: str = "unknown"

    @property
    def session_chain_id(self) -> Optional[str]:
        return "-".join(self.session_chain_ids) if self.session_chain_ids else None

class SMGProvider(OpenAIBaseProvider):
    _ALLOWED_CALLER_HEADERS = {"accept", "user-agent"}

    def __init__(self, original_provider: BaseProvider, config: Any):
        self._original_provider = original_provider
        super().__init__(original_provider.id, original_provider.name)
        self._clients_by_loop: dict[asyncio.AbstractEventLoop, Any] = {}
        self._clients_lock = threading.RLock()
        self._gateway_platform = str(config.platform_identifier)
        OpenAIBaseProvider.configure(
            self,
            ProviderConfig(
                provider_id=original_provider.id,
                api_key=str(config.api_key),
                base_url=str(config.base_url),
            ),
        )

    def _get_client(self):
        """Return a client scoped to the active event loop."""
        loop = asyncio.get_running_loop()
        with self._clients_lock:
            client = self._clients_by_loop.get(loop)
            if client is None:
                self._client = None
                client = super()._get_client()
                self._client = None
                self._clients_by_loop[loop] = client
            return client

    def close_clients(self) -> None:
        """Detach and close all event-loop-scoped clients where possible."""
        with self._clients_lock:
            clients = list(self._clients_by_loop.items())
            self._clients_by_loop.clear()
            self._client = None

        for loop, client in clients:
            close = getattr(client, "close", None)
            if close is None or loop.is_closed():
                continue
            result = close()
            if not inspect.isawaitable(result):
                continue
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(result, loop)
            else:
                loop.run_until_complete(result)

    def configure(self, config: ProviderConfig) -> None:
        """Keep management/configuration changes on the wrapped provider."""
        self._original_provider.configure(config)

    def is_configured(self) -> bool:
        return bool(
            self._config
            and self._config.api_key
            and self._config.base_url
            and self._gateway_platform
        )
    def configure_from_credential(self, config: Any) -> None:
        self._original_provider.configure_from_credential(config)

    async def validate_credential(self, config: Any) -> Any:
        return await self._original_provider.validate_credential(config)

    def get_models(self):
        return self._original_provider.get_models()

    def get_model_definitions(self):
        return self._original_provider.get_model_definitions()

    def get_meta(self):
        return self._original_provider.get_meta()

    def supports_embeddings(self):
        return self._original_provider.supports_embeddings()

    async def embed(self, *args, **kwargs):
        return await self._original_provider.embed(*args, **kwargs)

    async def embed_batch(self, *args, **kwargs):
        return await self._original_provider.embed_batch(*args, **kwargs)
    def get_embedding_models(self):
        return self._original_provider.get_embedding_models()


    def _request_kwargs(
        self,
        kwargs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Optional[str]]]:
        result = dict(kwargs)
        context = result.pop("gateway_context", None)
        context = context or result.pop("_smg_gateway_context", None)
        result.pop("currentToken", None)
        result.pop("current_token", None)
        caller_headers = result.pop("extra_headers", None) or {}
        caller_headers = {
            str(k): str(v)
            for k, v in caller_headers.items()
            if str(k).lower() in self._ALLOWED_CALLER_HEADERS
        }
        request_state: dict[str, Optional[str]] = {
            "span_id": None,
            "trace_id": context.trace_id if isinstance(context, GatewayRequestContext) else None,
            "call_source": context.call_source if isinstance(context, GatewayRequestContext) else "unknown",
        }

        def _headers_factory() -> dict[str, str]:
            headers = dict(caller_headers)
            span_id = str(uuid4())
            request_state["span_id"] = span_id
            headers["X-Platform"] = self._gateway_platform
            headers["X-Span-Id"] = span_id
            if isinstance(context, GatewayRequestContext):
                if context.user_token:
                    headers["X-User"] = context.user_token
                if context.session_chain_id:
                    headers["X-Session-Id"] = context.session_chain_id
                if context.trace_id:
                    headers["X-Trace-Id"] = context.trace_id
            return headers

        result["_extra_headers_factory"] = _headers_factory
        result["_smg_gateway_context"] = context
        return result, request_state

    def _request_error(
        self,
        model_id: str,
        state: dict[str, Optional[str]],
        exc: Exception,
    ) -> RuntimeError:
        fields = [
            f"provider_id={self.id}",
            f"model_id={model_id}",
            f"call_source={state.get('call_source') or 'unknown'}",
            f"span_id={state.get('span_id') or 'unassigned'}",
        ]
        if state.get("trace_id"):
            fields.append(f"trace_id={state['trace_id']}")
        fields.append(f"error_type={type(exc).__name__}")
        return RuntimeError("SMG request failed (" + ", ".join(fields) + ")")

    async def chat(self, model_id: str, messages: list[ChatMessage], **kwargs) -> ChatResponse:
        request_kwargs, state = self._request_kwargs(kwargs)
        try:
            return await super().chat(model_id, messages, **request_kwargs)
        except Exception as exc:
            raise self._request_error(model_id, state, exc) from None

    async def chat_stream(self, model_id: str, messages: list[ChatMessage], **kwargs) -> AsyncIterator[StreamChunk]:
        request_kwargs, state = self._request_kwargs(kwargs)
        try:
            async for chunk in super().chat_stream(model_id, messages, **request_kwargs):
                yield chunk
        except Exception as exc:
            raise self._request_error(model_id, state, exc) from None

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._original_provider, name)
