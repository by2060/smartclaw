import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from smartclaw.channel.builtin.feishu import media as feishu_media
from smartclaw.channel.builtin.weixin import channel as weixin_channel
from smartclaw.channel.builtin.weixin.channel import WeixinChannel


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _event_payloads(relative_path: str, event: str, method: str) -> list[set[str]]:
    source_path = PROJECT_ROOT / relative_path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    payloads = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != method:
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "log":
            continue
        if not isinstance(node.args[0], ast.Constant) or node.args[0].value != event:
            continue
        payload = node.args[1]
        if isinstance(payload, ast.Dict):
            payloads.append({key.value for key in payload.keys if isinstance(key, ast.Constant)})
    return payloads


@pytest.mark.parametrize(
    ("path", "event", "method"),
    [
        ("smartclaw/channel/builtin/feishu/media.py", "feishu.media.upload_failed", "warning"),
        ("smartclaw/channel/builtin/weixin/channel.py", "weixin.media.fetch_failed", "warning"),
        ("smartclaw/channel/inbound/dispatcher.py", "dispatcher.inbound_media_download_failed", "warning"),
    ],
)
def test_media_failure_log_payloads_exclude_raw_urls(path, event, method):
    payloads = _event_payloads(path, event, method)
    assert len(payloads) == 1
    assert "url" not in payloads[0]
    assert "media_url" not in payloads[0]
    assert "body" not in payloads[0]


@pytest.mark.parametrize(
    ("path", "event", "outer_payload"),
    [
        ("smartclaw/provider/sdk/cloudflare_gateway.py", "cloudflare_gateway.chat.error", {"error", "model"}),
        ("smartclaw/provider/sdk/gitlab.py", "gitlab.chat.error", {"error"}),
        ("smartclaw/provider/sdk/sap_ai_core.py", "sap_ai_core.chat.error", {"error", "model"}),
        ("smartclaw/provider/sdk/vertex_anthropic.py", "vertex_anthropic.chat.error", {"error", "model"}),
    ],
)
def test_provider_error_logs_keep_status_but_drop_response_body(path, event, outer_payload):
    payloads = _event_payloads(path, event, "error")
    assert {frozenset(payload) for payload in payloads} == {
        frozenset({"status"}),
        frozenset(outer_payload),
    }
    assert all("body" not in payload for payload in payloads)


def test_pty_and_runner_no_longer_log_sensitive_command_payloads():
    pty_source = (PROJECT_ROOT / "smartclaw/pty/pty.py").read_text(encoding="utf-8")
    runner_source = (PROJECT_ROOT / "smartclaw/session/runner.py").read_text(encoding="utf-8")
    assert '"pty.creating"' not in pty_source
    assert '"runner.command"' not in runner_source


def test_windows_bash_host_log_is_not_duplicated_with_sensitive_command_fields():
    payloads = _event_payloads("smartclaw/tool/code/bash.py", "bash.execute.host", "info")
    assert len(payloads) == 1
    assert payloads[0] == {"command", "cwd", "shell"}


@pytest.mark.asyncio
async def test_feishu_upload_failure_log_does_not_include_original_url(monkeypatch):
    sender = AsyncMock(return_value={"message_id": "message"})
    warning = MagicMock()
    monkeypatch.setattr(feishu_media, "_fetch_url_bytes", AsyncMock(side_effect=RuntimeError("private body")))
    monkeypatch.setattr(feishu_media.log, "warning", warning)
    monkeypatch.setattr("smartclaw.channel.builtin.feishu.send.send_message_feishu", sender)
    url = "https://private.example/download?token=secret"

    result = await feishu_media.send_media_feishu(
        config={}, to="chat", text="caption", media_url=url, reply_to_id=None, account_id=None
    )

    assert result == {"message_id": "message"}
    warning.assert_called_once_with("feishu.media.upload_failed", {"error": "private body"})
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_weixin_media_failure_log_does_not_include_original_url(monkeypatch):
    channel = object.__new__(WeixinChannel)
    channel._send_session = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr(weixin_channel.log, "warning", warning)
    monkeypatch.setattr(
        "smartclaw.channel.builtin.weixin.cdn.assert_weixin_cdn_url",
        MagicMock(side_effect=RuntimeError("private response")),
    )
    url = "https://private.example/media?access_token=secret"

    path, cleanup = await channel._resolve_media_to_path(url)

    assert path is None
    assert cleanup is False
    warning.assert_called_once_with("weixin.media.fetch_failed", {"error": "private response"})
