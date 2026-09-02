from pathlib import Path

import pytest

from smartclaw.server.routes.workflow import _build_workflow_tool_context
from smartclaw.session.message import Message, MessageRole
from smartclaw.session.session import Session


@pytest.mark.asyncio
async def test_workflow_context_scopes_outputs_to_root_session():
    root = await Session.create(
        project_id="default",
        directory=str(Path.cwd()),
        title="workflow-root",
        agent="titan",
    )
    child = await Session.create(
        project_id="default",
        directory=str(Path.cwd()),
        title="workflow-child",
        parent_id=root.id,
        agent="titan",
    )
    message = await Message.create(
        session_id=child.id,
        role=MessageRole.USER,
        content="workflow child message",
        agent="titan",
    )

    ctx = await _build_workflow_tool_context(
        workflow_id="wf-root-output",
        action_name="run",
        session_id=child.id,
        message_id=message.id,
        agent="titan",
    )

    assert ctx.session_id == child.id
    assert ctx.extra["main_session_key"] == root.id
    assert ctx.extra["output_session_id"] == root.id
