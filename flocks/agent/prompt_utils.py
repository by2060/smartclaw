"""
Prompt builder utilities for dynamic agent prompts.

These functions construct sections of the delegation-aware system prompts used
by Rex, Hephaestus, and similar orchestrator agents.  Each function takes typed
context objects from :mod:`flocks.agent.agent` and returns a Markdown string.

Previously located at flocks.agent.prompts.builder.dynamic.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from flocks.agent.agent import AvailableAgent, AvailableCategory, AvailableSkill, AvailableTool, AvailableWorkflow


# ---------------------------------------------------------------------------
# Tool categorisation helper
# ---------------------------------------------------------------------------

def categorize_tools(tool_names: List[str]) -> List[AvailableTool]:
    """Build AvailableTool list using real ToolRegistry categories.

    Falls back to name-based heuristics when ToolRegistry is unavailable
    (e.g. during unit tests that don't initialise the registry).
    """
    try:
        from flocks.tool.registry import ToolRegistry
        ToolRegistry.init()
        tools: List[AvailableTool] = []
        for name in tool_names:
            tool_entry = ToolRegistry.get(name)
            category = tool_entry.info.category.value if tool_entry else "system"
            tools.append(AvailableTool(name=name, category=category))
        return tools
    except Exception:
        # Fallback: minimal heuristic so prompt-building never crashes
        return [AvailableTool(name=name, category="system") for name in tool_names]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

# Human-readable labels for ToolCategory values shown in the prompt
# NOTE: Keep these in English - they are internal developer-facing labels
# that match ToolCategory enum values and are consumed by LLMs
_CATEGORY_LABELS: Dict[str, str] = {
    "file":     "File",
    "code":     "Code / Shell",
    "search":   "Search",
    "browser":  "Browser",
    "terminal": "Terminal",
    "system":   "System / Agent",
    "custom":   "Custom / Plugin",
}

# Display order for categories in the prompt
_CATEGORY_ORDER = ["file", "code", "search", "browser", "terminal", "system", "custom"]


def _format_tools_for_prompt(tools: List[AvailableTool]) -> str:
    """Group tools by ToolCategory and render all of them.

    Returns a compact multi-line block where every registered tool is visible,
    grouped under a human-readable category label.
    """
    # Group by category, preserving insertion order within each group
    groups: Dict[str, List[str]] = {}
    for t in tools:
        groups.setdefault(t.category, []).append(t.name)

    if not groups:
        return ""

    lines: List[str] = []
    # Emit ordered categories first, then any remaining ones
    seen: set = set()
    for cat in _CATEGORY_ORDER:
        if cat in groups:
            label = _CATEGORY_LABELS.get(cat, cat.capitalize())
            names = ", ".join(f"`{n}`" for n in groups[cat])
            lines.append(f"**{label}**: {names}")
            seen.add(cat)
    for cat, names_list in groups.items():
        if cat not in seen:
            label = _CATEGORY_LABELS.get(cat, cat.capitalize())
            names = ", ".join(f"`{n}`" for n in names_list)
            lines.append(f"**{label}**: {names}")

    return "\n".join(lines)


def _display_description(item: object) -> str:
    description_cn = getattr(item, "description_cn", None)
    if description_cn:
        return description_cn
    return getattr(item, "description", "") or ""


def _first_sentence(description: str) -> str:
    return description.split(".")[0] or description


def build_key_triggers_section(
    agents: List[AvailableAgent],
    _skills: Optional[List[AvailableSkill]] = None,
) -> str:
    key_triggers = [f"- {a.metadata.key_trigger}" for a in agents if a.metadata.key_trigger]
    if not key_triggers:
        return ""
    return (
        "### 关键触发词（分类前必查）：\n\n"
        + "\n".join(key_triggers)
        + '\n- **"看看" + "创建 PR"** → 不仅是研究。预期完整实现周期。'
    )


def build_tool_selection_table(
    agents: List[AvailableAgent],
    tools: Optional[List[AvailableTool]] = None,
    _skills: Optional[List[AvailableSkill]] = None,
) -> str:
    tools = tools or []
    rows: List[str] = ["### 工具与智能体选择："]

    if tools:
        tools_block = _format_tools_for_prompt(tools)
        if tools_block:
            rows += [
                "",
                "**可用工具**：",
                tools_block,
            ]

    cost_order = {"FREE": 0, "CHEAP": 1, "EXPENSIVE": 2}
    sorted_agents = [a for a in agents if a.metadata.category != "utility"]
    sorted_agents.sort(key=lambda a: cost_order.get(a.metadata.cost, 99))

    if sorted_agents:
        rows += [
            "",
            "**智能体**（任务复杂或专业时委派）：",
            "",
            "| 智能体 | 成本 | 使用场景 |",
            "|--------|------|----------|",
        ]
        for agent in sorted_agents:
            short_desc = _first_sentence(_display_description(agent))
            rows.append(f"| `{agent.name}` | {agent.metadata.cost} | {short_desc} |")

    rows.append("")
    rows.append("**默认流程**：explore/librarian（后台）+ 工具 → oracle（如需）")
    return "\n".join(rows)


def build_explore_section(agents: List[AvailableAgent]) -> str:
    explore_agent = next((a for a in agents if a.name == "explore"), None)
    if not explore_agent:
        return ""
    use_when = explore_agent.metadata.use_when or []
    avoid_when = explore_agent.metadata.avoid_when or []
    left = [f"| {w} |  |" for w in avoid_when]
    right = [f"|  | {w} |" for w in use_when]
    return (
        "### Explore 智能体 = 上下文 Grep\n\n"
        "作为**同级工具**使用，非降级方案。大胆调用。\n\n"
        "| 直接使用工具 | 使用 Explore 智能体 |\n"
        "|--------------|---------------------|\n"
        + "\n".join(left + right)
    )


def build_librarian_section(agents: List[AvailableAgent]) -> str:
    librarian_agent = next((a for a in agents if a.name == "librarian"), None)
    if not librarian_agent:
        return ""
    use_when = librarian_agent.metadata.use_when or []
    triggers = "\n".join([f'- "{w}"' for w in use_when])
    return (
        "### Librarian 智能体 = 参考 Grep\n\n"
        "搜索**外部参考**（文档、OSS、Web）。涉及陌生库时主动调用。\n\n"
        "| 上下文 Grep（内部） | 参考 Grep（外部） |\n"
        "|---------------------|-------------------|\n"
        "| 搜索我们的代码库 | 搜索外部资源 |\n"
        "| 在本仓库找模式 | 在其他仓库找示例 |\n"
        "| 我们的代码怎么工作？ | 这个库怎么工作？ |\n"
        "| 项目特定逻辑 | 官方 API 文档 |\n"
        "| | 库最佳实践与特性 |\n"
        "| | OSS 实现示例 |\n\n"
        "**触发短语**（立即调用 librarian）：\n"
        + triggers
    )


def build_delegation_table(agents: List[AvailableAgent]) -> str:
    rows: List[str] = [
        "### 委派表：",
        "",
        "| 领域 | 委派给 | 触发条件 |",
        "|------|--------|----------|",
    ]
    for agent in agents:
        for trigger in agent.metadata.triggers:
            rows.append(f"| {trigger.domain} | `{agent.name}` | {trigger.trigger} |")
    return "\n".join(rows)


def build_category_skills_delegation_guide(
    categories: List[AvailableCategory],
    skills: List[AvailableSkill],
) -> str:
    if not categories and not skills:
        return ""

    category_rows = [f"| `{c.name}` | {c.description or c.name} |" for c in categories]
    skill_rows = [
        f"| `{s.name}` | {_first_sentence(_display_description(s))} |"
        for s in skills
    ]

    return (
        "### 分类 + 技能委派系统\n\n"
        "**delegate_task() 结合分类和技能实现最优任务执行。**\n\n"
        "#### 可用分类（领域优化模型）\n\n"
        "每个分类配置一个针对该领域优化的模型。阅读描述了解何时使用。\n\n"
        "| 分类 | 领域 / 最佳用途 |\n"
        "|------|----------------|\n"
        + "\n".join(category_rows)
        + "\n\n#### 可用技能（领域专业注入）\n\n"
        "技能将专业指令注入子智能体。阅读描述了解每个技能的适用场景。\n\n"
        "| 技能 | 专业领域 |\n"
        "|------|----------|\n"
        + "\n".join(skill_rows)
        + "\n\n---\n\n"
        "### 强制：分类 + 技能选择协议\n\n"
        "**步骤 1：选择分类**\n"
        "- 阅读每个分类的描述\n"
        "- 将任务需求与分类领域匹配\n"
        "- 选择领域最匹配任务的分类\n\n"
        "**步骤 2：评估所有技能**\n"
        "对上述每个技能，问自己：\n"
        '> "这个技能的专业领域与我的任务有重叠吗？"\n\n'
        "- 是 → 包含在 `load_skills=[...]` 中\n"
        "- 否 → 必须说明原因（见下）\n\n"
        "**步骤 3：说明省略原因**\n\n"
        "如果选择不包含可能相关的技能，必须提供：\n\n"
        "```\n"
        '技能评估 "[skill-name]":\n'
        "- 技能领域：[技能描述所说的]\n"
        "- 任务领域：[你的任务关于什么]\n"
        "- 决定：省略\n"
        "- 原因：[具体解释为何领域不重叠]\n"
        "```\n\n"
        "**为何必须说明原因：**\n"
        "- 强制你真正阅读技能描述\n"
        "- 防止懒惰地省略有用的技能\n"
        "- 子智能体无状态 —— 它们只知道你告诉的内容\n"
        "- 漏掉相关技能 = 次优输出\n\n"
        "---\n\n"
        "### 委派模式\n\n"
        "```typescript\n"
        "delegate_task(\n"
        '  category="[selected-category]",\n'
        '  load_skills=["skill-1", "skill-2"],  // 包含所有相关技能\n'
        '  prompt="..."\n'
        ")\n"
        "```\n\n"
        "**反模式（将产生差结果）：**\n"
        "```typescript\n"
        'delegate_task(category="...", load_skills=[], run_in_background=false, prompt="...")  // 空 load_skills 且无说明\n'
        "```"
    )


def build_oracle_section(agents: List[AvailableAgent]) -> str:
    oracle_agent = next((a for a in agents if a.name == "oracle"), None)
    if not oracle_agent:
        return ""
    use_when = oracle_agent.metadata.use_when or []
    avoid_when = oracle_agent.metadata.avoid_when or []

    return (
        "<Oracle_Usage>\n"
        "## Oracle —— 只读高智商顾问\n\n"
        "Oracle 是只读、昂贵、高质量推理模型，用于调试和架构。仅作咨询。\n\n"
        "### 何时咨询：\n\n"
        "| 触发条件 | 操作 |\n"
        "|----------|------|\n"
        + "\n".join([f"| {w} | 先 Oracle，再实现 |" for w in use_when])
        + "\n\n### 何时不咨询：\n\n"
        + "\n".join([f"- {w}" for w in avoid_when])
        + "\n\n### 使用模式：\n"
        '调用前简短声明"为 [原因] 咨询 Oracle"。\n\n'
        "**例外**：这是唯一需要先声明再行动的情况。其他工作直接开始，无需状态更新。\n"
        "</Oracle_Usage>"
    )


def build_hard_blocks_section() -> str:
    blocks = [
        "| 类型错误抑制（`as any`、`@ts-ignore`） | 永不 |",
        "| 未经明确请求提交 | 永不 |",
        "| 猜测未读代码 | 永不 |",
        "| 失败后留下破损代码 | 永不 |",
    ]
    return (
        "## 硬性约束（永不违反）\n\n"
        "| 约束 | 无例外 |\n"
        "|------|--------|\n"
        + "\n".join(blocks)
    )


def build_anti_patterns_section() -> str:
    patterns = [
        "| **类型安全** | `as any`、`@ts-ignore`、`@ts-expect-error` |",
        "| **错误处理** | 空 catch 块 `catch(e) {}` |",
        "| **测试** | 删除失败测试以'通过' |",
        "| **搜索** | 为单行拼写错误或明显语法错误调用智能体 |",
        "| **调试** | 散弹式调试、随机改动 |",
    ]
    return (
        "## 反模式（阻塞性违规）\n\n"
        "| 类别 | 禁止 |\n"
        "|------|------|\n"
        + "\n".join(patterns)
    )


def build_ultrawork_section(
    agents: List[AvailableAgent],
    categories: List[AvailableCategory],
    skills: List[AvailableSkill],
) -> str:
    lines: List[str] = []

    if categories:
        lines.append("**分类**（用于实现任务）：")
        for cat in categories:
            short_desc = cat.description or cat.name
            lines.append(f"- `{cat.name}`: {short_desc}")
        lines.append("")

    if skills:
        lines.append("**技能**（与分类结合使用 —— 评估所有相关性）：")
        for skill in skills:
            short_desc = _first_sentence(_display_description(skill))
            lines.append(f"- `{skill.name}`: {short_desc}")
        lines.append("")

    if agents:
        ultrawork_agent_priority = ["explore", "librarian", "plan", "oracle"]
        sorted_agents = list(agents)
        sorted_agents.sort(
            key=lambda a: ultrawork_agent_priority.index(a.name)
            if a.name in ultrawork_agent_priority
            else 999
        )
        lines.append("**智能体**（用于专业咨询/探索）：")
        for agent in sorted_agents:
            short_desc = _first_sentence(_display_description(agent))
            suffix = "（可多次）" if agent.name in ("explore", "librarian") else ""
            lines.append(f"- `{agent.name}{suffix}`: {short_desc}")

    return "\n".join(lines)


def build_workflows_section(workflows: List[AvailableWorkflow]) -> str:
    """Render the available workflows section for injection into system prompts.

    Mirrors the pattern used by build_category_skills_delegation_guide() for
    skills, so agents know which workflows exist before calling run_workflow.
    """
    if not workflows:
        return ""

    project_wfs = [w for w in workflows if w.source == "project"]
    global_wfs = [w for w in workflows if w.source != "project"]

    rows: List[str] = [
        "### 可用工作流",
        "",
        "| 工作流 | 描述 | 路径 | 范围 |",
        "|--------|------|------|------|",
    ]
    for w in project_wfs:
        short_desc = w.description.split("\n")[0] if w.description else ""
        rows.append(f"| `{w.name}` | {short_desc} | `{w.path}` | 项目 |")
    for w in global_wfs:
        short_desc = w.description.split("\n")[0] if w.description else ""
        rows.append(f"| `{w.name}` | {short_desc} | `{w.path}` | 全局 |")

    rows += [
        "",
        '**用法**：`run_workflow(workflow="<path>", inputs={...})`',
    ]
    return "\n".join(rows)
