from __future__ import annotations

from flocks.agent.agent import (
    AgentInfo,
    AgentPromptMetadata,
    AvailableAgent,
    AvailableSkill,
    AvailableTool,
    DelegationTrigger,
)
from flocks.agent.agents.rex.prompt_builder import (
    build_dynamic_rex_prompt,
    build_session_dynamic_rex_prompt,
)


def _available_agent(name: str) -> AvailableAgent:
    return AvailableAgent(
        name=name,
        description=f"{name} executor.",
        metadata=AgentPromptMetadata(
            category="testing",
            cost="CHEAP",
            triggers=[DelegationTrigger(domain=name, trigger=f"use {name}")],
        ),
    )


def _agent_info(name: str, mode: str = "subagent", agent_type: str | None = None) -> AgentInfo:
    return AgentInfo(
        name=name,
        mode=mode,
        agent_type=agent_type,
        delegatable=True,
        description=f"{name} executor.",
    )


def _session_prompt(
    *,
    category: str = "user",
    user_context: dict | None = None,
    rex_tools: list[str] | None = None,
    rex_skills: list[str] | None = None,
) -> str:
    agents = [_available_agent("alpha"), _available_agent("beta"), _available_agent("gamma")]
    agent_lookup = {
        "alpha": _agent_info("alpha", mode="L1"),
        "beta": _agent_info("beta", mode="subagent"),
        "gamma": _agent_info("gamma", mode="L1"),
    }
    return build_session_dynamic_rex_prompt(
        rex_agent_info=AgentInfo(
            name="rex",
            mode="primary",
            tools=rex_tools or ["read", "tool_search"],
            skills=rex_skills,
        ),
        agent_lookup=agent_lookup,
        session_category=category,
        user_context=user_context or {},
        available_agents=agents,
        available_tools=[
            AvailableTool(name="read", category="file"),
            AvailableTool(name="bash", category="terminal"),
            AvailableTool(name="tool_search", category="system"),
        ],
        available_skills=[
            AvailableSkill(name="allowed-skill", description="Allowed skill.", location="project"),
            AvailableSkill(name="blocked-skill", description="Blocked skill.", location="project"),
        ],
        available_categories=[],
        available_workflows=[],
    )


class TestRexPromptBuilder:
    def test_agent_lookup_guidance_does_not_route_through_tool_search(self):
        prompt = build_dynamic_rex_prompt(
            available_agents=[],
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "Agent names come from the **Agents** / **Delegation Table** sections" in prompt
        assert "`tool_search` searches tools only" in prompt
        assert "do NOT use it to verify whether an agent exists" in prompt

    def test_cross_turn_risk_chain_is_in_rex_prompt(self):
        prompt = build_dynamic_rex_prompt(
            available_agents=[],
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "Cross-Turn Risk Chain Identification" in prompt
        assert "process IDs/PIDs" in prompt
        assert "Closing or blocking a port" in prompt
        assert "treat it as a capability gap" in prompt

    def test_dify_kb_protocol_is_in_rex_prompt(self):
        prompt = build_dynamic_rex_prompt(
            available_agents=[],
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "Dify Knowledge Base Retrieval Protocol" in prompt
        assert "`dify_kb_search`" in prompt
        assert "do not bypass with Bash" in prompt
        assert "Do NOT use direct Dify retrieval to bypass required skills" in prompt
        assert "Clearly state when an answer is based on Dify" in prompt

    def test_session_prompt_missing_allowed_subagents_keeps_all_subagents(self):
        prompt = _session_prompt(user_context={})

        assert "`alpha`" in prompt
        assert "`beta`" in prompt
        assert "`gamma`" in prompt

    def test_session_prompt_empty_allowed_subagents_hides_all_subagents(self):
        prompt = _session_prompt(user_context={"allowedSubagents": []})

        assert "`alpha`" not in prompt
        assert "`beta`" not in prompt
        assert "`gamma`" not in prompt

    def test_session_prompt_allowed_subagents_filters_by_name(self):
        prompt = _session_prompt(user_context={"allowedSubagents": ["beta"]})

        assert "`alpha`" not in prompt
        assert "`beta`" in prompt
        assert "`gamma`" not in prompt

    def test_session_prompt_workflow_keeps_only_l1_from_allowed_names(self):
        prompt = _session_prompt(
            category="workflow",
            user_context={"allowedSubagents": ["alpha", "beta", "gamma"]},
        )

        assert "`alpha`" in prompt
        assert "`beta`" not in prompt
        assert "`gamma`" in prompt

    def test_session_prompt_workflow_accepts_top_level_agent_type_l1(self):
        agents = [_available_agent("alpha"), _available_agent("beta")]
        agent_lookup = {
            "alpha": _agent_info("alpha", mode="subagent", agent_type="L1"),
            "beta": _agent_info("beta", mode="subagent"),
        }
        prompt = build_session_dynamic_rex_prompt(
            rex_agent_info=AgentInfo(
                name="rex",
                mode="primary",
                tools=["read"],
                skills=[],
            ),
            agent_lookup=agent_lookup,
            session_category="workflow",
            user_context={"allowedSubagents": ["alpha", "beta"]},
            available_agents=agents,
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "`alpha`" in prompt
        assert "`beta`" not in prompt

    def test_session_prompt_non_workflow_uses_rex_yaml_tools_strictly(self):
        prompt = _session_prompt(category="user", rex_tools=["read", "tool_search"])

        assert "`read`" in prompt
        assert "`tool_search`" in prompt
        assert "`bash`" not in prompt

    def test_session_prompt_workflow_uses_all_tools(self):
        prompt = _session_prompt(category="workflow", rex_tools=["read"])

        assert "`read`" in prompt
        assert "`tool_search`" in prompt
        assert "`bash`" in prompt

    def test_session_prompt_non_workflow_uses_rex_yaml_skills(self):
        prompt = _session_prompt(category="user", rex_skills=["allowed-skill"])

        assert "`allowed-skill`" in prompt
        assert "`blocked-skill`" not in prompt

    def test_session_prompt_workflow_uses_all_skills(self):
        prompt = _session_prompt(category="workflow", rex_skills=[])

        assert "`allowed-skill`" in prompt
        assert "`blocked-skill`" in prompt
