"""
Task Center Tools for Titan

Registers task management tools into ToolRegistry so Titan can
create, list, update, delete, and query tasks via natural language.
"""

import json
from typing import Optional

from smartclaw.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from smartclaw.utils.log import Log

log = Log.create(service="task.tools")


_TRUTHY_STRINGS = {"true", "1", "yes", "y", "on"}
_FALSY_STRINGS = {"false", "0", "no", "n", "off", ""}


def _coerce_legacy_bool(value: object, *, default: bool = False) -> bool:
    """Coerce values that may arrive as strings from legacy clients."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY_STRINGS:
            return True
        if normalized in _FALSY_STRINGS:
            return False
    return default


def _normalize_task_create_inputs(
    type_value: Optional[str],
    schedule_type: Optional[str],
    run_once: bool,
    run_at: Optional[str],
    cron: Optional[str],
    cron_description: Optional[str],
    timezone: str,
    schedule: Optional[str],
) -> tuple[Optional[str], bool, Optional[str], Optional[str], Optional[str], str]:
    """Accept common task_create aliases and infer scheduled tasks."""
    schedule_data: dict[str, object] = {}
    if schedule:
        normalized_schedule = schedule.strip()
        if normalized_schedule.startswith("{"):
            try:
                parsed_schedule = json.loads(normalized_schedule)
            except json.JSONDecodeError:
                parsed_schedule = None
            if isinstance(parsed_schedule, dict):
                schedule_data = parsed_schedule
            else:
                cron = cron or schedule
        else:
            cron = cron or schedule

    if schedule_data:
        type_value = type_value or schedule_data.get("type") or schedule_data.get("task_type")
        schedule_type = (
            schedule_type
            or schedule_data.get("schedule_type")
            or schedule_data.get("scheduleType")
        )
        cron = cron or schedule_data.get("cron")
        run_at = run_at or schedule_data.get("run_at") or schedule_data.get("runAt")
        cron_description = (
            cron_description
            or schedule_data.get("cron_description")
            or schedule_data.get("cronDescription")
        )
        timezone = str(schedule_data.get("timezone") or timezone)
        if schedule_data.get("run_once") is not None:
            run_once = _coerce_legacy_bool(schedule_data.get("run_once"), default=run_once)
        elif schedule_data.get("runOnce") is not None:
            run_once = _coerce_legacy_bool(schedule_data.get("runOnce"), default=run_once)

    if type_value:
        return type_value, run_once, run_at, cron, cron_description, timezone
    if not schedule_type:
        # When the caller signalled a scheduled intent (run_once / run_at / cron),
        # keep it scheduled so build_schedule can surface proper validation errors
        # instead of silently falling back to an immediate queued execution.
        if cron or run_at or run_once:
            return "scheduled", run_once, run_at, cron, cron_description, timezone
        return "queued", run_once, run_at, cron, cron_description, timezone

    normalized = schedule_type.strip().lower()
    if normalized == "queued":
        return "queued", run_once, run_at, cron, cron_description, timezone
    if normalized in {"scheduled", "cron", "recurring", "repeat"}:
        return "scheduled", False, run_at, cron, cron_description, timezone
    if normalized in {"once", "one_time", "one-time", "run_once"}:
        return "scheduled", True, run_at, cron, cron_description, timezone
    return schedule_type, run_once, run_at, cron, cron_description, timezone


# ======================================================================
# task_create
# ======================================================================

@ToolRegistry.register_function(
    name="task_create",
    description=(
        "Create a new task (queued, one-time scheduled, or recurring scheduled). "
        "Only call this when the user explicitly asks for deferred/delayed execution "
        "(e.g. 'add to queue', 'do it later', 'schedule daily at 8am', 'run once tonight at 6pm'). "
        "Do NOT create a task for immediate requests.\n\n"
        "IMPORTANT — Clarify schedule type before creating:\n"
        "When a user mentions a specific time (e.g. '今晚6点', '明天下午3点') WITHOUT clearly "
        "indicating recurrence, you MUST ask to confirm intent before calling this tool. "
        "Ask: '请问这个任务是只执行一次，还是每天在这个时间重复执行？'\n"
        "Recurrence signals (use type=scheduled, run_once=false): "
        "'每天', '每周', '每月', '每小时', '定期', '每个工作日', '每30分钟'\n"
        "One-time signals (use type=scheduled, run_once=true): "
        "'一次', '这次', specific date like '明天下午3点', '下周五晚上', '2024-01-15 18:00'\n"
        "Queue-only (use type=queued, no schedule): "
        "'等会', '稍后', '待会', '有空时', '不着急'\n\n"
        "IMPORTANT — IM session resolution before creating:\n"
        "If the task involves sending a message to an IM platform (企业微信/WeCom、飞书/Feishu、钉钉/DingTalk), "
        "you MUST resolve the target session_id and channel_type BEFORE calling this tool "
        "(follow the IM Session Resolution for task_create protocol in your system prompt). "
        "Embed both into description and user_prompt. "
        "If the user cannot provide a session_id, do NOT create the task."
    ),
    description_cn=(
        "创建新任务（队列任务、一次性定时任务或周期性定时任务）。"
        "仅当用户明确要求延迟/稍后执行时调用，例如“加入队列”“稍后做”“每天 8 点执行”“今晚 6 点执行一次”。"
        "不要为即时请求创建任务。\n\n"
        "重要：创建前需要澄清调度类型。"
        "当用户提到具体时间但没有明确表示重复执行时，必须先确认是只执行一次还是按该时间重复执行。\n"
        "周期性信号使用 type=scheduled, run_once=false；一次性信号使用 type=scheduled, run_once=true；仅排队任务使用 type=queued 且不带 schedule。\n\n"
        "重要：创建涉及 IM 平台（企业微信/WeCom、飞书/Feishu、钉钉/DingTalk）的消息任务前，"
        "必须先解析目标 session_id 和 channel_type，并写入 description 与 user_prompt。"
        "如果用户无法提供 session_id，不要创建任务。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="title",
            type=ParameterType.STRING,
            description="Short title for the task",
            required=True,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description=(
                "Detailed task description. "
                "If the task involves sending a message to an IM platform (WeCom/Feishu/DingTalk), "
                "MUST include the resolved channel_type and session_id here. "
                "Example: '每天早上8点向飞书群发送日报 channel_type=feishu session_id=ses_abc123'"
            ),
            required=True,
        ),
        ToolParameter(
            name="type",
            type=ParameterType.STRING,
            description=(
                "Task type: "
                "'queued' = deferred but no schedule (run when queue is free); "
                "'scheduled' = triggered at a specific time (one-time or recurring, "
                "controlled by run_once)"
            ),
            required=False,
            enum=["queued", "scheduled"],
        ),
        ToolParameter(
            name="schedule_type",
            type=ParameterType.STRING,
            description=(
                "Legacy alias for task schedule kind. "
                "Accepted values include 'queued', 'scheduled', 'cron', "
                "'once', 'one_time'. Prefer using type + run_once."
            ),
            required=False,
        ),
        ToolParameter(
            name="schedule",
            type=ParameterType.STRING,
            description=(
                "Legacy schedule alias. Can be a cron string like '*/5 * * * *' "
                "or a JSON string containing cron/runAt/runOnce/timezone."
            ),
            required=False,
        ),
        ToolParameter(
            name="run_once",
            type=ParameterType.BOOLEAN,
            description=(
                "Only for type=scheduled. "
                "True = run exactly once at the specified time then disable. "
                "False (default) = recurring, repeats per cron expression."
            ),
            required=False,
            default=False,
        ),
        ToolParameter(
            name="priority",
            type=ParameterType.STRING,
            description="Priority level",
            required=False,
            default="normal",
            enum=["urgent", "high", "normal", "low"],
        ),
        ToolParameter(
            name="run_at",
            type=ParameterType.STRING,
            description=(
                "ISO 8601 datetime string for one-time execution (used when run_once=True). "
                "e.g. '2024-01-15T18:00:00+08:00'. "
                "If only a time like '今晚18:00' is given, compute the full datetime. "
                "Required when run_once=True and no cron is provided."
            ),
            required=False,
        ),
        ToolParameter(
            name="cron",
            type=ParameterType.STRING,
            description=(
                "Cron expression for recurring tasks (run_once=False), "
                "e.g. '0 8 * * *' for daily 8am. "
                "Can also be used with run_once=True to fire at the next cron occurrence."
            ),
            required=False,
        ),
        ToolParameter(
            name="cron_description",
            type=ParameterType.STRING,
            description=(
                "Human-readable Chinese description of the schedule. "
                "Always provide this when creating a scheduled task, e.g. "
                "'每天早上8点', '每周一09:00', '今晚18:00执行一次', '2025-01-15 下午3点执行一次'. "
                "This is shown directly in the UI."
            ),
            required=False,
        ),
        ToolParameter(
            name="timezone",
            type=ParameterType.STRING,
            description="Timezone for scheduled tasks (default: Asia/Shanghai)",
            required=False,
            default="Asia/Shanghai",
        ),
        ToolParameter(
            name="user_prompt",
            type=ParameterType.STRING,
            description=(
                "The EXECUTION CONTENT ONLY — what the agent should actually do when this task runs. "
                "You MUST extract and restate only the action part from the user's message, "
                "discarding any scheduling/creation meta-instructions such as "
                "'帮我创建定时任务', '在XX点执行一次', '加到任务队列', '等会帮我' etc. "
                "Think of it as: what would you tell the agent to do if the user had said it directly? "
                "Example — user says: '创建个定时任务，在14:45执行一次：查询threatbook.cn的情报' "
                "→ user_prompt should be: '查询 threatbook.cn 的情报' "
                "Example — user says: '帮我加个任务，明天上午扫描一下内网资产' "
                "→ user_prompt should be: '扫描内网资产' "
                "CRITICAL — IM tasks: If the action involves sending a message to an IM platform "
                "(WeCom/Feishu/DingTalk), you MUST include the resolved channel_type and session_id "
                "in user_prompt. NEVER omit them — the task runs unattended and cannot ask the user. "
                "Example — user says: '每天8点发飞书消息给研发群' (session already resolved to ses_abc123) "
                "→ user_prompt should be: '向飞书(channel_type=feishu) session_id=ses_abc123 发送消息：<消息内容>' "
                "This text is displayed in the UI as '任务补充信息'."
            ),
            required=False,
        ),
        ToolParameter(
            name="enabled",
            type=ParameterType.BOOLEAN,
            description=(
                "Legacy compatibility field. False creates the task and then disables it. "
                "True keeps it active."
            ),
            required=False,
        ),
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description=(
                "Legacy compatibility field sometimes sent by models during task creation. "
                "Ignored by task_create."
            ),
            required=False,
        ),
    ],
)
async def task_create(
    ctx: ToolContext,
    title: str,
    description: str,
    type: Optional[str] = None,
    schedule_type: Optional[str] = None,
    schedule: Optional[str] = None,
    run_once: bool = False,
    priority: str = "normal",
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: str = "Asia/Shanghai",
    user_prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
    action: Optional[str] = None,
) -> ToolResult:
    from smartclaw.task.manager import TaskManager
    from smartclaw.task.models import (
        SchedulerMode,
        TaskPriority,
        TaskSource,
        TaskTrigger,
        build_schedule,
    )

    del action

    type, run_once, run_at, cron, cron_description, timezone = _normalize_task_create_inputs(
        type,
        schedule_type,
        run_once,
        run_at,
        cron,
        cron_description,
        timezone,
        schedule,
    )
    if type is None:
        return ToolResult(
            success=False,
            error="type or schedule_type is required",
        )

    task_priority = TaskPriority(priority)

    if type == "queued":
        mode = SchedulerMode.ONCE
        trigger = TaskTrigger(run_immediately=True)
    else:
        try:
            trigger = build_schedule(
                run_once=run_once,
                run_at=run_at,
                cron=cron,
                cron_description=cron_description,
                timezone=timezone,
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        mode = SchedulerMode.ONCE if run_once else SchedulerMode.CRON

    source = TaskSource(
        source_type="user_conversation",
        user_prompt=user_prompt,
    )

    scheduler = await TaskManager.create_scheduler(
        title=title,
        description=description,
        mode=mode,
        priority=task_priority,
        source=source,
        trigger=trigger,
    )
    if enabled is False:
        scheduler = await TaskManager.disable_scheduler(scheduler.id) or scheduler

    output_lines = [
        f"ID: {scheduler.id}",
        f"Title: {scheduler.title}",
        f"Mode: {scheduler.mode.value}",
        f"Status: {scheduler.status.value}",
        f"Priority: {scheduler.priority.value}",
    ]
    if scheduler.trigger.run_immediately:
        executions, _ = await TaskManager.list_scheduler_executions(
            scheduler.id,
            limit=1,
        )
        if executions:
            execution = executions[0]
            output_lines.append(f"Execution ID: {execution.id}")
            output_lines.append(f"Execution Status: {execution.status.value}")
    elif scheduler.trigger.run_at:
        output_lines.append(f"Run at: {scheduler.trigger.run_at.isoformat()}")
    elif scheduler.trigger.cron:
        output_lines.append(f"Cron: {scheduler.trigger.cron}")
        if scheduler.trigger.next_run:
            output_lines.append(f"Next run: {scheduler.trigger.next_run.isoformat()}")

    return ToolResult(
        success=True,
        output="\n".join(output_lines),
        title=f"Task created: {scheduler.title}",
    )


# ======================================================================
# task_list
# ======================================================================

_SCHEDULER_STATUSES = {"active", "disabled", "paused"}
_EXECUTION_STATUSES = {"pending", "queued", "running", "completed", "failed", "cancelled", "paused"}
_EXECUTION_TYPES = {"queued", "execution"}
_VALID_TYPES = {"scheduled"} | _EXECUTION_TYPES


@ToolRegistry.register_function(
    name="task_list",
    description=(
        "List tasks with optional filters.\n\n"
        "Routing rules (IMPORTANT - read before calling):\n"
        "- No parameters -> lists scheduled task definitions (schedulers).\n"
        "- status='active' -> lists active scheduled tasks.\n"
        "- status='disabled' -> lists disabled scheduled tasks.\n"
        "- legacy status='paused' is still accepted and mapped to disabled schedulers "
        "or cancelled executions depending on query target.\n"
        "- status='running' / 'completed' / 'failed' / 'pending' / 'queued' / 'cancelled' "
        "-> lists task executions with that status.\n"
        "- type='scheduled' -> forces listing schedulers.\n"
        "- type='execution' -> forces listing executions.\n"
        "- type='queued' -> legacy alias for type='execution'.\n\n"
        "Common scenarios:\n"
        "- 'How many scheduled tasks are there?' / 'List scheduled tasks' "
        "-> call with no parameters.\n"
        "- 'How many tasks are currently running?' -> call with status='running'.\n"
        "- 'Which scheduled tasks are disabled?' -> call with status='disabled'.\n"
        "- 'Show old paused tasks' -> call with status='paused'."
    ),
    description_cn=(
        "列出任务，并支持可选过滤。\n\n"
        "路由规则（调用前务必阅读）：\n"
        "- 不传参数：列出定时任务定义（schedulers）。\n"
        "- status='active'：列出活跃定时任务。\n"
        "- status='disabled'：列出已禁用定时任务。\n"
        "- 旧的 status='paused' 仍被接受，并按查询目标映射为禁用的 scheduler 或已取消的 execution。\n"
        "- status='running' / 'completed' / 'failed' / 'pending' / 'queued' / 'cancelled'：列出对应状态的任务执行记录。\n"
        "- type='scheduled'：强制列出 schedulers。\n"
        "- type='execution'：强制列出 executions。\n"
        "- type='queued'：type='execution' 的旧别名。\n\n"
        "常见场景：\n"
        "- “有多少定时任务？”/“列出定时任务”：不传参数调用。\n"
        "- “当前有多少任务正在运行？”：使用 status='running'。\n"
        "- “哪些定时任务被禁用了？”：使用 status='disabled'。\n"
        "- “显示旧的暂停任务”：使用 status='paused'。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="status",
            type=ParameterType.STRING,
            description=(
                "Filter by status. "
                "Scheduler statuses: 'active', 'disabled'. "
                "Execution statuses: 'pending', 'queued', 'running', 'completed', "
                "'failed', 'cancelled'. Legacy alias: 'paused'."
            ),
            required=False,
            enum=[
                "active", "disabled", "paused",
                "pending", "queued", "running", "completed", "failed", "cancelled",
            ],
        ),
        ToolParameter(
            name="type",
            type=ParameterType.STRING,
            description=(
                "Force query target: 'scheduled' = list schedulers (task definitions), "
                "'execution' = list task executions (task run history). "
                "'queued' is a legacy alias for 'execution'. "
                "If omitted, the target is inferred from status."
            ),
            required=False,
            enum=["queued", "execution", "scheduled"],
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Max results (default 10)",
            required=False,
            default=10,
        ),
    ],
)
async def task_list(
    ctx: ToolContext,
    status: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 10,
) -> ToolResult:
    from smartclaw.task.manager import TaskManager
    from smartclaw.task.models import SchedulerStatus, TaskStatus

    if type is not None and type not in _VALID_TYPES:
        return ToolResult(
            success=False,
            error=(
                f"Invalid type '{type}'. "
                f"Valid values: {', '.join(sorted(_VALID_TYPES))}."
            ),
        )

    if type == "scheduled":
        query_schedulers = True
    elif type in _EXECUTION_TYPES:
        query_schedulers = False
    elif status is None or status in _SCHEDULER_STATUSES:
        query_schedulers = True
    elif status in _EXECUTION_STATUSES:
        query_schedulers = False
    else:
        return ToolResult(
            success=False,
            error=(
                f"Invalid status '{status}'. "
                f"Scheduler statuses: {', '.join(sorted(_SCHEDULER_STATUSES))}. "
                f"Execution statuses: {', '.join(sorted(_EXECUTION_STATUSES))}. "
                "Use type='scheduled' for scheduler states or "
                "type='execution' for execution states."
            ),
        )

    if query_schedulers:
        if status is not None and status not in _SCHEDULER_STATUSES:
            return ToolResult(
                success=False,
                error=(
                    f"Invalid status '{status}' for type='scheduled'. "
                    f"Valid scheduler statuses: {', '.join(sorted(_SCHEDULER_STATUSES))}. "
                    "Use type='execution' to query execution statuses like "
                    f"'{status}'."
                ),
            )
        scheduler_status = None
        if status == "active":
            scheduler_status = SchedulerStatus.ACTIVE
        elif status in ("disabled", "paused"):
            scheduler_status = SchedulerStatus.DISABLED
        tasks, total = await TaskManager.list_schedulers(
            status=scheduler_status,
            scheduled_only=True,
            limit=limit,
        )
        label = "Scheduled tasks"
    else:
        try:
            mapped_status = "cancelled" if status == "paused" else status
            task_status = TaskStatus(mapped_status) if mapped_status else None
        except ValueError:
            return ToolResult(
                success=False,
                error=(
                    f"Invalid execution status '{status}'. "
                    f"Valid values: {', '.join(s.value for s in TaskStatus)}."
                ),
            )
        tasks, total = await TaskManager.list_executions(
            status=task_status,
            limit=limit,
        )
        label = "Task executions"

    lines = [f"{label} ({total} total, showing {len(tasks)}):"]
    for t in tasks:
        lines.append(_format_task_line(t))

    return ToolResult(success=True, output="\n".join(lines))


# ======================================================================
# task_status
# ======================================================================

@ToolRegistry.register_function(
    name="task_status",
    description="Get detailed status and result of a specific task",
    description_cn="获取指定任务的详细状态和结果。",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
    ],
)
async def task_status(ctx: ToolContext, task_id: str) -> ToolResult:
    from smartclaw.task.manager import TaskManager

    task = await TaskManager.get_execution(task_id)
    if task and task.delivery_status.value == "unread":
        await TaskManager.mark_notified(task_id)
    if task is None:
        task = await TaskManager.get_scheduler(task_id)
    if task is None:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=task.title,
    )


# ======================================================================
# task_update
# ======================================================================

@ToolRegistry.register_function(
    name="task_update",
    description=(
        "Update a task. By default action=update, which can modify scheduler "
        "fields like title, description, priority, cron, run_once, run_at, "
        "cron_description, timezone, and user_prompt. Supports enable/disable "
        "for scheduled tasks, and cancel/retry "
        "for execution tasks.\n\n"
        "IMPORTANT:\n"
        "- Pass update fields as top-level arguments. DO NOT wrap them inside "
        "a `fields` object or JSON string.\n"
        "- To stop a scheduled task, use action='disable', 'pause', or 'stop'. "
        "To resume it, use action='enable', 'resume', or 'start'.\n"
        "- When changing a schedule, also pass a human-readable `title` and "
        "`cron_description` that reflect the new schedule, otherwise the task "
        "title shown in the UI may remain the old wording.\n\n"
        "Good example for recurring schedule update:\n"
        "task_update(task_id='tsk_xxx', cron='*/10 * * * *', "
        "title='每10分钟执行关键词搜索摘要生成工作流', "
        "cron_description='每10分钟执行一次')\n"
        "Good example for stopping a scheduled task:\n"
        "task_update(task_id='tsk_xxx', action='disable')\n"
        "Bad example:\n"
        "task_update(task_id='tsk_xxx', fields='{\"cron\":\"*/10 * * * *\"}')"
    ),
    description_cn=(
        "更新任务。默认 action=update，可修改 scheduler 字段，例如 title、description、priority、cron、run_once、run_at、cron_description、timezone 和 user_prompt。"
        "支持对定时任务执行 enable/disable，也支持对执行任务执行 cancel/retry。\n\n"
        "重要：\n"
        "- 更新字段应作为顶层参数传入，不要包装在 `fields` 对象或 JSON 字符串中。\n"
        "- 停止定时任务请使用 action='disable'、'pause' 或 'stop'；恢复请使用 action='enable'、'resume' 或 'start'。\n"
        "- 修改调度时，也要传入反映新调度的人类可读 `title` 和 `cron_description`，否则 UI 中显示的任务标题可能仍是旧文案。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="Action to perform",
            required=False,
            default="update",
            enum=[
                "cancel", "retry", "update",
                "disable", "enable", "pause", "resume", "stop", "start",
            ],
        ),
        ToolParameter(
            name="priority",
            type=ParameterType.STRING,
            description="New priority (only for action=update)",
            required=False,
            enum=["urgent", "high", "normal", "low"],
        ),
        ToolParameter(
            name="title",
            type=ParameterType.STRING,
            description=(
                "New title (only for action=update). When changing cron/run_at, "
                "also update title so the UI wording matches the new schedule."
            ),
            required=False,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="New description (only for action=update)",
            required=False,
        ),
        ToolParameter(
            name="run_once",
            type=ParameterType.BOOLEAN,
            description="Update one-time vs recurring schedule",
            required=False,
        ),
        ToolParameter(
            name="run_at",
            type=ParameterType.STRING,
            description="ISO 8601 datetime for one-time scheduled execution",
            required=False,
        ),
        ToolParameter(
            name="cron",
            type=ParameterType.STRING,
            description=(
                "Cron expression for recurring scheduled execution. Pass this as "
                "a top-level argument, not inside a `fields` wrapper."
            ),
            required=False,
        ),
        ToolParameter(
            name="cron_description",
            type=ParameterType.STRING,
            description=(
                "Human-readable Chinese schedule description shown in UI. "
                "When changing schedule, provide this together with title."
            ),
            required=False,
        ),
        ToolParameter(
            name="timezone",
            type=ParameterType.STRING,
            description="Timezone for scheduled tasks",
            required=False,
        ),
        ToolParameter(
            name="user_prompt",
            type=ParameterType.STRING,
            description="Execution prompt stored with the scheduler",
            required=False,
        ),
        ToolParameter(
            name="enabled",
            type=ParameterType.BOOLEAN,
            description=(
                "Enable or disable a scheduled task. False stops it; True resumes it. "
                "Can be used with action=update as a compatibility shortcut."
            ),
            required=False,
        ),
    ],
)
async def task_update(
    ctx: ToolContext,
    task_id: str,
    action: str = "update",
    priority: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    run_once: Optional[bool] = None,
    run_at: Optional[str] = None,
    cron: Optional[str] = None,
    cron_description: Optional[str] = None,
    timezone: Optional[str] = None,
    user_prompt: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ToolResult:
    from smartclaw.task.manager import TaskManager
    from smartclaw.task.models import TaskPriority

    normalized_action = (action or "update").lower()
    if normalized_action in {"pause", "stop"}:
        normalized_action = "disable"
    elif normalized_action in {"resume", "start"}:
        normalized_action = "enable"

    if normalized_action == "cancel":
        task = await TaskManager.cancel_execution(task_id)
    elif normalized_action == "retry":
        task = await TaskManager.retry_execution(task_id)
    elif normalized_action == "disable":
        task = await TaskManager.disable_scheduler(task_id)
    elif normalized_action == "enable":
        task = await TaskManager.enable_scheduler(task_id)
    elif normalized_action == "update":
        fields = {}
        if priority:
            fields["priority"] = TaskPriority(priority)
        if title:
            fields["title"] = title
        if description is not None:
            fields["description"] = description
        try:
            task = await TaskManager.update_scheduler_with_trigger(
                task_id,
                fields=fields,
                cron=cron,
                timezone=timezone,
                cron_description=cron_description,
                run_once=run_once,
                run_at=run_at,
                user_prompt=user_prompt,
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if enabled is False:
            task = await TaskManager.disable_scheduler(task_id) or task
            normalized_action = "disable"
        elif enabled is True:
            task = await TaskManager.enable_scheduler(task_id) or task
            normalized_action = "enable"
    else:
        return ToolResult(success=False, error=f"Unknown action: {action}")

    if not task:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=f"Task {normalized_action}d: {task.title}",
    )


# ======================================================================
# task_delete
# ======================================================================

@ToolRegistry.register_function(
    name="task_delete",
    description="Delete a task permanently",
    description_cn="永久删除任务。",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
    ],
)
async def task_delete(ctx: ToolContext, task_id: str) -> ToolResult:
    from smartclaw.task.manager import TaskManager

    execution = await TaskManager.get_execution(task_id)
    if execution is not None:
        ok = await TaskManager.delete_execution(task_id)
    else:
        ok = await TaskManager.delete_scheduler(task_id)
    if not ok:
        return ToolResult(success=False, error=f"Task {task_id} not found")
    return ToolResult(success=True, output=f"Task {task_id} deleted.")


# ======================================================================
# task_rerun
# ======================================================================

@ToolRegistry.register_function(
    name="task_rerun",
    description="Rerun a task. If it is active, it will be cancelled and a new execution will be created.",
    description_cn="重新运行任务。如果任务正在活跃运行，会先取消它并创建新的执行记录。",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="task_id",
            type=ParameterType.STRING,
            description="Task ID",
            required=True,
        ),
    ],
)
async def task_rerun(ctx: ToolContext, task_id: str) -> ToolResult:
    from smartclaw.task.manager import TaskManager

    task = await TaskManager.rerun_execution(task_id)
    if task is None:
        task = await TaskManager.rerun_scheduler(task_id)
    if not task:
        return ToolResult(success=False, error=f"Task {task_id} not found")

    return ToolResult(
        success=True,
        output=_format_task(task),
        title=f"Task rerun: {task.title}",
    )


# ======================================================================
# Formatting helpers
# ======================================================================

_STATUS_ICON = {
    "pending": "⏳",
    "queued": "📋",
    "running": "🟢",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}


def _format_task_line(t) -> str:
    status = getattr(getattr(t, "status", None), "value", str(getattr(t, "status", "")))
    icon = _STATUS_ICON.get(status, "·")
    pri = f"[{t.priority.value}]" if t.priority.value != "normal" else ""
    return f"  {icon} {t.id}  {pri} {t.title}  ({status})"


def _format_task(t) -> str:
    mode_value = getattr(getattr(t, "mode", None), "value", getattr(t, "mode", None))
    trigger = getattr(t, "trigger", None)
    if mode_value == "cron":
        type_value = "scheduled"
    elif getattr(trigger, "run_immediately", False):
        type_value = "immediate"
    elif trigger is not None:
        type_value = "once"
    else:
        type_value = "execution"
    status_value = getattr(getattr(t, "status", None), "value", str(getattr(t, "status", "")))
    lines = [
        f"ID: {t.id}",
        f"Title: {t.title}",
        f"Type: {type_value}",
        f"Status: {_STATUS_ICON.get(status_value, '')} {status_value}",
        f"Priority: {t.priority.value}",
    ]
    if trigger is not None:
        if trigger.run_at:
            lines.append(f"Run at: {trigger.run_at.isoformat()}")
        if trigger.cron:
            lines.append(f"Cron: {trigger.cron} ({trigger.timezone})")
        if trigger.next_run:
            lines.append(f"Next run: {trigger.next_run.isoformat()}")
        if trigger.cron_description:
            lines.append(f"Schedule desc: {trigger.cron_description}")
    if getattr(t, "queued_at", None):
        lines.append(f"Queued: {t.queued_at.isoformat()}")
    if getattr(t, "started_at", None):
        lines.append(f"Started: {t.started_at.isoformat()}")
    if getattr(t, "completed_at", None):
        lines.append(f"Completed: {t.completed_at.isoformat()}")
    if getattr(t, "duration_ms", None) is not None:
        lines.append(f"Duration: {t.duration_ms}ms")
    if getattr(t, "result_summary", None):
        lines.append(f"Result:\n{t.result_summary}")
    if getattr(t, "error", None):
        lines.append(f"Error: {t.error}")
    lines.append(f"Created: {t.created_at.isoformat()}")
    return "\n".join(lines)
