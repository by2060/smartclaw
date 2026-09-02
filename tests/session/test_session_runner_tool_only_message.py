import pytest

from smartclaw.agent.agent import AgentInfo
from smartclaw.agent.registry import Agent
from smartclaw.provider.provider import ChatMessage, Provider
from smartclaw.session.message import Message, MessageRole, ToolPart, ToolStateCompleted
from smartclaw.session.runner import SessionRunner, StepResult
from smartclaw.session.session import Session
from smartclaw.utils.id import Identifier


@pytest.mark.asyncio
async def test_runner_does_not_disable_tools_after_tool_only_assistant_message(monkeypatch):
    """
    Regression test.

    When the last assistant message contains only tool results (e.g. `question`)
    and no non-empty text, the runner must NOT disable tools. Otherwise, multi-step
    flows that require follow-up tool calls (like generating workflow.json after
    user confirms) can get stuck.
    """

    session = await Session.create(project_id="test_project_tool_only", directory="/test/dir")

    user_1 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="start",
    )

    assistant = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",  # Empty text part -> has_text == False in runner
        parentID=user_1.id,
        modelID="test-model",
        providerID="test-provider",
        agent="titan",
    )

    # Add a completed tool result part (simulates AskQuestion completion)
    tool_part = ToolPart(
        id=Identifier.ascending("part"),
        sessionID=session.id,
        messageID=assistant.id,
        callID="call_question_1",
        tool="question",
        state=ToolStateCompleted(
            input={"questions": [{"question": "continue?", "options": ["yes", "no"]}]},
            output="User has answered your questions: ...",
            title="Asked 1 question",
            metadata={"answers": [["yes"]]},
            time={"start": 0, "end": 1},
        ),
    )
    await Message.store_part(session.id, assistant.id, tool_part)

    user_2 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="确认并生成 JSON",
    )

    messages = [user_1, assistant, user_2]

    class DummyProvider:
        def is_configured(self) -> bool:
            return True

    async def fake_apply_config(*args, **kwargs) -> None:
        return None

    async def fake_agent_get(name: str):
        return AgentInfo(name=name)

    sentinel_tools = [{"type": "function", "function": {"name": "write", "description": "", "parameters": {}}}]
    captured = {}

    async def fake_build_system_prompts(self, agent):  # noqa: ANN001
        return []

    async def fake_build_callable_tool_schema(self, agent, messages=None):  # noqa: ANN001
        del agent, messages
        return list(sentinel_tools)

    async def fake_to_chat_messages(self, _messages, _system_prompts):  # noqa: ANN001
        return [ChatMessage(role="user", content="test")]

    async def fake_call_llm(self, provider, messages, tools, agent, assistant_msg):  # noqa: ANN001
        captured["tools"] = tools
        return StepResult(action="stop", content="ok")

    monkeypatch.setattr(Provider, "get", lambda _provider_id: DummyProvider())
    monkeypatch.setattr(Provider, "apply_config", fake_apply_config)
    monkeypatch.setattr(Agent, "get", fake_agent_get)
    monkeypatch.setattr(SessionRunner, "_build_system_prompts", fake_build_system_prompts)
    monkeypatch.setattr(SessionRunner, "_build_callable_tool_schema", fake_build_callable_tool_schema)
    monkeypatch.setattr(SessionRunner, "_to_chat_messages", fake_to_chat_messages)
    monkeypatch.setattr(SessionRunner, "_call_llm", fake_call_llm)

    runner = SessionRunner(session=session, provider_id="test-provider", model_id="test-model", agent_name="titan")
    runner._step = 2  # ensure reminder wrapping branch doesn't break assumptions

    result = await runner._process_step(messages=messages, last_user=user_2)
    assert result.action == "stop"

    # Critical assertion: tools must remain available (not cleared to []).
    assert captured["tools"] == sentinel_tools


@pytest.mark.asyncio
async def test_runner_disables_tools_after_repeated_invalid_tool_only_messages(monkeypatch):
    session = await Session.create(project_id="test_project_invalid_tool_only", directory="/test/dir")

    user_1 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="start",
    )
    assistant_1 = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
        parentID=user_1.id,
        modelID="test-model",
        providerID="test-provider",
        agent="titan",
    )
    await Message.store_part(
        session.id,
        assistant_1.id,
        ToolPart(
            id=Identifier.ascending("part"),
            sessionID=session.id,
            messageID=assistant_1.id,
            callID="call_invalid_1",
            tool="invalid",
            state=ToolStateCompleted(
                input={"tool": "write", "error": "Failed to parse tool arguments", "arguments_preview": "{"},
                output="The arguments provided to the tool 'write' are invalid.",
                title="invalid",
                metadata={},
                time={"start": 0, "end": 1},
            ),
        ),
    )

    user_2 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="retry",
    )
    assistant_2 = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
        parentID=user_2.id,
        modelID="test-model",
        providerID="test-provider",
        agent="titan",
    )
    await Message.store_part(
        session.id,
        assistant_2.id,
        ToolPart(
            id=Identifier.ascending("part"),
            sessionID=session.id,
            messageID=assistant_2.id,
            callID="call_invalid_2",
            tool="invalid",
            state=ToolStateCompleted(
                input={"tool": "write", "error": "Failed to parse tool arguments", "arguments_preview": "{"},
                output="The arguments provided to the tool 'write' are invalid.",
                title="invalid",
                metadata={},
                time={"start": 2, "end": 3},
            ),
        ),
    )

    user_3 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="继续",
    )

    messages = [user_1, assistant_1, user_2, assistant_2, user_3]

    class DummyProvider:
        def is_configured(self) -> bool:
            return True

    async def fake_apply_config(*args, **kwargs) -> None:
        return None

    async def fake_agent_get(name: str):
        return AgentInfo(name=name)

    sentinel_tools = [{"type": "function", "function": {"name": "write", "description": "", "parameters": {}}}]
    captured = {}

    async def fake_build_system_prompts(self, agent):  # noqa: ANN001
        return []

    async def fake_build_callable_tool_schema(self, agent, messages=None):  # noqa: ANN001
        del agent, messages
        return list(sentinel_tools)

    async def fake_to_chat_messages(self, _messages, _system_prompts):  # noqa: ANN001
        captured["system_prompts"] = list(_system_prompts)
        return [ChatMessage(role="user", content="test")]

    async def fake_call_llm(self, provider, messages, tools, agent, assistant_msg):  # noqa: ANN001
        captured["tools"] = tools
        return StepResult(action="stop", content="ok")

    monkeypatch.setattr(Provider, "get", lambda _provider_id: DummyProvider())
    monkeypatch.setattr(Provider, "apply_config", fake_apply_config)
    monkeypatch.setattr(Agent, "get", fake_agent_get)
    monkeypatch.setattr(SessionRunner, "_build_system_prompts", fake_build_system_prompts)
    monkeypatch.setattr(SessionRunner, "_build_callable_tool_schema", fake_build_callable_tool_schema)
    monkeypatch.setattr(SessionRunner, "_to_chat_messages", fake_to_chat_messages)
    monkeypatch.setattr(SessionRunner, "_call_llm", fake_call_llm)

    runner = SessionRunner(session=session, provider_id="test-provider", model_id="test-model", agent_name="titan")
    runner._step = 3

    result = await runner._process_step(messages=messages, last_user=user_3)
    assert result.action == "stop"
    assert captured["tools"] == []
    assert any("Do not make any more tool calls in this turn." in prompt for prompt in captured["system_prompts"])

