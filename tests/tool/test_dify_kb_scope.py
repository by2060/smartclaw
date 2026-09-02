from __future__ import annotations

import asyncio
import importlib

import pytest

from smartclaw.agent.agent import AgentInfo
from smartclaw.tool.registry import ToolContext


def _load_dify_module():
    return importlib.import_module("smartclaw.tool.system.dify_kb_search")


def test_dify_kb_builds_compact_markdown_event_payload():
    module = _load_dify_module()
    records = [
        {
            "dataset_id": "kb_a",
            "score": 0.989856,
            "segment": {
                "id": "seg_a",
                "position": 8,
                "document_id": "doc_a",
                "content": "plain ![image](http://example.test/plain.png)",
                "sign_content": "signed ![image](http://example.test/signed.png?sign=abc)",
                "document": {
                    "id": "doc_a",
                    "name": "Guide.docx",
                    "data_source_type": "upload_file",
                    "doc_metadata": {"auth_tag": "team"},
                },
            },
            "child_chunks": [{"id": "child_a", "score": 0.9}],
        }
    ]

    ctx = ToolContext(session_id="ses_kb", message_id="msg_kb", agent="worker", call_id="call_kb")
    payload = module._build_knowledge_search_event(
        query="gateway process screenshot",
        records=records,
        scope_metadata={"effective_dataset_count": 1},
        ctx=ctx,
    )

    assert set(payload) == {
        "schema",
        "event_type",
        "tool",
        "sessionID",
        "messageID",
        "callID",
        "query",
        "count",
        "image_count",
        "scope",
        "records",
        "markdown",
    }
    assert payload["schema"] == "knowledge_search_result.v1"
    assert payload["event_type"] == "knowledge.search.result.v1"
    assert payload["tool"] == "dify_kb_search"
    assert payload["sessionID"] == "ses_kb"
    assert payload["messageID"] == "msg_kb"
    assert payload["callID"] == "call_kb"
    assert payload["scope"] == {"effective_dataset_count": 1}
    assert payload["count"] == 1
    assert payload["image_count"] == 1
    assert "Guide.docx" in payload["markdown"]
    assert "http://example.test/signed.png?sign=abc" in payload["markdown"]

    record = payload["records"][0]
    assert set(record) == {"dataset_id", "score", "document", "segment", "images"}
    assert record["dataset_id"] == "kb_a"
    assert record["document"] == {"id": "doc_a", "name": "Guide.docx"}
    assert record["segment"] == {
        "id": "seg_a",
        "position": 8,
        "markdown": "signed ![image](http://example.test/signed.png?sign=abc)",
    }
    assert record["images"] == [{"alt": "image", "url": "http://example.test/signed.png?sign=abc"}]
    assert "download_url" not in str(payload)
    assert "doc_metadata" not in str(payload)
    assert "child_chunks" not in str(payload)
    assert "document_download_url_count" not in str(payload)


def test_dify_kb_runtime_config_reads_project_secret_concurrency(monkeypatch):
    module = _load_dify_module()

    class EmptySecrets:
        def get(self, key):
            return ""

    monkeypatch.setattr(module, "get_secret_manager", lambda: EmptySecrets())
    monkeypatch.setattr(
        module,
        "_load_project_secret_file",
        lambda: {
            "dify_api_url": "http://dify.test/v1/",
            "dify_api_key": "token",
            "dify_retrieval_top_k": "7",
            "dify_retrieval_concurrency": "3",
        },
    )

    assert module._load_runtime_config() == ("http://dify.test/v1", "token", 7, 3)


def test_dify_kb_runtime_config_caps_project_secret_top_k(monkeypatch):
    module = _load_dify_module()

    class EmptySecrets:
        def get(self, key):
            return ""

    monkeypatch.setattr(module, "get_secret_manager", lambda: EmptySecrets())
    monkeypatch.setattr(
        module,
        "_load_project_secret_file",
        lambda: {
            "dify_api_url": "http://dify.test/v1/",
            "dify_api_key": "token",
            "dify_retrieval_top_k": "30",
        },
    )

    assert module._load_runtime_config() == (
        "http://dify.test/v1",
        "token",
        module.DEFAULT_RETRIEVAL_SIZE,
        module.DEFAULT_RETRIEVAL_CONCURRENCY,
    )

def test_dify_kb_runtime_config_caps_project_secret_concurrency(monkeypatch):
    module = _load_dify_module()

    class EmptySecrets:
        def get(self, key):
            return ""

    monkeypatch.setattr(module, "get_secret_manager", lambda: EmptySecrets())
    monkeypatch.setattr(
        module,
        "_load_project_secret_file",
        lambda: {
            "dify_api_url": "http://dify.test/v1/",
            "dify_api_key": "token",
            "dify_retrieval_concurrency": "999",
        },
    )

    assert module._load_runtime_config() == (
        "http://dify.test/v1",
        "token",
        module.DEFAULT_RETRIEVAL_SIZE,
        module.DEFAULT_MAX_RETRIEVAL_CONCURRENCY,
    )

@pytest.mark.asyncio
async def test_dify_kb_tool_output_records_keep_raw_dify_records(monkeypatch):
    module = _load_dify_module()
    raw_records = [
        {
            "dataset_id": "kb_a",
            "score": 0.989856,
            "segment": {
                "id": "seg_a",
                "position": 8,
                "document_id": "doc_a",
                "content": "plain content",
                "sign_content": "signed ![image](http://example.test/signed.png?sign=abc)",
                "document": {
                    "id": "doc_a",
                    "name": "Guide.docx",
                    "data_source_type": "upload_file",
                    "doc_metadata": {"auth_tag": "team"},
                },
            },
            "child_chunks": [{"id": "child_a", "score": 0.9}],
        }
    ]

    async def fake_scope(ctx):
        return ["kb_a"], {"effective_dataset_count": 1, "missing_required_scopes": []}

    async def fake_retrieve_from_dify_kb(query, dataset_ids, *, top_k=None):
        return raw_records

    monkeypatch.setattr(module, "_load_runtime_config", lambda top_k=None: ("http://dify.test/v1", "token", 5, 1))
    monkeypatch.setattr(module, "_resolve_effective_dataset_scope", fake_scope)
    monkeypatch.setattr(module, "retrieve_from_dify_kb", fake_retrieve_from_dify_kb)

    ctx = ToolContext(session_id="http-tool", message_id="msg_kb", agent="worker", call_id="call_kb")
    result = await module.dify_kb_search(ctx, query="gateway process screenshot")

    assert result.success is True
    assert result.output["records"] is raw_records
    assert "markdown" not in result.output
    assert "signed ![image](http://example.test/signed.png?sign=abc)" in result.metadata["knowledge_search_result"]["markdown"]
    assert result.output["records"][0]["child_chunks"] == [{"id": "child_a", "score": 0.9}]
    assert result.output["records"][0]["segment"]["document"]["doc_metadata"] == {"auth_tag": "team"}

    event_records = result.metadata["knowledge_search_result"]["records"]
    assert event_records == [
        {
            "dataset_id": "kb_a",
            "score": 0.989856,
            "document": {"id": "doc_a", "name": "Guide.docx"},
            "segment": {
                "id": "seg_a",
                "position": 8,
                "markdown": "signed ![image](http://example.test/signed.png?sign=abc)",
            },
            "images": [{"alt": "image", "url": "http://example.test/signed.png?sign=abc"}],
        }
    ]


@pytest.mark.asyncio
async def test_dify_kb_retrieval_respects_configured_concurrency(monkeypatch):
    module = _load_dify_module()
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    monkeypatch.setattr(
        module,
        "_load_runtime_config",
        lambda top_k=None: ("http://dify.test/v1", "token", 5, 2),
    )

    async def fake_request_json(method, url, *, headers, payload=None, timeout=module.DEFAULT_TIMEOUT):
        nonlocal active, max_active
        dataset_id = url.split("/datasets/", 1)[1].split("/", 1)[0]
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return {"records": [{"score": 1, "segment": {"content": dataset_id}}]}

    monkeypatch.setattr(module, "_request_json", fake_request_json)

    records = await module.retrieve_from_dify_kb(
        "gateway process screenshot",
        ["kb_a", "kb_b", "kb_c", "kb_d", "kb_e"],
    )

    assert max_active == 2
    assert [record["dataset_id"] for record in records] == ["kb_a", "kb_b", "kb_c", "kb_d", "kb_e"]


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

    from smartclaw.agent.registry import Agent
    from smartclaw.session.session import Session

    monkeypatch.setattr(Session, "get_by_id", fake_get_session)
    monkeypatch.setattr(Agent, "get", fake_get_agent)

    ctx = ToolContext(session_id="ses_kb", message_id="msg_kb", agent="worker", extra={})
    dataset_ids, metadata = await module._resolve_effective_dataset_scope(ctx)

    assert dataset_ids == ["kb_b"]
    assert metadata["knowledge_base_count"] == 3
    assert metadata["agent_kb_count"] == 2
    assert metadata["effective_dataset_count"] == 1

def test_dify_kb_extracts_document_name_hints_from_scoped_query():
    module = _load_dify_module()

    assert module._extract_document_name_hints("use \u300aGuide.docx\u300b to answer") == ["Guide.docx"]
    assert module._extract_document_name_hints("use Guide.docx to answer") == ["Guide.docx"]


@pytest.mark.asyncio
async def test_dify_kb_document_filter_is_resolved_inside_effective_scope(monkeypatch):
    module = _load_dify_module()
    looked_up = []
    retrieval_calls = []
    raw_records = [
        {
            "dataset_id": "kb_allowed",
            "score": 0.9,
            "segment": {
                "id": "seg_a",
                "position": 1,
                "document_id": "doc_a",
                "content": "allowed content",
                "document": {"id": "doc_a", "name": "Guide.docx"},
            },
        }
    ]

    async def fake_scope(ctx):
        return ["kb_allowed"], {"effective_dataset_count": 1, "missing_required_scopes": []}

    async def fake_list_dataset_documents(api_url, api_key, dataset_id, keyword):
        looked_up.append((dataset_id, keyword))
        assert dataset_id == "kb_allowed"
        return [{"id": "doc_a", "name": "Guide.docx"}]

    async def fake_retrieve_from_dify_kb(query, dataset_ids, *, top_k=None, document_names_by_dataset=None):
        retrieval_calls.append(
            {
                "dataset_ids": dataset_ids,
                "document_names_by_dataset": document_names_by_dataset,
            }
        )
        return raw_records

    monkeypatch.setattr(module, "_load_runtime_config", lambda top_k=None: ("http://dify.test/v1", "token", 5, 1))
    monkeypatch.setattr(module, "_resolve_effective_dataset_scope", fake_scope)
    monkeypatch.setattr(module, "_list_dataset_documents", fake_list_dataset_documents)
    monkeypatch.setattr(module, "retrieve_from_dify_kb", fake_retrieve_from_dify_kb)

    ctx = ToolContext(session_id="http-tool", message_id="msg_kb", agent="worker", call_id="call_kb")
    result = await module.dify_kb_search(ctx, query="use \u300aGuide.docx\u300b to answer")

    assert result.success is True
    assert looked_up == [("kb_allowed", "Guide.docx")]
    assert retrieval_calls == [
        {
            "dataset_ids": ["kb_allowed"],
            "document_names_by_dataset": {"kb_allowed": ["Guide.docx"]},
        }
    ]
    filter_metadata = result.metadata["knowledge_search_result"]["scope"]["document_name_filter"]
    assert filter_metadata["matched_documents_by_dataset"] == {"kb_allowed": ["Guide.docx"]}


@pytest.mark.asyncio
async def test_dify_kb_document_filter_falls_back_when_no_scoped_document_matches(monkeypatch):
    module = _load_dify_module()
    retrieval_calls = []

    async def fake_scope(ctx):
        return ["kb_a", "kb_b"], {"effective_dataset_count": 2, "missing_required_scopes": []}

    async def fake_list_dataset_documents(api_url, api_key, dataset_id, keyword):
        return []

    async def fake_retrieve_from_dify_kb(query, dataset_ids, *, top_k=None, document_names_by_dataset=None):
        retrieval_calls.append(
            {
                "dataset_ids": dataset_ids,
                "document_names_by_dataset": document_names_by_dataset,
            }
        )
        return []

    monkeypatch.setattr(module, "_load_runtime_config", lambda top_k=None: ("http://dify.test/v1", "token", 5, 1))
    monkeypatch.setattr(module, "_resolve_effective_dataset_scope", fake_scope)
    monkeypatch.setattr(module, "_list_dataset_documents", fake_list_dataset_documents)
    monkeypatch.setattr(module, "retrieve_from_dify_kb", fake_retrieve_from_dify_kb)

    ctx = ToolContext(session_id="http-tool", message_id="msg_kb", agent="worker", call_id="call_kb")
    result = await module.dify_kb_search(ctx, query="use \u300aMissing.docx\u300b to answer")

    assert result.success is True
    assert retrieval_calls == [
        {
            "dataset_ids": ["kb_a", "kb_b"],
            "document_names_by_dataset": None,
        }
    ]
    filter_metadata = result.metadata["knowledge_search_result"]["scope"]["document_name_filter"]
    assert filter_metadata["matched_document_count"] == 0
