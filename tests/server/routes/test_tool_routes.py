from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.tool.registry import Tool, ToolCategory, ToolInfo, ToolRegistry, ToolResult


@contextmanager
def _temporary_tool(tool: Tool) -> Iterator[None]:
    ToolRegistry.init()
    existing = ToolRegistry._tools.get(tool.info.name)
    ToolRegistry.register(tool)
    try:
        yield
    finally:
        ToolRegistry._failure_state.pop(tool.info.name, None)
        if existing is not None:
            ToolRegistry._tools[tool.info.name] = existing
        else:
            ToolRegistry._tools.pop(tool.info.name, None)


class _FakeSessionUser:
    def __init__(self, role: str) -> None:
        self.role = role

    def to_auth_user(self) -> AuthUser:
        return AuthUser(
            id=f"usr_{self.role}",
            username=f"{self.role}-user",
            role=self.role,
            status="active",
            must_reset_password=False,
        )


def _patch_session_user(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    from flocks.server import auth as auth_module

    async def _has_users():
        return True

    async def _get_user_by_session_id(_session_id: str):
        return _FakeSessionUser(role)

    monkeypatch.setattr(auth_module.AuthService, "has_users", _has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", _get_user_by_session_id)


async def _create_session_and_message(title: str) -> tuple[str, str]:
    session = await Session.create(
        project_id="default",
        directory=str(Path.cwd()),
        title=title,
        agent="rex",
    )
    message = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content=f"{title} message",
        agent="rex",
    )
    return session.id, message.id


class TestToolRouteSecurity:
    @pytest.mark.asyncio
    async def test_viewer_cannot_create_plugin_tool(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        _patch_session_user(monkeypatch, "viewer")

        response = await client.post(
            "/api/tools",
            headers={"cookie": "flocks_session=viewer-session"},
            json={
                "name": "viewer_created_tool",
                "description": "should be rejected",
                "handler": {
                    "type": "http",
                    "method": "GET",
                    "url": "https://example.com",
                },
            },
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_update_plugin_tool(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        _patch_session_user(monkeypatch, "viewer")

        response = await client.put(
            "/api/tools/existing_tool",
            headers={"cookie": "flocks_session=viewer-session"},
            json={"description": "nope"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_delete_plugin_tool(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        _patch_session_user(monkeypatch, "viewer")

        response = await client.delete(
            "/api/tools/existing_tool",
            headers={"cookie": "flocks_session=viewer-session"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_execute_blocks_direct_bash_access(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/bash/execute",
            json={"params": {"command": "pwd"}},
        )

        assert response.status_code == 403
        assert "session-backed request" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_test_endpoint_blocks_direct_bash_access(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/bash/test",
            json={"params": {"command": "pwd"}},
        )

        assert response.status_code == 403
        assert "session-backed request" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_batch_blocks_direct_bash_access(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/batch",
            json={
                "calls": [
                    {
                        "name": "bash",
                        "params": {"command": "pwd"},
                    }
                ]
            },
        )

        assert response.status_code == 403
        assert "session-backed request" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_execute_rejects_missing_message_id_for_local_tools(self, client: AsyncClient):
        session_id, _ = await _create_session_and_message("missing-message-id")

        response = await client.post(
            "/api/tools/bash/execute",
            json={
                "params": {"command": "pwd"},
                "sessionID": session_id,
            },
        )

        assert response.status_code == 403
        assert "verified" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_execute_rejects_unknown_session_for_local_tools(self, client: AsyncClient):
        response = await client.post(
            "/api/tools/bash/execute",
            json={
                "params": {"command": "pwd"},
                "sessionID": "sess-missing",
                "messageID": "msg-missing",
            },
        )

        assert response.status_code == 404
        assert "Session not found" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_execute_allows_direct_api_tools(self, client: AsyncClient):
        async def handler(ctx, text: str) -> ToolResult:
            return ToolResult(
                success=True,
                output=f"{text}:{ctx.session_id}",
            )

        tool = Tool(
            info=ToolInfo(
                name="http_safe_api_tool",
                description="safe http api tool",
                category=ToolCategory.CUSTOM,
                source="api",
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_safe_api_tool/execute",
                json={"params": {"text": "pong"}},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["output"] == "pong:http-tool"

    @pytest.mark.asyncio
    async def test_execute_allows_direct_custom_tools(self, client: AsyncClient):
        async def handler(ctx, text: str) -> ToolResult:
            return ToolResult(
                success=True,
                output=f"{text}:{ctx.session_id}",
            )

        tool = Tool(
            info=ToolInfo(
                name="http_safe_custom_tool",
                description="safe http custom tool",
                category=ToolCategory.CUSTOM,
                source="custom",
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_safe_custom_tool/execute",
                json={"params": {"text": "hello"}},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["output"] == "hello:http-tool"

    @pytest.mark.asyncio
    async def test_http_tool_context_with_child_session_scopes_outputs_to_root(self):
        from flocks.server.routes import tool as tool_routes

        root = await Session.create(
            project_id="default",
            directory=str(Path.cwd()),
            title="root-output-session",
            agent="rex",
        )
        child = await Session.create(
            project_id="default",
            directory=str(Path.cwd()),
            title="child-output-session",
            parent_id=root.id,
            agent="rex",
        )
        message = await Message.create(
            session_id=child.id,
            role=MessageRole.USER,
            content="child message",
            agent="rex",
        )

        ctx = await tool_routes._build_http_tool_context(
            tool_name="http_child_output_session_tool",
            tool_info=ToolInfo(
                name="http_child_output_session_tool",
                description="reports output session context",
                category=ToolCategory.CUSTOM,
                source="custom",
            ),
            session_id=child.id,
            message_id=message.id,
            agent="rex",
        )

        assert ctx.session_id == child.id
        assert ctx.message_id == message.id
        assert ctx.extra["output_session_id"] == root.id
        assert ctx.extra["main_session_key"] == root.id

    @pytest.mark.asyncio
    async def test_execute_rejects_message_outside_session(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        permission_ask = AsyncMock(return_value=None)
        monkeypatch.setattr(tool_routes.PermissionNext, "ask", permission_ask)

        session_id, _ = await _create_session_and_message("owner-session")
        _, foreign_message_id = await _create_session_and_message("foreign-session")

        async def handler(ctx) -> ToolResult:
            await ctx.ask(
                permission="bash",
                patterns=["pwd"],
                always=["*"],
                metadata={"source": "test"},
            )
            return ToolResult(success=True, output="ok")

        tool = Tool(
            info=ToolInfo(
                name="http_session_message_mismatch_tool",
                description="session-message mismatch tool",
                category=ToolCategory.SYSTEM,
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_session_message_mismatch_tool/execute",
                json={
                    "params": {},
                    "sessionID": session_id,
                    "messageID": foreign_message_id,
                    "agent": "rex",
                },
            )

        assert response.status_code == 404
        assert "not found in session" in response.json()["message"]
        permission_ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_uses_permission_flow_when_session_context_is_present(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        permission_ask = AsyncMock(return_value=None)
        monkeypatch.setattr(tool_routes.PermissionNext, "ask", permission_ask)
        session_id, message_id = await _create_session_and_message("valid-session-context")

        async def handler(ctx) -> ToolResult:
            await ctx.ask(
                permission="bash",
                patterns=["pwd"],
                always=["*"],
                metadata={"source": "test"},
            )
            return ToolResult(success=True, output="ok")

        tool = Tool(
            info=ToolInfo(
                name="http_session_bound_tool",
                description="session-bound test tool",
                category=ToolCategory.SYSTEM,
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/http_session_bound_tool/execute",
                json={
                    "params": {},
                    "sessionID": session_id,
                    "messageID": message_id,
                    "agent": "rex",
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        permission_ask.assert_awaited_once()
        kwargs = permission_ask.await_args.kwargs
        assert kwargs["session_id"] == session_id
        assert kwargs["permission"] == "bash"
        assert kwargs["metadata"]["messageID"] == message_id
        assert kwargs["tool"] == {"name": "http_session_bound_tool"}

    @pytest.mark.asyncio
    async def test_batch_uses_actual_child_tool_name_for_permission_flow(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.server.routes import tool as tool_routes

        permission_ask = AsyncMock(return_value=None)
        monkeypatch.setattr(tool_routes.PermissionNext, "ask", permission_ask)
        session_id, message_id = await _create_session_and_message("valid-batch-session-context")

        async def handler(ctx) -> ToolResult:
            await ctx.ask(
                permission="bash",
                patterns=["pwd"],
                always=["*"],
                metadata={"source": "batch-test"},
            )
            return ToolResult(success=True, output="ok")

        tool = Tool(
            info=ToolInfo(
                name="http_batch_named_tool",
                description="batch named test tool",
                category=ToolCategory.SYSTEM,
            ),
            handler=handler,
        )

        with _temporary_tool(tool):
            response = await client.post(
                "/api/tools/batch",
                json={
                    "calls": [{"name": "http_batch_named_tool", "params": {}}],
                    "parallel": True,
                    "sessionID": session_id,
                    "messageID": message_id,
                    "agent": "rex",
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["success"] is True
        permission_ask.assert_awaited_once()
        kwargs = permission_ask.await_args.kwargs
        assert kwargs["session_id"] == session_id
        assert kwargs["metadata"]["messageID"] == message_id
        assert kwargs["tool"] == {"name": "http_batch_named_tool"}


class TestAPIToolDraftRoutes:
    @pytest.mark.asyncio
    async def test_generate_overrides_provider_references_with_provider_id(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.tool import api_tool_draft_llm
        from flocks.tool.api_tool_draft import APIToolDraft, NonAPIToolDraftResult, ProviderDraft, ToolDraft

        async def fake_generate_api_tool_draft(
            *,
            source_context: str,
            auth_hint=None,
            tool_name_prefix=None,
            model_id=None,
        ):
            assert "GET /users" in source_context
            assert auth_hint == {"type": "api_key", "location": "header"}
            assert tool_name_prefix is None
            assert model_id is None
            return APIToolDraft(
                is_api_related=True,
                provider=ProviderDraft(
                    id="smc_4a_sync",
                    name="SMC 4A Sync",
                    service_id="smc_4a_sync",
                    description="Generated provider",
                    defaults={"base_url": "https://api.example.com"},
                ),
                tools=[
                    ToolDraft(
                        name="draft_route_provider_override_sample",
                        provider="smc_4a_sync",
                        inputSchema={"type": "object", "properties": {}},
                        handler={"type": "http", "method": "GET", "url": "{base_url}/users"},
                    )
                ],
            )

        monkeypatch.setattr(api_tool_draft_llm, "generate_api_tool_draft", fake_generate_api_tool_draft)

        response = await client.post(
            "/api/tools/drafts",
            json={
                "sources": [{"type": "text", "source_type": "text", "content": "GET /users"}],
                "provider_id": "postman_api_draft_demo",
                "auth_hint": {"type": "api_key", "location": "header"},
            },
        )

        assert response.status_code == 200, response.text
        draft = response.json()["draft"]
        assert draft["provider"]["id"] == "postman_api_draft_demo"
        assert draft["is_api_related"] is True
        assert "is_api_related" not in response.json()
        assert draft["provider"]["service_id"] == "postman_api_draft_demo"
        assert "version" not in draft["provider"]
        assert draft["tools"][0]["provider"] == "postman_api_draft_demo"

    @pytest.mark.asyncio
    async def test_generate_returns_non_api_related_result_without_draft_validation(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from flocks.tool import api_tool_draft_llm
        from flocks.tool.api_tool_draft import NonAPIToolDraftResult

        async def fake_generate_api_tool_draft(
            *,
            source_context: str,
            auth_hint=None,
            tool_name_prefix=None,
            model_id=None,
        ):
            assert "meeting notes" in source_context
            return NonAPIToolDraftResult(
                is_api_related=False,
                irrelevant_reason="输入材料与 API 接口无关。",
            )

        monkeypatch.setattr(api_tool_draft_llm, "generate_api_tool_draft", fake_generate_api_tool_draft)

        response = await client.post(
            "/api/tools/drafts",
            json={"sources": [{"type": "text", "source_type": "text", "content": "meeting notes"}]},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "is_api_related" not in payload
        assert payload["draft"] == {
            "is_api_related": False,
            "irrelevant_reason": "输入材料与 API 接口无关。",
        }
        assert payload["issues"] == []

    @pytest.mark.asyncio
    async def test_confirm_writes_provider_yaml_and_registers_tool(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        import yaml
        from flocks.project.instance import Instance

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))

        tool_name = "draft_confirm_route_sample"
        provider_id = "draft_confirm_provider"
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Draft Confirm Provider",
                        "description": "Provider from draft confirm route",
                        "defaults": {
                            "base_url": "https://api.example.com",
                            "timeout": 30,
                            "category": "custom",
                        },
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Fetch a user",
                            "enabled": False,
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "description": "User id"},
                                },
                                "required": ["id"],
                            },
                            "handler": {
                                "type": "http",
                                "method": "GET",
                                "url": "{base_url}/users/{id}",
                                "response": {"extract": "data"},
                            },
                        }
                    ],
                }
            },
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["provider_path"].endswith("_provider.yaml")
        assert len(payload["tool_paths"]) == 1
        assert payload["tools"][0]["name"] == tool_name

        provider_path = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id / "_provider.yaml"
        tool_path = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id / f"{tool_name}.yaml"
        assert provider_path.exists()
        assert tool_path.exists()

        provider_yaml = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
        assert provider_yaml["service_id"] == provider_id
        assert "version" not in provider_yaml

        tool_yaml = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
        assert "response" not in tool_yaml
        assert tool_yaml["handler"]["response"] == {"extract": "data"}
        assert ToolRegistry.get(tool_name) is not None

        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

    @pytest.mark.asyncio
    async def test_confirm_rejects_invalid_error_mapping_without_writing_files(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        from flocks.project.instance import Instance

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))

        tool_name = "draft_confirm_bad_status_mapping"
        provider_id = "draft_confirm_bad_status_provider"
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Bad Status Provider",
                        "description": "Provider with invalid response mapping",
                        "defaults": {"base_url": "https://api.example.com"},
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Invalid response mapping",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {
                                "type": "http",
                                "method": "GET",
                                "url": "{base_url}/users",
                                "response": {"error_mapping": {"IllegalMac": "bad mac"}},
                            },
                        }
                    ],
                },
            },
        )

        assert response.status_code == 422, response.text
        provider_dir = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id
        assert not (provider_dir / "_provider.yaml").exists()
        assert not (provider_dir / f"{tool_name}.yaml").exists()
        assert ToolRegistry.get(tool_name) is None

    @pytest.mark.asyncio
    async def test_confirm_upsert_overwrites_provider_and_tool(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        import yaml
        from flocks.project.instance import Instance

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))

        tool_name = "draft_confirm_upsert_sample"
        provider_id = "draft_confirm_upsert_provider"
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Original Provider",
                        "description": "Original provider",
                        "defaults": {"base_url": "https://api.example.com", "timeout": 30},
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Original description",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/original"},
                        }
                    ],
                }
            },
        )
        assert response.status_code == 201, response.text

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "mode": "upsert",
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Updated Provider",
                        "description": "Updated provider",
                        "defaults": {"base_url": "https://api.updated.example.com", "timeout": 60},
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Updated description",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/updated"},
                        }
                    ],
                },
            },
        )
        assert response.status_code == 201, response.text

        provider_path = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id / "_provider.yaml"
        tool_path = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id / f"{tool_name}.yaml"
        provider_yaml = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
        tool_yaml = yaml.safe_load(tool_path.read_text(encoding="utf-8"))

        assert provider_yaml["name"] == "Updated Provider"
        assert provider_yaml["defaults"]["base_url"] == "https://api.updated.example.com"
        assert tool_yaml["description"] == "Updated description"
        assert tool_yaml["handler"]["url"] == "{base_url}/updated"
        assert ToolRegistry.get(tool_name).info.description == "Updated description"

        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

    @pytest.mark.asyncio
    async def test_confirm_upsert_rejects_cross_provider_tool_collision(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        from flocks.project.instance import Instance

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))

        tool_name = "draft_confirm_cross_provider_sample"
        original_provider_id = "draft_confirm_original_provider"
        other_provider_id = "draft_confirm_other_provider"
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": original_provider_id,
                        "name": "Original Provider",
                        "description": "Original provider",
                        "defaults": {"base_url": "https://api.example.com"},
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Original description",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/original"},
                        }
                    ],
                }
            },
        )
        assert response.status_code == 201, response.text

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "mode": "upsert",
                "draft": {
                    "provider": {
                        "id": other_provider_id,
                        "name": "Other Provider",
                        "description": "Other provider",
                        "defaults": {"base_url": "https://api.other.example.com"},
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Should not overwrite",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/other"},
                        }
                    ],
                },
            },
        )

        assert response.status_code == 409, response.text
        assert "outside provider" in response.json()["message"]
        assert not (
            project_dir / ".flocks" / "plugins" / "tools" / "api" / other_provider_id / "_provider.yaml"
        ).exists()

        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

    @pytest.mark.asyncio
    async def test_confirm_upsert_delete_missing_tools(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        from flocks.project.instance import Instance

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))

        provider_id = "draft_confirm_delete_missing_provider"
        kept_tool_name = "draft_confirm_kept_tool"
        removed_tool_name = "draft_confirm_removed_tool"
        for tool_name in (kept_tool_name, removed_tool_name):
            ToolRegistry._tools.pop(tool_name, None)
            if tool_name in ToolRegistry._plugin_tool_names:
                ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Delete Missing Provider",
                        "description": "Provider",
                        "defaults": {"base_url": "https://api.example.com"},
                    },
                    "tools": [
                        {
                            "name": kept_tool_name,
                            "description": "Kept tool",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/kept"},
                        },
                        {
                            "name": removed_tool_name,
                            "description": "Removed tool",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/removed"},
                        },
                    ],
                }
            },
        )
        assert response.status_code == 201, response.text

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "mode": "upsert",
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Delete Missing Provider",
                        "description": "Provider",
                        "defaults": {"base_url": "https://api.example.com"},
                    },
                    "tools": [
                        {
                            "name": kept_tool_name,
                            "description": "Kept tool updated",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/kept-updated"},
                        }
                    ],
                },
            },
        )
        assert response.status_code == 201, response.text

        provider_dir = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id
        assert (provider_dir / f"{kept_tool_name}.yaml").exists()
        assert not (provider_dir / f"{removed_tool_name}.yaml").exists()
        assert ToolRegistry.get(removed_tool_name) is None

        for tool_name in (kept_tool_name, removed_tool_name):
            ToolRegistry._tools.pop(tool_name, None)
            if tool_name in ToolRegistry._plugin_tool_names:
                ToolRegistry._plugin_tool_names.remove(tool_name)

    @pytest.mark.asyncio
    async def test_delete_draft_provider_removes_directory_and_config_keeps_secret_by_default(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        from flocks.config.config import Config
        from flocks.config.config_writer import ConfigWriter
        from flocks.project.instance import Instance
        from flocks.security import get_secret_manager
        from flocks.security import secrets as secrets_module

        project_dir = tmp_path / "project"
        config_dir = tmp_path / "config"
        project_dir.mkdir()
        config_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))
        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
        Config._global_config = None
        Config._cached_config = None
        secrets_module._secret_manager = None

        provider_id = "draft_delete_provider"
        tool_name = "draft_delete_provider_tool"
        secret_id = "draft_delete_provider_secret"
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Delete Provider",
                        "description": "Provider",
                        "defaults": {"base_url": "https://api.example.com"},
                        "credential_fields": [
                            {
                                "key": "api_key",
                                "label": "API Key",
                                "storage": "secret",
                                "secret_id": secret_id,
                                "config_key": "apiKey",
                            }
                        ],
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Tool",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/tool"},
                        }
                    ],
                }
            },
        )
        assert response.status_code == 201, response.text

        ConfigWriter.set_api_service(provider_id, {"apiKey": f"{{secret:{secret_id}}}"})
        get_secret_manager().set(secret_id, "secret-value")

        response = await client.delete(f"/api/tools/drafts/{provider_id}")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["removed_provider"] is True
        assert payload["removed_config_keys"] == [provider_id]
        assert payload["deleted_secrets"] == []
        assert tool_name in payload["removed_tools"]
        assert not (project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id).exists()
        assert ConfigWriter.get_api_service_raw(provider_id) is None
        assert get_secret_manager().get(secret_id) == "secret-value"
        assert ToolRegistry.get(tool_name) is None

        secrets_module._secret_manager = None
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

    @pytest.mark.asyncio
    async def test_delete_draft_provider_accepts_service_id_and_deletes_secret_when_requested(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        import yaml
        from flocks.config.config import Config
        from flocks.config.config_writer import ConfigWriter
        from flocks.project.instance import Instance
        from flocks.security import get_secret_manager
        from flocks.security import secrets as secrets_module

        project_dir = tmp_path / "project"
        config_dir = tmp_path / "config"
        project_dir.mkdir()
        config_dir.mkdir()
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: str(project_dir)))
        monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
        Config._global_config = None
        Config._cached_config = None
        secrets_module._secret_manager = None

        provider_id = "draft_delete_service_provider"
        service_id = "draft_delete_service_id"
        tool_name = "draft_delete_service_tool"
        secret_id = "draft_delete_service_secret"
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)

        response = await client.post(
            "/api/tools/drafts/confirm",
            json={
                "draft": {
                    "provider": {
                        "id": provider_id,
                        "name": "Delete Service Provider",
                        "description": "Provider",
                        "defaults": {"base_url": "https://api.example.com"},
                        "credential_fields": [
                            {
                                "key": "api_key",
                                "label": "API Key",
                                "storage": "secret",
                                "secret_id": secret_id,
                                "config_key": "apiKey",
                            }
                        ],
                    },
                    "tools": [
                        {
                            "name": tool_name,
                            "description": "Tool",
                            "inputSchema": {"type": "object", "properties": {}},
                            "handler": {"type": "http", "method": "GET", "url": "{base_url}/tool"},
                        }
                    ],
                }
            },
        )
        assert response.status_code == 201, response.text

        provider_path = project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id / "_provider.yaml"
        provider_yaml = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
        provider_yaml["service_id"] = service_id
        provider_path.write_text(yaml.dump(provider_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
        ConfigWriter.set_api_service(service_id, {"apiKey": f"{{secret:{secret_id}}}"})
        get_secret_manager().set(secret_id, "secret-value")

        response = await client.delete(f"/api/tools/drafts/{service_id}?delete_secrets=true")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["removed_provider"] is True
        assert set(payload["removed_config_keys"]) == {provider_id, service_id}
        assert payload["deleted_secrets"] == [secret_id]
        assert not (project_dir / ".flocks" / "plugins" / "tools" / "api" / provider_id).exists()
        assert ConfigWriter.get_api_service_raw(provider_id) is None
        assert ConfigWriter.get_api_service_raw(service_id) is None
        assert get_secret_manager().get(secret_id) is None
        assert ToolRegistry.get(tool_name) is None

        secrets_module._secret_manager = None
        ToolRegistry._tools.pop(tool_name, None)
        if tool_name in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.remove(tool_name)
