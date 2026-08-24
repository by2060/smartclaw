"""
Tool search / discovery helper.

Lets the model search available tools and immediately add the returned matches
to the current session's callable tool set.
"""

from __future__ import annotations

from typing import Optional

from smartclaw.agent.controls import agent_allowed_tools, titan_session_uses_full_tool_catalog
from smartclaw.tool.catalog import (
    get_always_load_tool_names,
    normalize_tool_search_query,
    search_tool_catalog,
)
from smartclaw.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from smartclaw.session.callable_state import add_session_callable_tools


DESCRIPTION = """Search available tools by task intent, keyword, category, or exact names.

Use this tool when you need to discover a tool that is not already exposed in
the current turn. Search by user goal, capability, or keyword. Matching tools
returned here are added to the current session callable tool set immediately.
If you already know the needed tool names, prefer one exact batch query such as
`select:websearch,webfetch,skill` instead of multiple separate searches."""

DESCRIPTION_CN = """按任务意图、关键字、类别或精确名称搜索可用工具。

当需要发现当前轮次尚未暴露的工具时使用此工具。可以按用户目标、能力或关键字搜索。返回的匹配工具会立即加入当前会话的可调用工具集合。
如果已经知道需要的工具名称，优先使用一次精确批量查询，例如 `select:websearch,webfetch,skill`，而不是多次单独搜索。"""


async def _searchable_tool_names(ctx: ToolContext) -> Optional[set[str]]:
    if await titan_session_uses_full_tool_catalog(
        ctx.session_id,
        getattr(ctx, "agent", None),
        getattr(ctx, "extra", None),
    ):
        return None
    agent_name = getattr(ctx, "agent", None)
    declared = set(await agent_allowed_tools(agent_name))
    declared.update(get_always_load_tool_names())
    return {name for name in declared if name}

@ToolRegistry.register_function(
    name="tool_search",
    description=DESCRIPTION,
    description_cn=DESCRIPTION_CN,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            description=(
                "Search query describing the capability or task intent. "
                "Use select:tool_a,tool_b to expose multiple known tools in one call."
            ),
            required=False,
        ),
        ToolParameter(
            name="category",
            type=ParameterType.STRING,
            description="Optional category filter such as file, search, code, terminal, system, browser, custom",
            required=False,
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Maximum number of matching tools to return",
            required=False,
            default=8,
        ),
    ],
)
async def tool_search(
    ctx: ToolContext,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 8,
) -> ToolResult:
    limit = max(1, min(limit or 8, 20))
    searchable_tool_names = await _searchable_tool_names(ctx)
    matches, matched_tags = search_tool_catalog(
        query,
        category=category,
        limit=limit,
        tool_names=searchable_tool_names,
    )
    normalized_query = normalize_tool_search_query(query or "")
    callable_candidates = [match["name"] for match in matches]
    callable_tools = await add_session_callable_tools(
        ctx.session_id,
        callable_candidates,
        agent_name=getattr(ctx, "agent", None),
        extra=getattr(ctx, "extra", None),
    )
    if ctx.event_publish_callback:
        await ctx.event_publish_callback("runtime.tool_discovery", {
            "sessionID": ctx.session_id,
            "query": query or "",
            "normalizedQuery": normalized_query,
            "category": category,
            "returnedToolCount": len(matches),
            "callableToolCount": len(callable_tools),
            "callableToolNames": sorted(callable_candidates),
            "matchedTags": matched_tags,
        })

    return ToolResult(
        success=True,
        output={
            "query": query or "",
            "normalizedQuery": normalized_query,
            "category": category,
            "count": len(matches),
            "matchedTags": matched_tags,
            "callableToolNames": sorted(callable_candidates),
            "callableToolCount": len(callable_tools),
            # Legacy compatibility keys.
            "discoveredToolNames": sorted(callable_candidates),
            "discoveredToolCount": len(callable_tools),
            "matches": matches,
        },
    )
