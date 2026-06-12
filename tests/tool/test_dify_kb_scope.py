from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from flocks.agent.agent import AgentInfo
from flocks.tool.registry import ToolContext


def _load_dify_module():
    path = Path.cwd() / ".flocks" / "plugins" / "tools" / "python" / "dify_kb_search.py"
    spec = importlib.util.spec_from_file_location("test_project_dify_kb_search", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_dify_kb_scope_falls_back_to_session_user_context(monkeypatch):
    module = _load_dify_module()
    session = type(
        "SessionObj",
        (),
        {"user_context": {"knowledgeBaseIds": ["kb_a", "kb_b", "kb_c"]}},
    )()
    worker = AgentInfo(name="worker", mode="subagent", kb=["kb_b", "kb_z"])

    async def fake_get_session(session_id: str):
        return session

    async def fake_get_agent(name: str):
        return {"worker": worker}.get(name)

    from flocks.agent.registry import Agent
    from flocks.session.session import Session

    monkeypatch.setattr(Session, "get_by_id", fake_get_session)
    monkeypatch.setattr(Agent, "get", fake_get_agent)

    ctx = ToolContext(session_id="ses_kb", message_id="msg_kb", agent="worker", extra={})
    dataset_ids, metadata = await module._resolve_effective_dataset_scope(ctx)

    assert dataset_ids == ["kb_b"]
    assert metadata["knowledge_base_count"] == 3
    assert metadata["agent_kb_count"] == 2
    assert metadata["effective_dataset_count"] == 1
