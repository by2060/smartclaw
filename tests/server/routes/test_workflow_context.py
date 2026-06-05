from pathlib import Path

import pytest

from flocks.server.routes.workflow import _build_workflow_tool_context
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session


@pytest.mark.asyncio
async def test_workflow_context_scopes_outputs_to_root_session():
    root = await Session.create(
        project_id="default",
        directory=str(Path.cwd()),
        title="workflow-root",
        agent="rex",
    )
    child = await Session.create(
        project_id="default",
        directory=str(Path.cwd()),
        title="workflow-child",
        parent_id=root.id,
        agent="rex",
    )
    message = await Message.create(
        session_id=child.id,
        role=MessageRole.USER,
        content="workflow child message",
        agent="rex",
    )

    ctx = await _build_workflow_tool_context(
        workflow_id="wf-root-output",
        action_name="run",
        session_id=child.id,
        message_id=message.id,
        agent="rex",
    )

    assert ctx.session_id == child.id
    assert ctx.extra["main_session_key"] == root.id
    assert ctx.extra["output_session_id"] == root.id
