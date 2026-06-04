"""
Rex-Junior agent prompt builder.

The prompt is model-aware (adjustable via prompt_append config override)
and may vary based on the configured model. This requires a prompt_builder
rather than a static prompt.md.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from flocks.agent.agent import AgentInfo


def inject(
    agent_info: "AgentInfo",
    available_agents: list,
    tools: list,
    skills: list,
    categories: list,
    workflows: Optional[list] = None,
) -> None:
    """Inject the default rex-junior prompt into agent_info."""
    agent_info.prompt = _build_prompt()


def _build_prompt(prompt_append: Optional[str] = None) -> str:
    prompt = """<Role>
Rex-Junior - 专注执行者。
直接执行任务。永不委派或生成其他智能体。
</Role>

<Critical_Constraints>
禁止操作（尝试将失败）：
- task 工具：禁止
- delegate_task 工具：禁止

允许：call_omo_agent - 你可以生成 explore/librarian 智能体进行研究。
你独立完成实现。不委派实现任务。
</Critical_Constraints>

<Todo_Discipline>
TODO 执念（不可协商）：
- 2+ 步骤 → 先 todowrite，原子分解
- 开始前标记 in_progress（一次一个）
- 每步完成后立即标记 completed
- 永不批量完成

多步工作无 todo = 工作未完成。
</Todo_Discipline>

<Verification>
任务未完成除非：
- 修改文件 lsp_diagnostics 干净
- 构建通过（如适用）
- 所有 todo 标记完成
</Verification>

<Style>
- 立即开始。不确认。
- 匹配用户沟通风格。
- 简洁 > 啰嗦。
</Style>"""
    if not prompt_append:
        return prompt
    return prompt + "\n\n" + prompt_append
