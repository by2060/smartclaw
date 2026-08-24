"""Workflow artifact validation, writing, and registration."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from smartclaw.utils.log import Log
from smartclaw.workflow.fs_store import find_workspace_root, read_workflow_dir, workflow_scan_dirs
from smartclaw.workflow.models import Workflow
from smartclaw.workflow.node_type_spec import NODE_TYPE_SPECS
from smartclaw.workflow.workflow_lint import lint_workflow


log = Log.create(service="workflow.artifact-service")
EventPublisher = Callable[[str, Dict[str, Any]], Awaitable[None]]


def _format_issue_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        visible = list(value[:10])
        suffix = f"...(+{len(value) - 10})" if len(value) > 10 else ""
        return json.dumps(visible, ensure_ascii=False) + suffix
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def format_workflow_artifact_issues(issues: list[dict[str, Any]], *, max_items: int = 20) -> str:
    if not issues:
        return ""

    lines = ["Lint gate issues:"]
    for index, issue in enumerate(issues[:max_items], start=1):
        severity = issue.get("severity") or "error"
        kind = issue.get("kind") or "unknown"
        message = issue.get("message") or "No message provided."
        location_parts: list[str] = []
        if issue.get("node_id"):
            location_parts.append(f"node={issue['node_id']}")
        if issue.get("node_type"):
            location_parts.append(f"type={issue['node_type']}")
        if issue.get("edge_from") or issue.get("edge_to"):
            location_parts.append(f"edge={issue.get('edge_from', '?')}->{issue.get('edge_to', '?')}")
        if issue.get("tool_name"):
            location_parts.append(f"tool={issue['tool_name']}")
        location = f" ({', '.join(location_parts)})" if location_parts else ""

        detail_parts = []
        for key in (
            "missing_fields",
            "unknown_args",
            "allowed_args",
            "workflow_id",
            "schema_path",
            "src_path",
            "dst_key",
            "import",
            "call",
        ):
            if key in issue and issue[key] not in (None, "", []):
                detail_parts.append(f"{key}={_format_issue_value(issue[key])}")
        details = f" Details: {'; '.join(detail_parts)}" if detail_parts else ""
        lines.append(f"{index}. [{severity}] {kind}{location}: {message}{details}")

    if len(issues) > max_items:
        lines.append(f"... {len(issues) - max_items} more issue(s) omitted")
    return "\n".join(lines)


class WorkflowArtifactError(Exception):
    def __init__(self, message: str, *, issues: Optional[list[dict[str, Any]]] = None):
        self.issues = issues or []
        issue_text = format_workflow_artifact_issues(self.issues)
        super().__init__(f"{message}\n\n{issue_text}" if issue_text else message)


@dataclass
class WorkflowArtifactFiles:
    workflow_json: Optional[dict[str, Any]] = None
    markdown_content: Optional[str] = None
    sample_inputs: Optional[dict[str, Any]] = None
    node_test_results: Optional[dict[str, Any]] = None


@dataclass
class WorkflowValidationResult:
    valid: bool
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def issues(self) -> list[dict[str, Any]]:
        return [*self.errors, *self.warnings]


def workflow_dir(workflow_id: str, *, workspace: Optional[Path] = None) -> Path:
    root = workspace or find_workspace_root()
    return root / ".smartclaw" / "plugins" / "workflows" / workflow_id


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _known_workflow_ids() -> set[str]:
    ids: set[str] = set()
    for root, _source in workflow_scan_dirs():
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.is_dir() and (entry / "workflow.json").is_file():
                ids.add(entry.name)
    return ids


_PREVIEW_STAGE_VALUES = {
    "preview",
    "simplified_preview",
    "workflow_preview",
    "draft_preview",
}

_ARTIFACT_GATE_WARNING_ONLY_KINDS = {
    "multi_incoming_no_join",
    "expensive_node_multi_trigger",
    "edge_selection_missing_select_key",
    "edge_selection_multiple_default_edges",
    "edge_selection_missing_label",
}


def _is_preview_workflow_json(workflow_json: dict[str, Any]) -> bool:
    metadata = workflow_json.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("preview") is True:
        return True
    for key in ("stage", "phase", "mode", "workflowBuilderStage"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip().lower() in _PREVIEW_STAGE_VALUES:
            return True
    return False


def _relax_preview_lint_issues(
    workflow: Workflow,
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    nodes = workflow.nodes_by_id()
    relaxed: list[dict[str, Any]] = []
    for item in issues:
        kind = item.get("kind")
        is_relaxable = False
        if kind == "multi_incoming_no_join":
            is_relaxable = True
        elif kind in {
            "edge_selection_missing_select_key",
            "edge_selection_multiple_default_edges",
        }:
            node_id = item.get("node_id")
            node = nodes.get(str(node_id)) if node_id is not None else None
            is_relaxable = getattr(node, "type", None) == "logic"
        elif kind == "edge_selection_missing_label":
            edge_from = item.get("edge_from")
            node = nodes.get(str(edge_from)) if edge_from is not None else None
            is_relaxable = getattr(node, "type", None) == "logic"

        if is_relaxable:
            downgraded = dict(item)
            downgraded["severity"] = "warning"
            downgraded["preview_relaxed"] = True
            downgraded["message"] = (
                f"{downgraded.get('message', '')} "
                "Allowed while workflow metadata marks this as a preview draft; "
                "fix before final run, validation, or publish."
            ).strip()
            relaxed.append(downgraded)
        else:
            relaxed.append(item)
    return relaxed


def _relax_artifact_gate_lint_issues(
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    relaxed: list[dict[str, Any]] = []
    for item in issues:
        if item.get("severity") == "error" and item.get("kind") in _ARTIFACT_GATE_WARNING_ONLY_KINDS:
            downgraded = dict(item)
            downgraded["severity"] = "warning"
            downgraded["artifact_gate_relaxed"] = True
            downgraded["message"] = (
                f"{downgraded.get('message', '')} "
                "Saved as a warning by the workflow artifact gate; fix before relying on production execution."
            ).strip()
            relaxed.append(downgraded)
        else:
            relaxed.append(item)
    return relaxed


def validate_workflow_json(
    workflow_json: dict[str, Any],
    *,
    relax_preview_lint: bool = False,
    relax_artifact_gate_lint: bool = False,
) -> WorkflowValidationResult:
    try:
        workflow = Workflow.from_dict(workflow_json)
    except Exception as exc:
        raw_issues = _raw_node_required_field_issues(workflow_json)
        schema_issues = _schema_issues_from_exception(workflow_json, exc)
        return WorkflowValidationResult(
            valid=False,
            errors=[*raw_issues, *schema_issues],
        )

    lint_results = lint_workflow(workflow, known_workflow_ids=_known_workflow_ids())
    if relax_preview_lint and _is_preview_workflow_json(workflow_json):
        lint_results = _relax_preview_lint_issues(workflow, lint_results)
    if relax_artifact_gate_lint:
        lint_results = _relax_artifact_gate_lint_issues(lint_results)
    errors = [item for item in lint_results if item.get("severity") == "error"]
    warnings = [item for item in lint_results if item.get("severity") != "error"]
    return WorkflowValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _is_missing_raw_field(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _raw_node_required_field_issues(workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = workflow_json.get("nodes")
    if not isinstance(nodes, list):
        return []

    issues: list[dict[str, Any]] = []
    for index, raw_node in enumerate(nodes):
        if not isinstance(raw_node, dict):
            continue
        node_type = str(raw_node.get("type") or "python")
        spec = NODE_TYPE_SPECS.get(node_type)
        node_id = str(raw_node.get("id") or f"nodes[{index}]")
        if spec is None:
            issues.append({
                "kind": "node_type_unknown",
                "severity": "error",
                "node_id": node_id,
                "node_type": node_type,
                "message": f"Node {node_id!r} uses unsupported node type {node_type!r}.",
            })
            continue
        missing = [field for field in spec.required_fields if _is_missing_raw_field(raw_node.get(field))]
        if missing:
            issues.append({
                "kind": "node_type_required_fields_missing",
                "severity": "error",
                "node_id": node_id,
                "node_type": node_type,
                "missing_fields": missing,
                "message": f"Node {node_id!r} of type {node_type!r} is missing required fields: {missing}.",
            })
    return issues


def _schema_issues_from_exception(workflow_json: dict[str, Any], exc: Exception) -> list[dict[str, Any]]:
    errors_method = getattr(exc, "errors", None)
    if not callable(errors_method):
        return [{
            "kind": "schema_invalid",
            "severity": "error",
            "message": f"Invalid workflow JSON: {exc}",
        }]

    try:
        raw_errors = errors_method()
    except Exception:
        raw_errors = []
    if not raw_errors:
        return [{
            "kind": "schema_invalid",
            "severity": "error",
            "message": f"Invalid workflow JSON: {exc}",
        }]

    nodes = workflow_json.get("nodes")
    issues: list[dict[str, Any]] = []
    for item in raw_errors:
        loc = item.get("loc") if isinstance(item, dict) else None
        loc_parts = list(loc or [])
        issue: dict[str, Any] = {
            "kind": "schema_invalid",
            "severity": "error",
            "schema_path": ".".join(str(part) for part in loc_parts) if loc_parts else "workflow",
            "message": item.get("msg") if isinstance(item, dict) else str(item),
        }
        if len(loc_parts) >= 2 and loc_parts[0] == "nodes" and isinstance(loc_parts[1], int):
            node_index = loc_parts[1]
            if isinstance(nodes, list) and 0 <= node_index < len(nodes) and isinstance(nodes[node_index], dict):
                issue["node_id"] = nodes[node_index].get("id")
                issue["node_type"] = nodes[node_index].get("type")
        issues.append(issue)
    return issues


def lint_workflow_artifact(workflow_dir_path: Path, workflow_json: dict[str, Any]) -> WorkflowValidationResult:
    result = validate_workflow_json(
        workflow_json,
        relax_preview_lint=True,
        relax_artifact_gate_lint=True,
    )
    if not (workflow_dir_path / "workflow.md").is_file():
        result.warnings.append({
            "kind": "workflow_markdown_missing",
            "severity": "warning",
            "message": "workflow.md is missing; add a human-readable workflow description.",
        })
    return result


def _read_existing_meta(wf_dir: Path) -> dict[str, Any]:
    meta_file = wf_dir / "meta.json"
    if not meta_file.is_file():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_existing_workflow_json(wf_dir: Path) -> Optional[dict[str, Any]]:
    json_file = wf_dir / "workflow.json"
    if not json_file.is_file():
        return None
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _build_meta(
    workflow_id: str,
    wf_dir: Path,
    workflow_json: Optional[dict[str, Any]],
    metadata: Optional[dict[str, Any]],
    *,
    mode: Literal["create", "update"],
    actor: Optional[str],
    validation: Optional[WorkflowValidationResult],
    workflow_json_written: bool = False,
    auto_activate_on_complete: bool = False,
) -> dict[str, Any]:
    now_ms = _now_ms()
    existing = _read_existing_meta(wf_dir)
    requested = dict(metadata or {})
    workflow_json = workflow_json or _read_existing_workflow_json(wf_dir) or {}
    created_at = existing.get("createdAt") or requested.get("createdAt") or now_ms
    created_by = existing.get("createdBy") or requested.get("createdBy") or actor
    requested_status = requested.get("status")
    existing_status = existing.get("status")
    status = requested_status or existing_status or "draft"
    if validation is not None and not validation.valid:
        status = "invalid"
    elif (
        auto_activate_on_complete
        and requested_status is None
        and existing_status in (None, "", "draft")
        and workflow_json_written
        and validation is not None
        and validation.valid
        and not _is_preview_workflow_json(workflow_json)
        and (wf_dir / "workflow.md").is_file()
    ):
        status = "active"

    return {
        **existing,
        **requested,
        "id": workflow_id,
        "name": requested.get("name") or workflow_json.get("name") or existing.get("name") or workflow_id,
        "description": requested.get("description") or workflow_json.get("description") or existing.get("description"),
        "category": requested.get("category") or existing.get("category") or "default",
        "status": status,
        "createdBy": created_by,
        "createdAt": created_at,
        "updatedAt": now_ms,
        "updatedBy": actor or requested.get("updatedBy") or existing.get("updatedBy"),
        "validation": {
            "valid": validation.valid,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "checkedAt": now_ms,
        } if validation is not None else existing.get("validation"),
    }


async def write_workflow_artifact(
    workflow_id: str,
    files: WorkflowArtifactFiles,
    mode: Literal["create", "update"] = "update",
    actor: Optional[str] = None,
    *,
    metadata: Optional[dict[str, Any]] = None,
    event_publisher: Optional[EventPublisher] = None,
    workspace: Optional[Path] = None,
    auto_activate_on_complete: bool = False,
) -> dict[str, Any]:
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        raise WorkflowArtifactError("workflow_id is required")

    wf_dir = workflow_dir(workflow_id, workspace=workspace)
    existing_workflow = _read_existing_workflow_json(wf_dir)
    workflow_json = files.workflow_json if files.workflow_json is not None else existing_workflow
    validation: Optional[WorkflowValidationResult] = None
    if workflow_json is not None:
        validation = lint_workflow_artifact(wf_dir, workflow_json)
        if not validation.valid:
            if event_publisher and files.workflow_json is not None:
                await event_publisher("workflow.lint.completed", {
                    "id": workflow_id,
                    "valid": False,
                    "errors": validation.errors,
                    "warnings": validation.warnings,
                })
            raise WorkflowArtifactError(
                "Workflow lint gate failed; fix all error issues before saving workflow.json.",
                issues=validation.errors,
            )

    wf_dir.mkdir(parents=True, exist_ok=True)
    created = existing_workflow is None and files.workflow_json is not None

    if files.markdown_content is not None:
        _atomic_write_text(wf_dir / "workflow.md", files.markdown_content)
    if files.sample_inputs is not None:
        _atomic_write_json(wf_dir / "sample-inputs.json", files.sample_inputs)
    if files.node_test_results is not None:
        _atomic_write_json(wf_dir / "node-test-results.json", files.node_test_results)
    if files.workflow_json is not None:
        _atomic_write_json(wf_dir / "workflow.json", files.workflow_json)

    meta = _build_meta(
        workflow_id,
        wf_dir,
        workflow_json,
        metadata,
        mode=mode,
        actor=actor,
        validation=validation,
        workflow_json_written=files.workflow_json is not None,
        auto_activate_on_complete=auto_activate_on_complete,
    )
    _atomic_write_json(wf_dir / "meta.json", meta)

    record = read_workflow_dir(wf_dir, workflow_id, "project") or {
        **meta,
        "id": workflow_id,
        "source": "project",
        "workflowJson": workflow_json or {},
        "markdownContent": files.markdown_content,
    }

    if event_publisher and (files.workflow_json is not None or existing_workflow is not None):
        event_name = "workflow.created" if files.workflow_json is not None and (created or mode == "create") else "workflow.updated"
        await event_publisher(event_name, {"id": workflow_id, "name": meta.get("name")})
        if files.workflow_json is not None and validation is not None:
            await event_publisher("workflow.lint.completed", {
                "id": workflow_id,
                "valid": validation.valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
            })
        stage_events: list[str] = []
        if files.markdown_content is not None:
            stage_events.append("workflow_md_written")
        if files.workflow_json is not None:
            stage_events.append("workflow_json_written")
        if files.sample_inputs is not None:
            stage_events.append("sample_inputs_saved")
        if files.node_test_results is not None:
            stage_events.append("node_tests_recorded")
        for stage in stage_events:
            await event_publisher("workflow.builder.stage.changed", {
                "id": workflow_id,
                "stage": stage,
                "skill": "workflow-builder",
            })
        if files.node_test_results is not None:
            await event_publisher("workflow.node_test.completed", {
                "id": workflow_id,
                "results": files.node_test_results,
            })

    log.info("workflow.artifact.written", {
        "id": workflow_id,
        "mode": mode,
        "created": created,
        "has_workflow_json": files.workflow_json is not None,
    })
    return record


def register_workflow_artifact(
    workflow_dir_path: Path,
    source: str = "project",
    actor: Optional[str] = None,
) -> dict[str, Any]:
    workflow_id = workflow_dir_path.name
    data = read_workflow_dir(workflow_dir_path, workflow_id, source)
    if data is None:
        raise WorkflowArtifactError(f"Workflow artifact not found: {workflow_dir_path}")
    validation = validate_workflow_json(data["workflowJson"])
    if not validation.valid:
        raise WorkflowArtifactError("Workflow lint gate failed", issues=validation.errors)
    meta = _build_meta(
        workflow_id,
        workflow_dir_path,
        data["workflowJson"],
        data,
        mode="update",
        actor=actor,
        validation=validation,
    )
    _atomic_write_json(workflow_dir_path / "meta.json", meta)
    refreshed = read_workflow_dir(workflow_dir_path, workflow_id, source)
    return refreshed or {**data, **meta}
