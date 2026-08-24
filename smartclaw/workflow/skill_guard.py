"""Workflow-builder skill guard for workflow artifact writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from smartclaw.tool.registry import ToolContext
from smartclaw.utils.log import Log


log = Log.create(service="workflow.skill-guard")

WORKFLOW_BUILDER_SKILL = "workflow-builder"
WORKFLOW_REQUIRED_SKILLS = [WORKFLOW_BUILDER_SKILL]
WORKFLOW_ARTIFACT_NAMES = {
    "workflow.json",
    "workflow.md",
    "sample-inputs.json",
    "node-test-results.json",
}


def _normalize_skill_name(value: object) -> str:
    return str(value or "").strip().lower()


def workflow_session_metadata(
    category: Optional[str],
    metadata: Optional[dict[str, Any]] = None,
    *,
    default_mode: str = "create",
) -> dict[str, Any]:
    result = dict(metadata or {})
    if str(category or "").strip().lower() != "workflow":
        return result

    result.setdefault("workflowMode", default_mode)
    required = result.get("requiredSkills")
    if not isinstance(required, list):
        required = []
    normalized = {_normalize_skill_name(item) for item in required}
    if WORKFLOW_BUILDER_SKILL not in normalized:
        required.append(WORKFLOW_BUILDER_SKILL)
    result["requiredSkills"] = required
    result.setdefault("loadedSkills", [])
    return result


def is_workflow_artifact_path(filepath: str | Path) -> bool:
    path = Path(filepath).expanduser()
    if path.name not in WORKFLOW_ARTIFACT_NAMES:
        return False
    parts = tuple(part.lower() for part in path.parts)
    for index in range(len(parts) - 2):
        if parts[index:index + 3] == (".smartclaw", "plugins", "workflows"):
            return len(parts) > index + 4
    return False


def _loaded_skills_from_metadata(metadata: dict[str, Any]) -> set[str]:
    raw = metadata.get("loadedSkills")
    if not isinstance(raw, list):
        return set()
    return {_normalize_skill_name(item) for item in raw if _normalize_skill_name(item)}


def _requires_workflow_builder(session: Any, filepath: str | Path) -> bool:
    if is_workflow_artifact_path(filepath):
        return True
    if session is None:
        return False
    metadata = getattr(session, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    category = str(getattr(session, "category", "") or "").strip().lower()
    mode = str(metadata.get("workflowMode") or "").strip().lower()
    required = metadata.get("requiredSkills")
    required_set = {
        _normalize_skill_name(item)
        for item in required
        if _normalize_skill_name(item)
    } if isinstance(required, list) else set()
    return (
        category == "workflow"
        or mode in {"create", "edit"}
        or WORKFLOW_BUILDER_SKILL in required_set
    )


async def mark_workflow_builder_loaded(ctx: ToolContext, skill_name: str) -> None:
    if _normalize_skill_name(skill_name) != WORKFLOW_BUILDER_SKILL:
        return
    try:
        from smartclaw.session.session import Session

        session = await Session.get_by_id(ctx.session_id)
        if not session:
            return
        metadata = workflow_session_metadata(
            getattr(session, "category", None),
            getattr(session, "metadata", None),
        )
        loaded = metadata.get("loadedSkills")
        if not isinstance(loaded, list):
            loaded = []
        loaded_set = {_normalize_skill_name(item) for item in loaded}
        if WORKFLOW_BUILDER_SKILL not in loaded_set:
            loaded.append(WORKFLOW_BUILDER_SKILL)
        metadata["loadedSkills"] = loaded
        await Session.update(session.project_id, session.id, metadata=metadata)
        if isinstance(ctx.extra, dict):
            ctx.extra["session_metadata"] = metadata
            ctx.extra["loadedSkills"] = loaded
    except Exception as exc:
        log.warning("workflow.skill.loaded.record_failed", {
            "session_id": getattr(ctx, "session_id", None),
            "error": str(exc),
        })


async def require_workflow_builder_for_artifact(ctx: ToolContext, filepath: str | Path) -> Optional[str]:
    if not is_workflow_artifact_path(filepath):
        return None

    session = None
    metadata: dict[str, Any] = {}
    try:
        from smartclaw.session.session import Session

        session = await Session.get_by_id(ctx.session_id)
        if session is not None and isinstance(getattr(session, "metadata", None), dict):
            metadata = dict(session.metadata)
    except Exception as exc:
        log.warning("workflow.skill.guard.session_lookup_failed", {
            "session_id": getattr(ctx, "session_id", None),
            "path": str(filepath),
            "error": str(exc),
        })

    if isinstance(ctx.extra, dict):
        extra_metadata = ctx.extra.get("session_metadata")
        if isinstance(extra_metadata, dict):
            metadata.update(extra_metadata)
        extra_loaded = ctx.extra.get("loadedSkills")
        if isinstance(extra_loaded, list):
            metadata.setdefault("loadedSkills", extra_loaded)

    if not _requires_workflow_builder(session, filepath):
        return None
    if WORKFLOW_BUILDER_SKILL in _loaded_skills_from_metadata(metadata):
        return None

    return (
        "需要先加载 workflow-builder skill 后再写入 workflow 文件。"
        "请先调用 skill(name=\"workflow-builder\")，再重试写入 workflow artifact。"
    )
