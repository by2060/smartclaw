"""
call_omo_agent tool - spawn explore/librarian subagents (Oh-My-Flocks parity).
"""

from typing import Optional

from flocks.tool.registry import (
    ToolRegistry,
    ToolCategory,
    ToolParameter,
    ParameterType,
    ToolResult,
    ToolContext,
)
from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop
from flocks.task.background import get_background_manager, LaunchInput
from flocks.agent.controls import agent_allowed_subagents, agent_allows_subagent
from flocks.utils.log import Log

log = Log.create(service="tool.call_omo_agent")


ALLOWED_AGENTS = ["explore", "librarian"]


@ToolRegistry.register_function(
    name="call_omo_agent",
    description=(
        "Spawn explore/librarian agent. "
        "run_in_background defaults to false (sync). Set true for async with task_id. "
        "Pass session_id to continue a previous agent with full context."
    ),
    description_cn=(
        "启动 explore/librarian agent。"
        "run_in_background 默认为 false（同步）；设为 true 时异步返回 task_id。"
        "传入 session_id 可继续之前的 agent，并保留完整上下文。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="A short (3-5 words) description of the task",
            required=True,
        ),
        ToolParameter(
            name="prompt",
            type=ParameterType.STRING,
            description="The task for the agent to perform",
            required=True,
        ),
        ToolParameter(
            name="subagent_type",
            type=ParameterType.STRING,
            description="The type of specialized agent to use (explore or librarian)",
            required=True,
        ),
        ToolParameter(
            name="run_in_background",
            type=ParameterType.BOOLEAN,
            description="Optional. true=async (returns task_id), false=sync (waits). Defaults to false.",
            required=False,
        ),
        ToolParameter(
            name="session_id",
            type=ParameterType.STRING,
            description="Existing Task session to continue",
            required=False,
        ),
    ],
)
async def call_omo_agent_tool(
    ctx: ToolContext,
    description: str,
    prompt: str,
    subagent_type: str,
    run_in_background: Optional[bool] = False,
    session_id: Optional[str] = None,
) -> ToolResult:
    if not subagent_type:
        return ToolResult(success=False, error="subagent_type is required")
    normalized = subagent_type.lower()
    if normalized not in ALLOWED_AGENTS:
        return ToolResult(
            success=False,
            error=f'Invalid agent type "{subagent_type}". Only {", ".join(ALLOWED_AGENTS)} are allowed.',
        )
    if not await agent_allows_subagent(ctx.agent, normalized):
        allowed = await agent_allowed_subagents(ctx.agent)
        allowed_text = ", ".join(allowed) or "none"
        return ToolResult(
            success=False,
            error=(
                f'Agent "{ctx.agent}" is not allowed to delegate to "{normalized}". '
                f"Allowed sub_agents: {allowed_text}"
            ),
        )
    if run_in_background is None:
        run_in_background = False

    await ctx.ask(
        permission="call_omo_agent",
        patterns=[normalized],
        always=["*"],
        metadata={"description": description, "subagent_type": normalized},
    )

    if run_in_background:
        if session_id:
            return ToolResult(
                success=False,
                error="session_id is not supported in background mode. Use run_in_background=false to continue.",
            )
        manager = get_background_manager()
        task = await manager.launch(
            LaunchInput(
                description=description,
                prompt=prompt,
                agent=normalized,
                parent_session_id=ctx.session_id,
                parent_message_id=ctx.message_id,
                parent_agent=ctx.agent,
            )
        )
        ctx.metadata({"title": description, "metadata": {"sessionId": task.session_id}})
        output = (
            "Background agent task launched successfully.\n\n"
            f"Task ID: {task.id}\n"
            f"Session ID: {task.session_id}\n"
            f"Description: {task.description}\n"
            f"Agent: {task.agent} (subagent)\n"
            f"Status: {task.status}\n\n"
            f'Use `background_output` with task_id="{task.id}" to check progress.'
        )
        return ToolResult(success=True, output=output, title=description, metadata={"sessionId": task.session_id})

    # Sync path
    if session_id:
        session = await Session.get_by_id(session_id)
        if not session:
            return ToolResult(success=False, error=f"Session {session_id} not found")
        target_session_id = session.id
        target_session = session
    else:
        parent_session = await Session.get_by_id(ctx.session_id)
        if not parent_session:
            return ToolResult(success=False, error="Parent session not found")
        created = await Session.create(
            project_id=parent_session.project_id,
            directory=parent_session.directory,
            title=f"{description} (@{normalized} subagent)",
            parent_id=parent_session.id,
            permission=[{"permission": "question", "action": "deny", "pattern": "*"}],
            agent=normalized,
            metadata=parent_session.metadata if isinstance(parent_session.metadata, dict) else None,
            # 澄清选择答案记忆新增
            owner_user_id=parent_session.owner_user_id,
            owner_username=parent_session.owner_username,
        )
        target_session_id = created.id
        target_session = created

    target_session = await Session.inherit_gateway_trace(
        target_session,
        ctx.session_id,
        ctx.message_id,
    )

    await Message.create(
        session_id=target_session_id,
        role=MessageRole.USER,
        content=prompt,
        agent=normalized,
    )
    from flocks.session.session_loop import LoopCallbacks as _LoopCbs
    result = await SessionLoop.run(
        target_session_id,
        callbacks=_LoopCbs(event_publish_callback=ctx.event_publish_callback),
    )
    output_text = ""
    if result.last_message:
        output_text = await Message.get_text_content(result.last_message)
    ctx.metadata({"title": description, "metadata": {"sessionId": target_session_id}})
    output = (
        f"{output_text}\n\n<task_metadata>\n"
        f"session_id: {target_session_id}\n"
        "</task_metadata>"
    )
    return ToolResult(success=True, output=output, title=description, metadata={"sessionId": target_session_id})
