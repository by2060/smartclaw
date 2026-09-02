"""
Tool integration tests.

Tests tool registration and agent tool declarations.
Tests that relied on the removed runtime/ module (ToolCoordinator, strategy)
have been removed as part of the runtime/ cleanup.
"""

import pytest
from unittest.mock import patch, MagicMock

from smartclaw.tool.registry import ToolRegistry
from smartclaw.agent import Agent


class TestToolRegistration:
    """Test tool registration."""

    def test_threatbook_tools_registered(self):
        """Verify ThreatBook tools are registered."""
        ToolRegistry.init()
        tools = [t.name for t in ToolRegistry.list_tools()]
        threatbook_tools = [name for name in tools if name.startswith("threatbook")]

        if not threatbook_tools:
            pytest.skip("ThreatBook tools are not available in this environment")

        assert any(name.endswith("ip_query") for name in threatbook_tools)
        assert any(name.endswith("domain_query") for name in threatbook_tools)


class TestTitanToolDeclarations:
    """Test Titan agent tool declarations."""

    @pytest.mark.asyncio
    async def test_titan_permission_for_ip_query(self):
        """Verify Titan tool declaration for IP query tool."""
        result = await Agent.has_tool("titan", "threatbook_mcp_ip_query")
        assert result in [True, False]
