from __future__ import annotations

import asyncio
import json
import os
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


def _load_runtime_config(top_k: Optional[int] = None) -> tuple[str, str, int]:
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
    try:
        resolved_top_k = int(top_k if top_k is not None else configured_top_k)
    except (TypeError, ValueError):
        resolved_top_k = DEFAULT_RETRIEVAL_SIZE

    if resolved_top_k <= 0:
        resolved_top_k = DEFAULT_RETRIEVAL_SIZE

    return api_url, api_key, resolved_top_k


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
    return _normalize_dataset_ids(agent_kb)


async def _resolve_effective_dataset_scope(ctx: ToolContext) -> tuple[List[str], Dict[str, Any]]:
    knowledge_base_ids = _resolve_user_context_knowledge_base_ids(ctx)
    agent_kb_ids = await _resolve_agent_knowledge_base_ids(ctx)
    missing_required_scopes: List[str] = []

    if not knowledge_base_ids:
        missing_required_scopes.append("session.userContext.knowledgeBaseIds")
    if agent_kb_ids is None:
        missing_required_scopes.append("agent.kb")

    agent_kb_set = set(agent_kb_ids or [])
    effective_dataset_ids = [dataset_id for dataset_id in knowledge_base_ids if dataset_id in agent_kb_set]

    scope_metadata = {
        "knowledge_base_count": len(knowledge_base_ids),
        "agent_kb_count": len(agent_kb_ids or []),
        "effective_dataset_count": len(effective_dataset_ids),
        "missing_required_scopes": missing_required_scopes,
    }
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
    api_url, api_key, resolved_top_k = _load_runtime_config(top_k=top_k)

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

    all_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for dataset_id in requested_dataset_ids:
        try:
            response = await _request_json(
                "POST",
                f"{api_url}/datasets/{dataset_id}/retrieve",
                headers=_headers(api_key),
                payload={"query": normalized_query, "top_k": resolved_top_k},
            )
            records = response.get("records", [])
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict):
                        enriched = dict(record)
                        enriched.setdefault("dataset_id", dataset_id)
                        all_records.append(enriched)
        except urllib.error.HTTPError as exc:
            try:
                error_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_text = str(exc)
            log.warn("dify_kb_search.dataset_http_error", {"dataset_id": dataset_id, "error": error_text})
            errors.append({"dataset_id": dataset_id, "error": f"HTTP {exc.code}: {error_text[:300]}"})
        except Exception as exc:
            log.warn("dify_kb_search.dataset_error", {"dataset_id": dataset_id, "error": str(exc)})
            errors.append({"dataset_id": dataset_id, "error": str(exc)})

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
    api_url, api_key, resolved_top_k = _load_runtime_config(top_k=top_k)
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
                **scope_metadata,
            },
        )

    try:
        records = await retrieve_from_dify_kb(
            query=query,
            dataset_ids=resolved_dataset_ids,
            top_k=resolved_top_k,
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

    if not records:
        return ToolResult(
            success=True,
            output={"records": [], "message": "No relevant content found."},
            metadata={"source": "Dify", "record_count": 0, **scope_metadata},
            title="Dify knowledge base query",
        )

    return ToolResult(
        success=True,
        output={
            "records": records,
            "count": len(records),
        },
        metadata={"source": "Dify", "record_count": len(records), **scope_metadata},
        title="Dify knowledge base query",
    )
