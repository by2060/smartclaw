"""SMG chat proxy with request-scoped, protected gateway headers."""
from __future__ import annotations
import asyncio
import inspect
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from flocks.provider.provider import BaseProvider, ChatMessage, ChatResponse, ProviderConfig, StreamChunk
from flocks.provider.sdk.openai_base import OpenAIBaseProvider
from flocks.utils.log import Log


log = Log.create(service="provider.smg")


def _safe_url(value: Any) -> str:
    """Return a URL suitable for logs without credentials, query, or fragment."""
    try:
        parsed = urlsplit(str(value))
        hostname = parsed.hostname or ""
        if parsed.port is not None:
            hostname = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "invalid-url"


def _redact_detail(value: str, secrets: tuple[Optional[str], ...]) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[redacted]")
    return result[:500]


def _exception_diagnostics(
    exc: Exception,
    secrets: tuple[Optional[str], ...],
) -> tuple[list[str], Optional[str]]:
    """Extract exception types and a safe transport-level root cause."""
    chain: list[BaseException] = []
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__

    detail = None
    for item in reversed(chain):
        module = type(item).__module__.split(".", 1)[0]
        if module in {"httpx", "httpcore", "ssl", "socket"} or isinstance(item, OSError):
            detail = _redact_detail(str(item), secrets)
            break
    return [type(item).__name__ for item in chain], detail

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

    def _http_event_hooks(self) -> dict[str, list[Any]]:
        return {
            "request": [self._log_http_attempt],
            "response": [self._log_http_response],
        }

    async def _log_http_attempt(self, request: httpx.Request) -> None:
        request.extensions["flocks_smg_started_at"] = time.perf_counter()
        log.info("smg.http.attempt", {
            "provider_id": self.id,
            "method": request.method,
            "url": _safe_url(request.url),
            "span_id": request.headers.get("X-Span-Id"),
            "trace_id": request.headers.get("X-Trace-Id"),
            "retry_count": request.headers.get("x-stainless-retry-count", "0"),
            "content_length": request.headers.get("content-length"),
        })

    async def _log_http_response(self, response: httpx.Response) -> None:
        started_at = response.request.extensions.get("flocks_smg_started_at")
        duration_ms = (
            int((time.perf_counter() - started_at) * 1000)
            if isinstance(started_at, (int, float))
            else None
        )
        log.info("smg.http.response", {
            "provider_id": self.id,
            "method": response.request.method,
            "url": _safe_url(response.request.url),
            "span_id": response.request.headers.get("X-Span-Id"),
            "trace_id": response.request.headers.get("X-Trace-Id"),
            "retry_count": response.request.headers.get("x-stainless-retry-count", "0"),
            "status_code": response.status_code,
            "request_id": response.headers.get("x-request-id"),
            "duration_ms": duration_ms,
        })

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
        model_id: Optional[str] = None,
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
            log.info("smg.request.prepared", {
                "provider_id": self.id,
                "model_id": model_id,
                "call_source": request_state["call_source"],
                "url": _safe_url(self._config.base_url if self._config else None),
                "span_id": span_id,
                "trace_id": request_state["trace_id"],
                "has_user": "X-User" in headers,
                "has_session": "X-Session-Id" in headers,
            })
            return headers

        result["_extra_headers_factory"] = _headers_factory
        result["_smg_gateway_context"] = context
        return result, request_state

    def _log_request_failure(
        self,
        model_id: str,
        state: dict[str, Optional[str]],
        exc: Exception,
        *,
        started_at: float,
        stream: bool,
        received_chunks: int = 0,
    ) -> None:
        request = getattr(exc, "request", None)
        headers = getattr(request, "headers", {}) or {}
        secrets = (
            self._config.api_key if self._config else None,
            headers.get("X-User"),
        )
        error_chain, cause_detail = _exception_diagnostics(exc, secrets)
        response = getattr(exc, "response", None)
        log.error("smg.request.failed", {
            "provider_id": self.id,
            "model_id": model_id,
            "call_source": state.get("call_source") or "unknown",
            "url": _safe_url(getattr(request, "url", None) or (self._config.base_url if self._config else None)),
            "span_id": state.get("span_id") or "unassigned",
            "trace_id": state.get("trace_id"),
            "retry_count": headers.get("x-stainless-retry-count"),
            "stream": stream,
            "received_chunks": received_chunks,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "error_type": type(exc).__name__,
            "error_chain": error_chain,
            "cause_detail": cause_detail,
            "status_code": getattr(response, "status_code", None),
            "request_id": getattr(response, "headers", {}).get("x-request-id") if response is not None else None,
        })

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
        request_kwargs, state = self._request_kwargs(kwargs, model_id=model_id)
        started_at = time.perf_counter()
        try:
            return await super().chat(model_id, messages, **request_kwargs)
        except Exception as exc:
            self._log_request_failure(
                model_id, state, exc, started_at=started_at, stream=False
            )
            raise self._request_error(model_id, state, exc) from None

    async def chat_stream(self, model_id: str, messages: list[ChatMessage], **kwargs) -> AsyncIterator[StreamChunk]:
        request_kwargs, state = self._request_kwargs(kwargs, model_id=model_id)
        started_at = time.perf_counter()
        received_chunks = 0
        try:
            async for chunk in super().chat_stream(model_id, messages, **request_kwargs):
                received_chunks += 1
                yield chunk
        except Exception as exc:
            self._log_request_failure(
                model_id,
                state,
                exc,
                started_at=started_at,
                stream=True,
                received_chunks=received_chunks,
            )
            raise self._request_error(model_id, state, exc) from None

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._original_provider, name)
