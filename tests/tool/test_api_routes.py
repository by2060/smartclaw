from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from smartclaw.server.routes import tool as route_module
from smartclaw.session.session import Session
from smartclaw.tool import api_tool_draft as draft_module
from smartclaw.tool import tool_loader
from smartclaw.tool.api_tool_draft import APIToolDraft, ProviderDraft, ToolDraft
from smartclaw.tool.registry import ToolInfo, ToolRegistry
from smartclaw.tool.smart_auth import SmartAuthBinding, SmartAuthConfigError, SmartAuthError


@pytest.mark.asyncio
async def test_build_http_tool_context_copies_session_user_context():
    session = SimpleNamespace(
        category="user",
        user_context={"iamToken": "iam-token"},
        metadata={},
    )
    tool_info = ToolInfo(name="http_tool", description="test tool")

    with (
        patch.object(Session, "resolve_root_session_id", AsyncMock(return_value="ses_root")),
        patch.object(Session, "get_by_id", AsyncMock(return_value=session)),
    ):
        context = await route_module._build_http_tool_context(
            tool_name=tool_info.name,
            tool_info=tool_info,
            session_id="ses_child",
            message_id=None,
            agent=None,
        )

    assert context.extra["user_context"] == {"iamToken": "iam-token"}
    assert context.extra["user_context"] is not session.user_context
    assert context.extra["main_session_key"] == "ses_root"
    assert context.extra["output_session_id"] == "ses_root"


@pytest.mark.asyncio
async def test_build_http_tool_context_ignores_non_dict_user_context():
    session = SimpleNamespace(category="user", user_context="invalid", metadata={})
    tool_info = ToolInfo(name="http_tool", description="test tool")

    with (
        patch.object(Session, "resolve_root_session_id", AsyncMock(return_value="ses_root")),
        patch.object(Session, "get_by_id", AsyncMock(return_value=session)),
    ):
        context = await route_module._build_http_tool_context(
            tool_name=tool_info.name,
            tool_info=tool_info,
            session_id="ses_child",
            message_id=None,
            agent=None,
        )

    assert "user_context" not in context.extra


def test_validate_api_tool_draft_reports_smart_auth_and_empty_tools_errors():
    draft = APIToolDraft(
        provider=ProviderDraft(
            id="smart-auth-provider",
            authType="smartAuth",
            defaults={"base_url": "https://api.example.com"},
        ),
        tools=[],
    )

    with patch.object(
        draft_module,
        "build_smart_auth_config",
        side_effect=SmartAuthConfigError("provider.auth", "invalid smart auth"),
    ):
        issues = draft_module.validate_api_tool_draft(
            draft,
            check_collisions=False,
            allow_empty_tools=False,
        )

    assert [(issue.path, issue.message) for issue in issues] == [
        ("provider.auth", "invalid smart auth"),
        ("tools", "至少需要一个工具草稿"),
    ]


def test_validate_api_tool_draft_accepts_valid_smart_auth_and_empty_upsert():
    draft = APIToolDraft(
        provider=ProviderDraft(
            id="smart-auth-provider",
            authType="smartAuth",
            defaults={"base_url": "https://api.example.com"},
        ),
        tools=[],
    )

    with patch.object(draft_module, "build_smart_auth_config", return_value=object()):
        issues = draft_module.validate_api_tool_draft(
            draft,
            check_collisions=False,
            allow_empty_tools=True,
        )

    assert issues == []


def test_validate_api_tool_draft_checks_non_smartauth_auth_branch():
    draft = APIToolDraft(
        provider=ProviderDraft(
            id="smart-provider",
            authType="smart",
            authExt=[{"inject_as": "header", "key": "Authorization", "value": "cipher"}],
            defaults={"base_url": "https://api.example.com"},
        ),
        tools=[],
    )

    issues = draft_module.validate_api_tool_draft(
        draft,
        check_collisions=False,
        allow_empty_tools=True,
    )

    assert issues == []


@pytest.mark.asyncio
async def test_validate_api_tool_draft_route_forwards_upsert_options():
    draft = APIToolDraft(
        provider=ProviderDraft(
            id="provider-test",
            defaults={"base_url": "https://api.example.com"},
        ),
        tools=[],
    )
    request = route_module.ValidateAPIToolDraftRequest(
        draft=draft,
        check_collisions=False,
        mode="upsert",
    )

    with (
        patch.object(route_module, "normalize_api_tool_draft", return_value=draft),
        patch.object(route_module, "validate_api_tool_draft", return_value=[]) as validate,
    ):
        response = await route_module.validate_api_tool_draft_route(request, _admin=object())

    assert response.valid is True
    validate.assert_called_once_with(
        draft,
        check_collisions=False,
        allow_empty_tools=True,
    )


def test_reload_api_provider_tool_bindings_registers_each_yaml_tool(tmp_path: Path):
    yaml_path = tmp_path / "sample.yaml"
    tool = SimpleNamespace(info=SimpleNamespace(name="sample_tool", source=None))

    with (
        patch.object(tool_loader, "list_api_provider_tools", return_value=[yaml_path]),
        patch.object(tool_loader, "_read_yaml_raw", return_value={"name": "sample_tool"}) as read_yaml,
        patch.object(tool_loader, "yaml_to_tool", return_value=tool) as yaml_to_tool,
        patch.object(ToolRegistry, "init") as registry_init,
        patch.object(ToolRegistry, "register") as registry_register,
        patch.object(ToolRegistry, "_plugin_tool_names", []),
    ):
        route_module._reload_api_provider_tool_bindings("provider-test")
        assert ToolRegistry._plugin_tool_names == ["sample_tool"]

    registry_init.assert_called_once_with()
    read_yaml.assert_called_once_with(yaml_path)
    yaml_to_tool.assert_called_once_with({"name": "sample_tool"}, yaml_path)
    registry_register.assert_called_once_with(tool)
    assert tool.info.source == "plugin_yaml"


@pytest.mark.asyncio
async def test_confirm_empty_upsert_reloads_provider_and_invalidates_auth_cache(tmp_path: Path):
    provider_dir = tmp_path / "provider-test"
    provider_dir.mkdir()
    provider_path = provider_dir / "_provider.yaml"
    provider_path.write_text(
        "authType: smartAuth\ndefaults:\n  base_url: https://old.example.com\n",
        encoding="utf-8",
    )
    provider_yaml = {
        "authType": "smartAuth",
        "defaults": {"base_url": "https://new.example.com"},
    }
    request = route_module.ConfirmAPIToolDraftRequest(
        mode="upsert",
        delete_missing_tools=True,
        draft=APIToolDraft(
            provider=ProviderDraft(
                id="provider-test",
                name="Provider Test",
                authType="smartAuth",
                defaults={"base_url": "https://new.example.com"},
            ),
            tools=[],
        ),
    )

    invalidate = MagicMock(
        side_effect=[
            True,
            SmartAuthConfigError("provider.auth", "invalid smart auth"),
        ]
    )
    with (
        patch.object(route_module, "validate_api_tool_draft", return_value=[]),
        patch.object(route_module, "compile_provider_yaml", return_value=provider_yaml),
        patch.object(route_module, "_reload_api_provider_tool_bindings") as reload_bindings,
        patch.object(route_module, "_invalidate_agent_cache_after_tool_change") as invalidate_agents,
        patch.object(tool_loader, "find_api_provider_dir", return_value=provider_dir),
        patch.object(tool_loader, "create_api_provider_yaml", return_value=provider_path) as create_provider,
        patch("smartclaw.tool.smart_auth.invalidate_smart_auth_provider", invalidate),
    ):
        response = await route_module.confirm_api_tool_draft_route(request, _admin=object())

    assert response.provider_path == str(provider_path)
    assert response.tool_paths == []
    assert response.tools == []
    create_provider.assert_called_once_with("provider-test", provider_yaml, overwrite=True)
    reload_bindings.assert_called_once_with("provider-test")
    assert invalidate.call_count == 2
    assert invalidate.call_args_list[0].args[0]["defaults"]["base_url"] == "https://old.example.com"
    assert invalidate.call_args_list[1].args[0] == provider_yaml
    invalidate_agents.assert_called_once_with("api_draft_confirmed")


@pytest.mark.asyncio
async def test_confirm_empty_upsert_requires_existing_provider():
    request = route_module.ConfirmAPIToolDraftRequest(
        mode="upsert",
        draft=APIToolDraft(
            provider=ProviderDraft(
                id="missing-provider",
                defaults={"base_url": "https://api.example.com"},
            ),
            tools=[],
        ),
    )

    with (
        patch.object(route_module, "validate_api_tool_draft", return_value=[]),
        patch.object(tool_loader, "find_api_provider_dir", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await route_module.confirm_api_tool_draft_route(request, _admin=object())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_confirm_upsert_deletes_stale_tools_and_tolerates_invalid_previous_yaml(
    tmp_path: Path,
):
    provider_dir = tmp_path / "provider-test"
    provider_dir.mkdir()
    provider_path = provider_dir / "_provider.yaml"
    provider_path.write_text("defaults: [", encoding="utf-8")
    current_path = provider_dir / "current.yaml"
    stale_one = provider_dir / "stale-one.yaml"
    stale_two = provider_dir / "stale-two.yaml"
    tool = SimpleNamespace(
        info=ToolInfo(name="current", description="current tool", provider="provider-test")
    )
    request = route_module.ConfirmAPIToolDraftRequest(
        mode="upsert",
        delete_missing_tools=True,
        draft=APIToolDraft(
            provider=ProviderDraft(
                id="provider-test",
                defaults={"base_url": "https://api.example.com"},
            ),
            tools=[
                ToolDraft(
                    name="current",
                    description="current tool",
                    handler={"type": "http", "url": "https://api.example.com/current"},
                )
            ],
        ),
    )

    with (
        patch.object(route_module, "validate_api_tool_draft", return_value=[]),
        patch.object(route_module, "compile_provider_yaml", return_value={"id": "provider-test"}),
        patch.object(route_module, "compile_tool_yaml", return_value={"name": "current"}),
        patch.object(
            route_module,
            "_create_and_register_yaml_tool",
            AsyncMock(return_value=(tool, current_path)),
        ),
        patch.object(route_module, "_reload_api_provider_tool_bindings") as reload_bindings,
        patch.object(route_module, "_invalidate_agent_cache_after_tool_change") as invalidate_agents,
        patch.object(tool_loader, "find_api_provider_dir", return_value=provider_dir),
        patch.object(tool_loader, "find_yaml_tool", return_value=None),
        patch.object(tool_loader, "find_api_provider_tool", return_value=None),
        patch.object(tool_loader, "create_api_provider_yaml", return_value=provider_path),
        patch.object(tool_loader, "list_api_provider_tools", return_value=[stale_one, stale_two]),
        patch.object(tool_loader, "delete_api_provider_tool", side_effect=[True, False]) as delete_tool,
        patch("smartclaw.tool.smart_auth.invalidate_smart_auth_provider") as invalidate_auth,
    ):
        response = await route_module.confirm_api_tool_draft_route(request, _admin=object())

    assert response.tool_paths == [str(current_path)]
    assert delete_tool.call_count == 2
    reload_bindings.assert_called_once_with("provider-test")
    invalidate_agents.assert_any_call("api_draft_missing_tools_deleted")
    invalidate_agents.assert_any_call("api_draft_confirmed")
    invalidate_auth.assert_called_once_with({"id": "provider-test"})


@pytest.mark.asyncio
async def test_confirm_create_does_not_reload_existing_provider_bindings(tmp_path: Path):
    provider_path = tmp_path / "provider-new" / "_provider.yaml"
    tool_path = tmp_path / "provider-new" / "created.yaml"
    tool = SimpleNamespace(
        info=ToolInfo(name="created", description="created tool", provider="provider-new")
    )
    request = route_module.ConfirmAPIToolDraftRequest(
        mode="create",
        draft=APIToolDraft(
            provider=ProviderDraft(
                id="provider-new",
                defaults={"base_url": "https://api.example.com"},
            ),
            tools=[
                ToolDraft(
                    name="created",
                    description="created tool",
                    handler={"type": "http", "url": "https://api.example.com/created"},
                )
            ],
        ),
    )

    with (
        patch.object(route_module, "validate_api_tool_draft", return_value=[]),
        patch.object(route_module, "compile_provider_yaml", return_value={"id": "provider-new"}),
        patch.object(route_module, "compile_tool_yaml", return_value={"name": "created"}),
        patch.object(
            route_module,
            "_create_and_register_yaml_tool",
            AsyncMock(return_value=(tool, tool_path)),
        ),
        patch.object(route_module, "_reload_api_provider_tool_bindings") as reload_bindings,
        patch.object(route_module, "_invalidate_agent_cache_after_tool_change"),
        patch.object(tool_loader, "find_api_provider_dir", return_value=None),
        patch.object(tool_loader, "find_yaml_tool", return_value=None),
        patch.object(tool_loader, "find_api_provider_tool", return_value=None),
        patch.object(tool_loader, "create_api_provider_yaml", return_value=provider_path),
        patch("smartclaw.tool.smart_auth.invalidate_smart_auth_provider"),
    ):
        response = await route_module.confirm_api_tool_draft_route(request, _admin=object())

    assert response.provider_path == str(provider_path)
    reload_bindings.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_upsert_ignores_non_mapping_previous_provider_yaml(tmp_path: Path):
    provider_dir = tmp_path / "provider-test"
    provider_dir.mkdir()
    provider_path = provider_dir / "_provider.yaml"
    provider_path.write_text("- legacy\n", encoding="utf-8")
    provider_yaml = {"id": "provider-test"}
    request = route_module.ConfirmAPIToolDraftRequest(
        mode="upsert",
        draft=APIToolDraft(
            provider=ProviderDraft(
                id="provider-test",
                defaults={"base_url": "https://api.example.com"},
            ),
            tools=[],
        ),
    )

    with (
        patch.object(route_module, "validate_api_tool_draft", return_value=[]),
        patch.object(route_module, "compile_provider_yaml", return_value=provider_yaml),
        patch.object(route_module, "_reload_api_provider_tool_bindings"),
        patch.object(route_module, "_invalidate_agent_cache_after_tool_change"),
        patch.object(tool_loader, "find_api_provider_dir", return_value=provider_dir),
        patch.object(tool_loader, "create_api_provider_yaml", return_value=provider_path),
        patch("smartclaw.tool.smart_auth.invalidate_smart_auth_provider") as invalidate,
    ):
        await route_module.confirm_api_tool_draft_route(request, _admin=object())

    invalidate.assert_called_once_with(provider_yaml)


def test_merge_provider_defaults_covers_smart_and_bearer_auth_branches():
    smart_raw = {"handler": {"type": "http", "url": "https://api.example.com"}}
    bearer_raw = {"handler": {"type": "http", "url": "https://api.example.com"}}

    with (
        patch.object(tool_loader, "_inject_provider_auth_ext") as inject_ext,
        patch.object(tool_loader, "_inject_provider_auth") as inject_auth,
    ):
        tool_loader._merge_provider_defaults(
            smart_raw,
            {"defaults": {}, "authType": "smart", "authExt": [{"key": "X-Test"}]},
        )
        tool_loader._merge_provider_defaults(
            bearer_raw,
            {"defaults": {}, "authType": "bearerToken", "auth": {"secret": "token"}},
        )

    inject_ext.assert_called_once_with(smart_raw["handler"], [{"key": "X-Test"}])
    inject_auth.assert_called_once_with(bearer_raw["handler"], {"secret": "token"})


@pytest.mark.asyncio
async def test_http_handler_returns_smart_auth_error(monkeypatch):
    binding = SmartAuthBinding.__new__(SmartAuthBinding)
    handler = tool_loader._build_http_handler(
        {
            "type": "http",
            "method": "GET",
            "url": "https://api.example.com",
            "_smart_auth_binding": binding,
        }
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("aiohttp.ClientSession", return_value=session),
        patch.object(
            SmartAuthBinding,
            "get_token",
            AsyncMock(side_effect=SmartAuthError("smart auth unavailable")),
        ),
    ):
        result = await handler(
            route_module.ToolContext(session_id="session-1", message_id="message-1")
        )

    assert result.success is False
    assert result.error == "smart auth unavailable"
