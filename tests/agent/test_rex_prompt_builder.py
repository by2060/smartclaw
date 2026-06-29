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

    def test_skill_matching_rule_requires_authorized_visible_skill(self):
        prompt = build_dynamic_rex_prompt(
            available_agents=[],
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "current session's authorized skill list" in prompt
        assert "do not infer skill access from installed files" in prompt
        assert "not available in the current session" in prompt

    def test_capability_self_intro_is_in_rex_prompt(self):
        prompt = build_dynamic_rex_prompt(
            available_agents=[],
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "Capability Self-Introduction" in prompt
        assert "current session's authorized scope" in prompt
        assert "Do not present broad SecOps positioning" in prompt
        assert "actually exposed by the current prompt" in prompt
        assert "I will not assume access to tools" in prompt
        assert "threat intelligence and IOC analysis" not in prompt
        assert "alert and log triage" not in prompt

    def test_identity_question_uses_titan_business_assistant_reply(self):
        prompt = build_dynamic_rex_prompt(
            available_agents=[],
            available_tools=[],
            available_skills=[],
            available_categories=[],
            available_workflows=[],
        )

        assert "When the user asks who you are" in prompt
        assert '"你是谁"' in prompt
        assert "我是Titan，泰岳安全公司的安全业务AI助手，它关注于安全运营、身份安全、资产安全、安全管理方向的安全业务。" in prompt
        assert "有什么安全业务需求吗？" in prompt

    def test_session_capability_intro_summarizes_kb_without_ids(self):
        prompt = _session_prompt(
            user_context={
                "knowledgeBaseIds": ["kb_secret_alpha"],
                "allowedSubagents": [],
            }
        )

        assert "authorized knowledge-base retrieval is available" in prompt
        assert "kb_secret_alpha" not in prompt
        assert "authorized specialist delegation is not indicated" in prompt

    def test_session_capability_intro_summarizes_authorized_specialists(self):
        prompt = _session_prompt(
            user_context={
                "knowledgeBaseIds": [],
                "allowedSubagents": ["beta"],
            }
        )

        assert "knowledge-base retrieval authorization is not indicated" in prompt
        assert "authorized specialist delegation is available" in prompt
        assert "authorized specialist agents" in prompt

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
