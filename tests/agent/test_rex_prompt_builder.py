from __future__ import annotations

from flocks.agent.agents.rex.prompt_builder import build_dynamic_rex_prompt


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
