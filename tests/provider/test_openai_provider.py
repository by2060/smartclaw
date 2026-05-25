from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.provider.provider import ChatMessage, ProviderConfig
from flocks.provider.sdk.openai import OpenAIProvider


class _AsyncStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class TestOpenAIProviderConfiguration:
    @patch("httpx.AsyncClient")
    @patch("openai.AsyncOpenAI")
    def test_get_client_respects_verify_ssl_false(self, mock_async_openai, mock_http_client):
        provider = OpenAIProvider()
        provider.configure(
            ProviderConfig(
                provider_id=provider.id,
                api_key="test-api-key",
                base_url="https://gateway.internal/v1",
                custom_settings={"verify_ssl": False},
            )
        )

        http_client = MagicMock()
        mock_http_client.return_value = http_client
        mock_async_openai.return_value = MagicMock()

        provider._get_client()

        # Granular timeout supports multimodal payloads; verify fields
        # semantically so minor adjustments to non-critical values don't break.
        assert mock_http_client.call_count == 1
        kwargs = mock_http_client.call_args.kwargs
        assert kwargs["trust_env"] is True
        assert kwargs["verify"] is False
        timeout_arg = kwargs["timeout"]
        assert getattr(timeout_arg, "connect", None) == 30.0
        assert getattr(timeout_arg, "read", None) == 600.0
        assert getattr(timeout_arg, "write", None) == 600.0

        mock_async_openai.assert_called_once_with(
            api_key="test-api-key",
            base_url="https://gateway.internal/v1",
            http_client=http_client,
        )

    @pytest.mark.asyncio
    async def test_chat_stream_preserves_length_finish_reason_for_tool_calls(self):
        provider = OpenAIProvider()
        create = AsyncMock(return_value=_AsyncStream([
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="write",
                                        arguments="{",
                                    ),
                                )
                            ],
                        ),
                    )
                ],
            ),
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        delta=SimpleNamespace(content=None, tool_calls=None),
                    )
                ],
            ),
        ]))
        provider._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        chunks = [
            chunk async for chunk in provider.chat_stream(
                "gpt-test",
                [ChatMessage(role="user", content="write a file")],
                tools=[{"type": "function", "function": {"name": "write"}}],
            )
        ]

        terminal = chunks[-1]
        assert terminal.finish_reason == "length"
        assert terminal.tool_calls[0]["function"]["arguments"] == "{"
