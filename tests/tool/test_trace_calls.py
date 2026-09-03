from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import smartclaw.session.runner as runner_module
from smartclaw.provider.provider import ChatMessage
from smartclaw.session.runner import SessionRunner
from smartclaw.session.session import SessionInfo
from smartclaw.task import background as background_module
from smartclaw.task.background import BackgroundManager, BackgroundTask, LaunchInput, ResumeInput
from smartclaw.tool.agent import delegate_task as delegate_module
from smartclaw.tool.registry import ToolContext, ToolResult
from smartclaw.tool.task import task as task_module


def _context() -> ToolContext:
    return ToolContext(session_id="parent-session", message_id="parent-message", agent="titan")


@pytest.mark.asyncio
async def test_runner_logs_gateway_request_start_and_stream_failure(monkeypatch):
    session = SessionInfo.model_construct(
        id="session-1",
        project_id="project-1",
        directory="/tmp/project",
        title="test",
    )
    runner = SessionRunner(session=session, provider_id="demo", model_id="demo-model")
    processor = SimpleNamespace(
        process_event=AsyncMock(),
        _langfuse_generation=None,
    )
    accumulator = SimpleNamespace()

    class FailingProvider:
        async def chat_stream(self, **_kwargs):
            if False:
                yield None
            raise RuntimeError("stream failed")

    monkeypatch.setattr(runner, "_resolve_output_session_id", AsyncMock(return_value="session-1"))
    monkeypatch.setattr(runner, "_should_use_text_tool_call_mode", lambda: False)
    monkeypatch.setattr(runner_module, "StreamProcessor", lambda **_kwargs: processor)
    monkeypatch.setattr(runner_module, "trace_scope", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("disabled")))
    monkeypatch.setattr(
        "smartclaw.provider.options.build_provider_options",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        "smartclaw.session.streaming.tool_accumulator.ToolCallAccumulator",
        lambda _processor: accumulator,
    )
    monkeypatch.setattr(
        "smartclaw.config.Config.get",
        AsyncMock(side_effect=RuntimeError("no config")),
    )
    monkeypatch.setattr(runner_module.HookPipeline, "run_llm_before", AsyncMock())
    monkeypatch.setattr(runner_module.HookPipeline, "run_llm_after", AsyncMock())
    monkeypatch.setattr(
        runner_module.Session,
        "build_gateway_request_context",
        AsyncMock(
            return_value=SimpleNamespace(
                trace_id="trace-1",
                call_source="session.runner",
            )
        ),
    )

    with pytest.raises(RuntimeError, match="stream failed"):
        await runner._call_llm(
            FailingProvider(),
            [ChatMessage(role="user", content="hello")],
            [],
            SimpleNamespace(name="titan"),
            SimpleNamespace(id="assistant-1"),
        )

    runner_module.HookPipeline.run_llm_after.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_launch_and_resume_inherit_gateway_trace(monkeypatch):
    manager = BackgroundManager()
    child = SimpleNamespace(id="child-session", agent="worker")
    inherit = AsyncMock(return_value=child)
    monkeypatch.setattr(background_module.Session, "get_by_id", AsyncMock(return_value=child))
    monkeypatch.setattr(background_module.Session, "create", AsyncMock(return_value=child))
    monkeypatch.setattr(background_module.Session, "inherit_gateway_trace", inherit)
    monkeypatch.setattr(background_module.Message, "create", AsyncMock())
    monkeypatch.setattr(
        manager,
        "_run_session_with_watchdog",
        AsyncMock(return_value=SimpleNamespace(last_message=None)),
    )

    launch_task = BackgroundTask(
        id="launch-task",
        status="pending",
        description="launch",
        prompt="",
        agent="worker",
    )
    await manager._run_task(
        launch_task,
        LaunchInput(
            description="launch",
            prompt="work",
            agent="worker",
            parent_session_id=None,
            parent_message_id=None,
            parent_agent="titan",
            directory="/tmp/project",
            project_id="project-1",
        ),
    )

    resume_task = BackgroundTask(
        id="resume-task",
        status="pending",
        description="resume",
        prompt="",
        agent="worker",
    )
    await manager._run_resume(
        resume_task,
        ResumeInput(
            session_id="child-session",
            prompt="continue",
            parent_session_id="parent-session",
            parent_message_id="parent-message",
            parent_agent="titan",
        ),
    )

    assert launch_task.status == "completed"
    assert resume_task.status == "completed"
    assert inherit.await_args_list[0].args == (child, None, None)
    assert inherit.await_args_list[1].args == (
        child,
        "parent-session",
        "parent-message",
    )


@pytest.mark.asyncio
async def test_delegate_sync_launch_inherits_gateway_trace(monkeypatch):
    parent = SimpleNamespace(
        id="parent-session",
        project_id="project-1",
        directory="/tmp/project",
        metadata={},
        owner_user_id=None,
        owner_username=None,
    )
    created = SimpleNamespace(id="child-session")
    inherit = AsyncMock(return_value=created)
    forwarder = SimpleNamespace(
        final_metadata={"sessionId": "child-session"},
        build_callbacks=lambda **_kwargs: object(),
    )

    monkeypatch.setattr(
        delegate_module,
        "_lookup_agent",
        AsyncMock(return_value=SimpleNamespace(name="worker", delegatable=True)),
    )
    monkeypatch.setattr(delegate_module, "is_delegatable", lambda _name: True)
    monkeypatch.setattr(
        delegate_module,
        "titan_session_uses_full_skill_catalog",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        delegate_module,
        "titan_session_allows_subagent",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(delegate_module, "_find_completed_delegate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        delegate_module.Config,
        "get",
        AsyncMock(return_value=SimpleNamespace(categories=None)),
    )
    monkeypatch.setattr(delegate_module.Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(delegate_module.Session, "create", AsyncMock(return_value=created))
    monkeypatch.setattr(delegate_module.Session, "inherit_gateway_trace", inherit)
    monkeypatch.setattr(delegate_module.Message, "create", AsyncMock())
    monkeypatch.setattr(
        delegate_module.SessionLoop,
        "run",
        AsyncMock(return_value=SimpleNamespace(last_message=None)),
    )
    monkeypatch.setattr(
        delegate_module,
        "format_sync_subagent_result",
        AsyncMock(return_value=ToolResult(success=True, output="done")),
    )

    with patch(
        "smartclaw.session.features.activity_forwarder.ActivityForwarder",
        return_value=forwarder,
    ):
        result = await delegate_module.delegate_task_tool(
            _context(),
            prompt="work",
            subagent_type="worker",
            run_in_background=False,
        )

    assert result.success is True
    inherit.assert_awaited_once_with(created, "parent-session", "parent-message")


@pytest.mark.asyncio
async def test_task_sync_continue_and_launch_inherit_gateway_trace(monkeypatch):
    parent = SimpleNamespace(
        id="parent-session",
        project_id="project-1",
        directory="/tmp/project",
        provider=None,
        model=None,
        model_pinned=False,
    )
    child = SimpleNamespace(id="child-session", agent="worker")
    inherit = AsyncMock(return_value=child)
    forwarder = SimpleNamespace(
        final_metadata={"sessionId": "child-session"},
        build_callbacks=lambda **_kwargs: object(),
    )

    monkeypatch.setattr(task_module, "is_delegatable", lambda _name: True)
    monkeypatch.setattr(
        task_module,
        "titan_session_allows_subagent",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        task_module,
        "_resolve_child_model",
        AsyncMock(return_value=(None, None, "fallback")),
    )
    monkeypatch.setattr(
        task_module.Session,
        "get_by_id",
        AsyncMock(side_effect=[parent, child, parent]),
    )
    monkeypatch.setattr(task_module.Session, "create", AsyncMock(return_value=child))
    monkeypatch.setattr(task_module.Session, "inherit_gateway_trace", inherit)
    monkeypatch.setattr(task_module.Message, "create", AsyncMock())
    monkeypatch.setattr(
        task_module.SessionLoop,
        "run",
        AsyncMock(return_value=SimpleNamespace(last_message=None)),
    )
    monkeypatch.setattr(
        task_module,
        "format_sync_subagent_result",
        AsyncMock(return_value=ToolResult(success=True, output="done")),
    )

    continued = await task_module.task_tool(
        _context(),
        description="continue",
        prompt="continue",
        subagent_type="worker",
        session_id="child-session",
    )
    with patch(
        "smartclaw.session.features.activity_forwarder.ActivityForwarder",
        return_value=forwarder,
    ):
        launched = await task_module.task_tool(
            _context(),
            description="launch",
            prompt="launch",
            subagent_type="worker",
        )

    assert continued.success is True
    assert launched.success is True
    assert inherit.await_args_list[0].args == (
        child,
        "parent-session",
        "parent-message",
    )
    assert inherit.await_args_list[1].args == (
        child,
        "parent-session",
        "parent-message",
    )
