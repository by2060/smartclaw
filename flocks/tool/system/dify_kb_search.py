"""Built-in Dify knowledge base retrieval tool."""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from flocks.security import get_secret_manager
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.dify_kb_search")

DEFAULT_TIMEOUT = 20
DEFAULT_RETRIEVAL_SIZE = 5
DEFAULT_RETRIEVAL_CONCURRENCY = 1
DEFAULT_MAX_RETRIEVAL_CONCURRENCY = 30
KNOWLEDGE_SEARCH_EVENT_TYPE = "knowledge.search.result.v1"
KNOWLEDGE_SEARCH_SCHEMA = "knowledge_search_result.v1"
IMAGE_MARKDOWN_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _now_for_log() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def _normalize_base_url(raw_url: Optional[str]) -> str:
    return (raw_url or "").strip().rstrip("/")


def _first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return ""


def _load_project_secret_file() -> Dict[str, Any]:
    project_secret_path = Path.cwd() / ".flocks" / ".secret.json"
    if not project_secret_path.is_file():
        return {}

    try:
        raw = json.loads(project_secret_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warn("dify_kb_search.project_secret_parse_failed", {"path": str(project_secret_path), "error": str(exc)})
        return {}

    if not isinstance(raw, dict):
        return {}

    return raw


def _resolve_positive_int(value: Any, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _load_runtime_config(top_k: Optional[int] = None) -> tuple[str, str, int, int]:
    secrets = get_secret_manager()
    project_secret = _load_project_secret_file()

    api_url = _normalize_base_url(
        _first_non_empty(
            secrets.get("dify_api_url"),
            project_secret.get("dify_api_url"),
            os.getenv("DIFY_API_URL"),
        )
    )
    api_key = _first_non_empty(
        secrets.get("dify_api_key"),
        project_secret.get("dify_api_key"),
        os.getenv("DIFY_API_KEY"),
    )

    configured_top_k = _first_non_empty(
        secrets.get("dify_retrieval_top_k"),
        str(project_secret.get("dify_retrieval_top_k")) if project_secret.get("dify_retrieval_top_k") is not None else "",
        os.getenv("DIFY_RETRIEVAL_TOP_K"),
    )
    resolved_top_k = _resolve_positive_int(
        top_k if top_k is not None else configured_top_k,
        DEFAULT_RETRIEVAL_SIZE,
    )

    configured_concurrency = _first_non_empty(
        secrets.get("dify_retrieval_concurrency"),
        str(project_secret.get("dify_retrieval_concurrency"))
        if project_secret.get("dify_retrieval_concurrency") is not None
        else "",
        os.getenv("DIFY_RETRIEVAL_CONCURRENCY"),
    )
    resolved_concurrency = _resolve_positive_int(
        configured_concurrency,
        DEFAULT_RETRIEVAL_CONCURRENCY,
    )
    resolved_concurrency = _clamp_int(
        resolved_concurrency,
        minimum=1,
        maximum=DEFAULT_MAX_RETRIEVAL_CONCURRENCY,
    )

    return api_url, api_key, resolved_top_k, resolved_concurrency

def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _should_request_permission(ctx: ToolContext) -> bool:
    session_id = getattr(ctx, "session_id", "") or ""
    return session_id != "http-tool"


def _normalize_dataset_ids(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
        return [item.strip() for item in text.split(",") if item.strip()]

    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    return []


def _resolve_user_context_knowledge_base_ids(ctx: ToolContext) -> List[str]:
    extra = getattr(ctx, "extra", None) or {}
    if not isinstance(extra, dict):
        return []

    user_context = extra.get("user_context")
    if not isinstance(user_context, dict):
        user_context = extra.get("userContext")
    if not isinstance(user_context, dict):
        return []

    return _normalize_dataset_ids(user_context.get("knowledgeBaseIds"))


async def _resolve_session_knowledge_base_ids(ctx: ToolContext) -> List[str]:
    session_id = str(getattr(ctx, "session_id", "") or "").strip()
    if not session_id:
        return []

    try:
        from flocks.session.session import Session

        session = await Session.get_by_id(session_id)
    except Exception as exc:
        log.warn("dify_kb_search.session_kb_resolve_failed", {"session_id": session_id, "error": str(exc)})
        return []

    user_context = getattr(session, "user_context", None) if session else None
    if not isinstance(user_context, dict):
        return []

    return _normalize_dataset_ids(user_context.get("knowledgeBaseIds"))


async def _resolve_agent_knowledge_base_ids(ctx: ToolContext) -> Optional[List[str]]:
    agent_name = str(getattr(ctx, "agent", "") or "").strip()
    if not agent_name:
        return None

    try:
        from flocks.agent.registry import Agent

        agent = await Agent.get(agent_name)
    except Exception as exc:
        log.warn("dify_kb_search.agent_kb_resolve_failed", {"agent": agent_name, "error": str(exc)})
        return None

    if agent is None:
        return None

    agent_kb = getattr(agent, "kb", None)
    if agent_kb is None:
        return None
    if "all" in agent_kb:
        return ["all"]
    return _normalize_dataset_ids(agent_kb)


async def _resolve_effective_dataset_scope(ctx: ToolContext) -> tuple[List[str], Dict[str, Any]]:
    knowledge_base_ids = _resolve_user_context_knowledge_base_ids(ctx)
    log.info(f"当前会话context中的的知识库ids:{knowledge_base_ids}")
    if not knowledge_base_ids:
        knowledge_base_ids = await _resolve_session_knowledge_base_ids(ctx)
    log.info(f"当前会话持久化的的知识库ids:{knowledge_base_ids}")
    agent_kb_ids = await _resolve_agent_knowledge_base_ids(ctx)
    log.info(f"当前智能体的知识库ids:{agent_kb_ids}")
    missing_required_scopes: List[str] = []

    if not knowledge_base_ids:
        missing_required_scopes.append("session.userContext.knowledgeBaseIds")
    if agent_kb_ids is None:
        missing_required_scopes.append("agent.kb")

    if len(agent_kb_ids) == 1 and "all" == agent_kb_ids[0]:
        effective_dataset_ids = knowledge_base_ids
    else:
        agent_kb_set = set(agent_kb_ids or [])
        effective_dataset_ids = [dataset_id for dataset_id in knowledge_base_ids if dataset_id in agent_kb_set]
    log.info(f"要检索的知识库的ids:{effective_dataset_ids}")
    scope_metadata = {
        "knowledge_base_count": len(knowledge_base_ids),
        "agent_kb_count": len(agent_kb_ids or []),
        "effective_dataset_count": len(effective_dataset_ids),
        "missing_required_scopes": missing_required_scopes,
    }
    log.info(f"scope_metadata:{scope_metadata}")
    return effective_dataset_ids, scope_metadata


def _http_json_request(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    data: Optional[bytes] = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())

    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")

    if not body.strip():
        return {}

    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("Dify API returned a non-object JSON payload")
    return parsed


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_score(score: Any) -> str:
    try:
        return f"{float(score):.3f}"
    except (TypeError, ValueError):
        return "-"


def _extract_markdown_images(markdown: str) -> List[Dict[str, str]]:
    images: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in IMAGE_MARKDOWN_RE.finditer(markdown or ""):
        alt = (match.group(1) or "image").strip() or "image"
        url = (match.group(2) or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        images.append({"alt": alt, "url": url})
    return images


def _document_source_block(
    *,
    document_name: str,
    document_id: str,
    dataset_id: Any,
    position: Any,
    score: Any,
) -> str:
    return "\n".join(
        [
            f"- {document_name}",
            f"  - Dataset ID: `{dataset_id or '-'}`",
            f"  - Document ID: `{document_id or '-'}`",
            f"  - Segment position: `{position if position is not None else '-'}`",
            f"  - Score: `{_format_score(score)}`",
        ]
    )


def _record_markdown(record: Dict[str, Any], index: int) -> str:
    segment = _as_dict(record.get("segment"))
    document = _as_dict(segment.get("document"))
    content = _first_non_empty(segment.get("sign_content"), segment.get("content"))
    document_name = _first_non_empty(document.get("name"), "Untitled document")
    position = segment.get("position")
    score = _format_score(record.get("score"))
    lines = [
        f"#### {index}. {document_name}",
        "",
        f"- Dataset ID: `{record.get('dataset_id') or '-'}`",
        f"- Document ID: `{segment.get('document_id') or document.get('id') or '-'}`",
        f"- Segment position: `{position if position is not None else '-'}`",
        f"- Score: `{score}`",
        "",
    ]
    if content:
        lines.extend([content, ""])
    return "\n".join(lines).rstrip()


def _build_knowledge_search_event(
    *,
    query: str,
    records: List[Dict[str, Any]],
    scope_metadata: Dict[str, Any],
    ctx: ToolContext,
) -> Dict[str, Any]:
    normalized_records: List[Dict[str, Any]] = []
    source_lines = ["### Sources", ""]
    image_count = 0

    for index, record in enumerate(records, start=1):
        segment = _as_dict(record.get("segment"))
        document = _as_dict(segment.get("document"))
        content_markdown = _first_non_empty(segment.get("sign_content"), segment.get("content"))
        images = _extract_markdown_images(content_markdown)
        image_count += len(images)

        document_id = _first_non_empty(segment.get("document_id"), document.get("id"))
        document_name = _first_non_empty(document.get("name"), "Untitled document")
        source_block = _document_source_block(
            document_name=document_name,
            document_id=document_id,
            dataset_id=record.get("dataset_id"),
            position=segment.get("position"),
            score=record.get("score"),
        )
        source_lines.append(f"{index}. {source_block[2:] if source_block.startswith('- ') else source_block}")

        normalized_records.append(
            {
                "dataset_id": record.get("dataset_id"),
                "score": record.get("score"),
                "document": {
                    "id": document_id,
                    "name": document_name,
                },
                "segment": {
                    "id": segment.get("id"),
                    "position": segment.get("position"),
                    "markdown": content_markdown,
                },
                "images": images,
            }
        )

    records_markdown = "\n\n".join(
        _record_markdown(record, index)
        for index, record in enumerate(records, start=1)
    )
    sources_block = "\n".join(source_lines).rstrip()

    markdown_parts = [
        "### Knowledge search result",
        "",
        f"- Query: `{query.strip()}`",
        f"- Records: `{len(records)}`",
        f"- Images: `{image_count}`",
    ]
    if records_markdown:
        markdown_parts.extend(["", records_markdown])
    if sources_block:
        markdown_parts.extend(["", sources_block])

    return {
        "schema": KNOWLEDGE_SEARCH_SCHEMA,
        "event_type": KNOWLEDGE_SEARCH_EVENT_TYPE,
        "tool": "dify_kb_search",
        "sessionID": getattr(ctx, "session_id", None),
        "messageID": getattr(ctx, "message_id", None),
        "callID": getattr(ctx, "call_id", None),
        "query": query.strip(),
        "count": len(records),
        "image_count": image_count,
        "scope": scope_metadata,
        "records": normalized_records,
        "markdown": "\n".join(markdown_parts).rstrip(),
    }


async def _publish_knowledge_search_event(ctx: ToolContext, payload: Dict[str, Any]) -> None:
    if not getattr(ctx, "event_publish_callback", None):
        return
    try:
        await ctx.event_publish_callback(KNOWLEDGE_SEARCH_EVENT_TYPE, payload)
    except Exception as exc:
        log.warn("dify_kb_search.event_publish_failed", {"error": str(exc)})


async def _request_json(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _http_json_request,
        method,
        url,
        headers=headers,
        payload=payload,
        timeout=timeout,
    )


async def retrieve_from_dify_kb(
    query: str,
    dataset_ids: List[str],
    *,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    retrieval_start = time.perf_counter()
    api_url, api_key, resolved_top_k, resolved_concurrency = _load_runtime_config(top_k=top_k)

    if not api_url or not api_key:
        raise ValueError(
            "Missing Dify configuration: set dify_api_url and dify_api_key in .secret.json, "
            "or provide DIFY_API_URL and DIFY_API_KEY as environment variables."
        )

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Query must not be empty.")

    requested_dataset_ids = _normalize_dataset_ids(dataset_ids)
    if not requested_dataset_ids:
        raise ValueError("No Dify dataset IDs were provided.")

    effective_concurrency = min(resolved_concurrency, len(requested_dataset_ids))
    log.info(
        "dify_kb_search.retrieval_start",
        {
            "start_time": _now_for_log(),
            "dataset_count": len(requested_dataset_ids),
            "top_k": resolved_top_k,
            "concurrency": effective_concurrency,
            "query_length": len(normalized_query),
        },
    )

    semaphore = asyncio.Semaphore(effective_concurrency)

    async def _retrieve_dataset(dataset_id: str) -> tuple[List[Dict[str, Any]], Optional[Dict[str, str]]]:
        async with semaphore:
            dataset_start = time.perf_counter()
            log.info(
                "dify_kb_search.dataset_retrieval_start",
                {
                    "start_time": _now_for_log(),
                    "dataset_id": dataset_id,
                    "top_k": resolved_top_k,
                },
            )
            try:
                response = await _request_json(
                    "POST",
                    f"{api_url}/datasets/{dataset_id}/retrieve",
                    headers=_headers(api_key),
                    payload={"query": normalized_query, "top_k": resolved_top_k},
                )
                records = response.get("records", [])
                dataset_records: List[Dict[str, Any]] = []
                if isinstance(records, list):
                    for record in records:
                        if isinstance(record, dict):
                            enriched = dict(record)
                            enriched.setdefault("dataset_id", dataset_id)
                            dataset_records.append(enriched)
                log.info(
                    "dify_kb_search.dataset_retrieval_done",
                    {
                        "end_time": _now_for_log(),
                        "dataset_id": dataset_id,
                        "elapsed_ms": _elapsed_ms(dataset_start),
                        "record_count": len(dataset_records),
                    },
                )
                return dataset_records, None
            except urllib.error.HTTPError as exc:
                try:
                    error_text = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    error_text = str(exc)
                log.warn(
                    "dify_kb_search.dataset_retrieval_http_error",
                    {
                        "end_time": _now_for_log(),
                        "dataset_id": dataset_id,
                        "elapsed_ms": _elapsed_ms(dataset_start),
                        "error": error_text,
                    },
                )
                return [], {"dataset_id": dataset_id, "error": f"HTTP {exc.code}: {error_text[:300]}"}
            except Exception as exc:
                log.warn(
                    "dify_kb_search.dataset_retrieval_error",
                    {
                        "end_time": _now_for_log(),
                        "dataset_id": dataset_id,
                        "elapsed_ms": _elapsed_ms(dataset_start),
                        "error": str(exc),
                    },
                )
                return [], {"dataset_id": dataset_id, "error": str(exc)}

    dataset_results = await asyncio.gather(
        *(_retrieve_dataset(dataset_id) for dataset_id in requested_dataset_ids)
    )

    all_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for dataset_records, error in dataset_results:
        all_records.extend(dataset_records)
        if error is not None:
            errors.append(error)

    log.info(
        "dify_kb_search.retrieval_done",
        {
            "end_time": _now_for_log(),
            "elapsed_ms": _elapsed_ms(retrieval_start),
            "dataset_count": len(requested_dataset_ids),
            "successful_record_count": len(all_records),
            "error_count": len(errors),
            "concurrency": effective_concurrency,
        },
    )

    if all_records:
        return all_records

    if errors:
        raise RuntimeError(json.dumps(errors, ensure_ascii=False))

    return []

@ToolRegistry.register_function(
    name="dify_kb_search",
    description=(
        "Query Dify knowledge bases and return retrieved records. "
        "The dataset scope is the intersection of the current session's "
        "userContext.knowledgeBaseIds and the current agent's kb field."
    ),
    description_cn=(
        "查询 Dify 知识库并返回检索记录。数据集范围由当前会话的 "
        "userContext.knowledgeBaseIds 与当前 Agent 的 kb 字段共同限定。"
    ),
    category=ToolCategory.SEARCH,
    tags=["dify", "knowledge-base", "rag", "retrieval"],
    parameters=[
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            description="Search text sent to the Dify knowledge base retrieval API.",
            required=True,
        ),
        ToolParameter(
            name="top_k",
            type=ParameterType.INTEGER,
            description="Maximum number of records to retrieve from each dataset. Defaults to DIFY_RETRIEVAL_SIZE or 5.",
            required=False,
            default=DEFAULT_RETRIEVAL_SIZE,
        ),
    ],
)
async def dify_kb_search(
    ctx: ToolContext,
    query: str,
    top_k: int = DEFAULT_RETRIEVAL_SIZE,
) -> ToolResult:
    search_start = time.perf_counter()
    log.info(
        "Dify 知识检索工具开始执行",
        {
            "开始时间": _now_for_log(),
            "session_id": getattr(ctx, "session_id", None),
            "message_id": getattr(ctx, "message_id", None),
            "call_id": getattr(ctx, "call_id", None),
            "agent": getattr(ctx, "agent", None),
            "查询长度": len(query.strip()) if isinstance(query, str) else 0,
            "top_k": top_k,
        },
    )
    api_url, api_key, resolved_top_k, resolved_concurrency = _load_runtime_config(top_k=top_k)
    del api_key
    resolved_dataset_ids, scope_metadata = await _resolve_effective_dataset_scope(ctx)

    if not query or not query.strip():
        return ToolResult(success=False, error="Parameter 'query' is required.")

    if not api_url:
        return ToolResult(
            success=False,
            error=(
                "Missing Dify configuration: set dify_api_url in .secret.json, "
                "or provide DIFY_API_URL as an environment variable."
            ),
        )

    if scope_metadata["missing_required_scopes"]:
        return ToolResult(
            success=False,
            error=(
                "Missing Dify knowledge base permission context: "
                + ", ".join(scope_metadata["missing_required_scopes"])
            ),
            metadata=scope_metadata,
            title="Dify knowledge base query",
        )

    if not resolved_dataset_ids:
        return ToolResult(
            success=False,
            error=(
                "No Dify knowledge bases are queryable after intersecting the current "
                "agent kb field with session userContext.knowledgeBaseIds."
            ),
            metadata=scope_metadata,
            title="Dify knowledge base query",
        )

    if _should_request_permission(ctx):
        await ctx.ask(
            permission="webfetch",
            patterns=[api_url],
            always=["*"],
            metadata={
                "tool": "dify_kb_search",
                "query": query.strip(),
                "dataset_count": len(resolved_dataset_ids),
                "top_k": resolved_top_k,
                "concurrency": resolved_concurrency,
                **scope_metadata,
            },
        )

    try:
        retrieval_start = time.perf_counter()
        records = await retrieve_from_dify_kb(
            query=query,
            dataset_ids=resolved_dataset_ids,
            top_k=resolved_top_k,
        )
        retrieval_elapsed_ms = _elapsed_ms(retrieval_start)
        log.info(
            "Dify 所有知识库检索调用结束",
            {
                "结束时间": _now_for_log(),
                "检索耗时毫秒": retrieval_elapsed_ms,
                "知识库数量": len(resolved_dataset_ids),
                "记录数": len(records),
            },
        )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return ToolResult(success=False, error=f"Dify API error: HTTP {exc.code}: {body[:500]}")
    except urllib.error.URLError as exc:
        return ToolResult(success=False, error=f"Dify request failed: {exc.reason}")
    except RuntimeError as exc:
        return ToolResult(
            success=False,
            error=f"Dify retrieval failed for all datasets: {exc}",
        )
    except Exception as exc:
        log.error("dify_kb_search.query_failed", {"error": str(exc), "error_type": type(exc).__name__})
        return ToolResult(success=False, error=f"Dify retrieval failed: {exc}")

    processing_start = time.perf_counter()
    log.info(
        "Dify 检索结果处理开始",
        {
            "开始时间": _now_for_log(),
            "记录数": len(records),
            "知识库数量": len(resolved_dataset_ids),
        },
    )

    if not records:
        event_payload = _build_knowledge_search_event(
            query=query,
            records=[],
            scope_metadata=scope_metadata,
            ctx=ctx,
        )
        await _publish_knowledge_search_event(ctx, event_payload)
        processing_elapsed_ms = _elapsed_ms(processing_start)
        log.info(
            "Dify 检索结果处理结束，准备返回",
            {
                "结束时间": _now_for_log(),
                "检索耗时毫秒": retrieval_elapsed_ms,
                "处理后返回耗时毫秒": processing_elapsed_ms,
                "总耗时毫秒": _elapsed_ms(search_start),
                "记录数": 0,
                "图片数": event_payload["image_count"],
            },
        )
        return ToolResult(
            success=True,
            output={
                "records": event_payload["records"],
                "count": event_payload["count"],
                "image_count": event_payload["image_count"],
                "markdown": event_payload["markdown"],
                "message": "No relevant content found.",
            },
            metadata={
                "source": "Dify",
                "record_count": 0,
                "event_type": KNOWLEDGE_SEARCH_EVENT_TYPE,
                "knowledge_search_result": event_payload,
            },
            title="Dify knowledge base query",
        )

    event_payload = _build_knowledge_search_event(
        query=query,
        records=records,
        scope_metadata=scope_metadata,
        ctx=ctx,
    )
    await _publish_knowledge_search_event(ctx, event_payload)
    processing_elapsed_ms = _elapsed_ms(processing_start)
    log.info(
        "Dify 检索结果处理结束，准备返回",
        {
            "结束时间": _now_for_log(),
            "检索耗时毫秒": retrieval_elapsed_ms,
            "处理后返回耗时毫秒": processing_elapsed_ms,
            "总耗时毫秒": _elapsed_ms(search_start),
            "记录数": event_payload["count"],
            "图片数": event_payload["image_count"],
        },
    )

    return ToolResult(
        success=True,
        output={
            "records": records,
            "count": len(records),
            "image_count": event_payload["image_count"],
            "markdown": event_payload["markdown"],
        },
        metadata={
            "source": "Dify",
            "record_count": event_payload["count"],
            "event_type": KNOWLEDGE_SEARCH_EVENT_TYPE,
            "knowledge_search_result": event_payload,
        },
        title="Dify knowledge base query",
    )
