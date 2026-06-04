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

        assert "智能体名称来自本提示词中的 **智能体** / **委派表** 部分" in prompt
        assert "`tool_search` 只搜索工具" in prompt
        assert "不要用它验证智能体是否存在" in prompt
