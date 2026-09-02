"""
Agent system tests

Tests for Agent definitions, permissions, prompts, and registry operations.
Reflects the current architecture: 13 built-in agents loaded from YAML folders,
no permission_compat helpers, compaction/title/summary live in session/prompts.py.
"""

import pytest
from smartclaw.agent import Agent, AgentInfo, AgentModel, PROMPT_COMPACTION, PROMPT_TITLE, PROMPT_SUMMARY
from smartclaw.session.prompt_strings import PROMPT_COMPACTION, PROMPT_TITLE, PROMPT_SUMMARY


# =============================================================================
# Agent Definition Tests
# =============================================================================

BUILTIN_AGENTS = [
    "titan", "hephaestus", "plan", "explore",
    "oracle", "librarian", "metis", "momus", "multimodal-looker",
    "self-enhance", "titan-junior", "host-forensics", "host-forensics-fast",
]


class TestAgentDefinitions:
    """Test that all built-in agents load correctly from YAML."""

    @pytest.mark.asyncio
    async def test_all_builtin_agents_exist(self):
        for name in BUILTIN_AGENTS:
            agent = await Agent.get(name)
            assert agent is not None, f"Agent '{name}' should exist"
            assert agent.name == name

    @pytest.mark.asyncio
    async def test_agent_count(self):
        agents = await Agent.list()
        assert len(agents) >= 12, f"Should have at least 12 agents, got {len(agents)}"

    @pytest.mark.asyncio
    async def test_no_legacy_agents(self):
        """general / compaction / title / summary are no longer registered agents."""
        for name in ["general", "compaction", "title", "summary"]:
            agent = await Agent.get(name)
            assert agent is None, f"Legacy agent '{name}' should not exist"


class TestPrimaryAgents:

    @pytest.mark.asyncio
    async def test_titan_agent(self):
        agent = await Agent.get("titan")
        assert agent is not None
        assert agent.mode == "primary"
        assert agent.native is True
        assert agent.hidden is False
        assert agent.delegatable is False
        assert "websearch" in (agent.tools or [])
        assert "webfetch" in (agent.tools or [])

    @pytest.mark.asyncio
    async def test_plan_agent(self):
        agent = await Agent.get("plan")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.native is True
        assert agent.hidden is True
        assert agent.delegatable is False


class TestSubagents:

    @pytest.mark.asyncio
    async def test_explore_agent(self):
        agent = await Agent.get("explore")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.native is True
        assert agent.hidden is False
        assert agent.delegatable is True
        assert agent.prompt is not None and len(agent.prompt) > 0

    @pytest.mark.asyncio
    async def test_hephaestus_agent(self):
        agent = await Agent.get("hephaestus")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.delegatable is True
        assert agent.hidden is False

    @pytest.mark.asyncio
    async def test_titan_junior_agent(self):
        agent = await Agent.get("titan-junior")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.delegatable is False

    @pytest.mark.asyncio
    async def test_self_enhance_agent(self):
        agent = await Agent.get("self-enhance")
        assert agent is not None
        assert agent.mode == "subagent"
        assert agent.hidden is True
        assert agent.delegatable is False

    @pytest.mark.asyncio
    async def test_security_agents(self):
        for name in ["host-forensics", "host-forensics-fast"]:
            agent = await Agent.get(name)
            assert agent is not None
            assert agent.mode == "subagent"
            assert agent.delegatable is True


# =============================================================================
# Agent Listing Tests
# =============================================================================

class TestAgentListing:

    @pytest.mark.asyncio
    async def test_list_visible(self):
        visible = await Agent.list_visible()
        names = [a.name for a in visible]
        assert "titan" in names
        assert "explore" in names
        assert "hephaestus" in names
        # plan is hidden
        assert "plan" not in names

    @pytest.mark.asyncio
    async def test_list_hidden(self):
        hidden = await Agent.list_hidden()
        names = [a.name for a in hidden]
        assert "plan" in names

    @pytest.mark.asyncio
    async def test_list_subagents(self):
        subagents = await Agent.list_subagents()
        names = [a.name for a in subagents]
        assert "explore" in names
        assert "hephaestus" in names
        assert "oracle" in names
        # titan is primary, not subagent
        assert "titan" not in names
        # hidden agents excluded
        assert "plan" not in names

    @pytest.mark.asyncio
    async def test_list_primary(self):
        primary = await Agent.list_primary()
        names = [a.name for a in primary]
        assert "titan" in names
        assert "explore" not in names

    @pytest.mark.asyncio
    async def test_is_hidden(self):
        assert await Agent.is_hidden("plan") is True
        assert await Agent.is_hidden("explore") is False
        assert await Agent.is_hidden("titan") is False
        assert await Agent.is_hidden("nonexistent") is False

    @pytest.mark.asyncio
    async def test_is_delegatable(self):
        async def delegatable(name: str) -> bool:
            agent = await Agent.get(name)
            return bool(agent.delegatable) if agent else False

        assert await delegatable("titan") is False
        assert await delegatable("plan") is False
        assert await delegatable("titan-junior") is False
        assert await delegatable("explore") is True
        assert await delegatable("hephaestus") is True
        assert await delegatable("oracle") is True

    @pytest.mark.asyncio
    async def test_list_names(self):
        names = await Agent.list_names()
        for name in BUILTIN_AGENTS:
            assert name in names


# =============================================================================
# Agent Permission Tests
# =============================================================================

class TestAgentPermissions:

    @pytest.mark.asyncio
    async def test_explore_tools(self):
        """explore agent only allows declared read/search tools."""
        assert await Agent.has_tool("explore", "grep") is True
        assert await Agent.has_tool("explore", "glob") is True
        assert await Agent.has_tool("explore", "list") is True
        assert await Agent.has_tool("explore", "read") is True
        assert await Agent.has_tool("explore", "websearch") is True
        # Always-load tools remain available to every agent.
        assert await Agent.has_tool("explore", "write") is True
        assert await Agent.has_tool("explore", "edit") is False
        assert await Agent.has_tool("explore", "bash") is True

    @pytest.mark.asyncio
    async def test_nonexistent_agent_has_no_tool(self):
        assert await Agent.has_tool("nonexistent", "read") is False


# =============================================================================
# Session Prompt Constants Tests
# =============================================================================

class TestSessionPrompts:
    """Session management prompts live in session/prompts.py, not in agent registry."""

    def test_prompt_compaction_content(self):
        assert PROMPT_COMPACTION is not None
        assert len(PROMPT_COMPACTION) > 0
        assert "summariz" in PROMPT_COMPACTION.lower()

    def test_prompt_title_content(self):
        assert PROMPT_TITLE is not None
        assert len(PROMPT_TITLE) > 0
        assert "title" in PROMPT_TITLE.lower()

    def test_prompt_summary_content(self):
        assert PROMPT_SUMMARY is not None
        assert len(PROMPT_SUMMARY) > 0


# =============================================================================
# Agent Registration Tests
# =============================================================================

class TestAgentRegistration:

    @pytest.mark.asyncio
    async def test_register_custom_agent(self):
        custom = AgentInfo(
            name="custom_test",
            description="A test agent",
            mode="subagent",
            native=False,
        )
        Agent.register("custom_test", custom)
        try:
            agents = await Agent._load_agents()
            retrieved = agents.get("custom_test")
            assert retrieved is not None
            assert retrieved.name == "custom_test"
            assert retrieved.native is False
        finally:
            Agent.unregister("custom_test")

    @pytest.mark.asyncio
    async def test_unregister_custom_agent(self):
        custom = AgentInfo(name="temp_agent", mode="subagent", native=False)
        Agent.register("temp_agent", custom)
        agents = await Agent._load_agents()
        assert agents.get("temp_agent") is not None

        result = Agent.unregister("temp_agent")
        assert result is True
        agents = await Agent._load_agents()
        assert agents.get("temp_agent") is None

    @pytest.mark.asyncio
    async def test_cannot_unregister_native_agent(self):
        result = Agent.unregister("plan")
        assert result is False
        assert await Agent.get("plan") is not None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_agent(self):
        result = Agent.unregister("nonexistent_agent")
        assert result is False


# =============================================================================
# AgentModel Tests
# =============================================================================

class TestAgentModel:

    def test_create_agent_model(self):
        model = AgentModel(model_id="gpt-4", provider_id="openai")
        assert model.model_id == "gpt-4"
        assert model.provider_id == "openai"

    @pytest.mark.asyncio
    async def test_builtin_agents_no_custom_model(self):
        assert await Agent.get_model_config("plan") is None
        assert await Agent.get_model_config("explore") is None
        assert await Agent.get_model_config("nonexistent") is None

    @pytest.mark.asyncio
    async def test_agent_with_custom_model(self):
        from smartclaw.agent.agent import AgentModel
        custom = AgentInfo(
            name="model_test",
            mode="subagent",
            native=False,
            model=AgentModel(model_id="claude-3", provider_id="anthropic"),
        )
        Agent.register("model_test", custom)
        try:
            agents = await Agent._load_agents()
            agent = agents.get("model_test")
            assert agent is not None
            assert agent.model is not None
            assert agent.model.model_id == "claude-3"
        finally:
            Agent.unregister("model_test")


# =============================================================================
# AgentInfo Model Tests
# =============================================================================

class TestAgentInfo:

    def test_agent_info_defaults(self):
        info = AgentInfo(name="test")
        assert info.name == "test"
        assert info.description is None
        assert info.mode == "all"
        assert info.native is False
        assert info.hidden is False
        assert info.temperature is None
        assert info.top_p is None
        assert info.color is None
        assert info.model is None
        assert info.prompt is None
        assert info.steps is None
        assert info.permission == []

    def test_agent_info_with_values(self):
        info = AgentInfo(
            name="custom",
            description="Custom agent",
            mode="subagent",
            native=False,
            hidden=True,
            temperature=0.5,
            top_p=0.9,
            color="#FF0000",
            steps=10,
        )
        assert info.temperature == 0.5
        assert info.top_p == 0.9
        assert info.color == "#FF0000"
        assert info.steps == 10


# =============================================================================
# Default Agent Tests
# =============================================================================

class TestDefaultAgent:

    @pytest.mark.asyncio
    async def test_default_agent_is_titan(self):
        default = await Agent.default_agent()
        assert default == "titan"

    @pytest.mark.asyncio
    async def test_list_sorted_with_default_first(self):
        agents = await Agent.list()
        default = await Agent.default_agent()
        assert agents[0].name == default
