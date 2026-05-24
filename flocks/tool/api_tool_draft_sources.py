from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from flocks.workspace.manager import TEXT_EXTENSIONS, WorkspaceManager


SourceType = Literal[
    "auto",
    "text",
    "openapi",
    "curl",
    "http_example",
    "markdown",
    "pdf",
    "word",
    "excel",
    "office",
    "html",
    "csv",
]

_ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv",
    ".pdf", ".doc", ".docx", ".html", ".htm", ".ppt", ".pptx", ".xls", ".xlsx",
}
_MAX_SOURCE_CHARS = 60_000
_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?)[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
]


class APIToolDraftSource(BaseModel):
    type: Literal["text", "workspace_file"]
    source_type: SourceType = "auto"
    content: Optional[str] = None
    path: Optional[str] = None
    name: Optional[str] = None
    size: Optional[int] = None


class ExtractedDraftSource(BaseModel):
    label: str
    source_type: SourceType
    content: str
    path: Optional[str] = None
    parser: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


def redact_source_secrets(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        def _replace(match: re.Match[str]) -> str:
            if match.lastindex:
                return f"{match.group(1)}<REDACTED>"
            return "<REDACTED>"

        redacted = pattern.sub(_replace, redacted)
    return redacted


def _truncate_source_text(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_SOURCE_CHARS:
        return text, False
    return text[:_MAX_SOURCE_CHARS], True


def _source_type_from_path(path: Path) -> SourceType:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix in {".json", ".yaml", ".yml"}:
        return "openapi"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".doc", ".docx"}:
        return "word"
    if suffix in {".xls", ".xlsx"}:
        return "excel"
    if suffix in {".ppt", ".pptx"}:
        return "office"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".csv":
        return "csv"
    return "text"


def resolve_workspace_source(source: APIToolDraftSource) -> tuple[Path, str]:
    if not source.path:
        raise ValueError("workspace_file source requires path")

    workspace = WorkspaceManager.get_instance()
    workspace_root = workspace.get_workspace_dir().resolve()
    raw_path = Path(source.path).expanduser()

    if raw_path.is_absolute():
        resolved = raw_path.resolve()
        if not resolved.is_relative_to(workspace_root):
            raise ValueError("workspace_file path must stay inside the workspace")
        relative_path = str(resolved.relative_to(workspace_root))
        return resolved, relative_path

    resolved = workspace.resolve_workspace_path(str(raw_path))
    relative_path = str(resolved.resolve().relative_to(workspace_root))
    return resolved, relative_path


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_document_text(path: Path) -> tuple[str, str, list[str]]:
    from flocks.tool.file.doc_parser import SUPPORTED_SUFFIXES, _run_extractors

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported document type: {suffix}")
    return _run_extractors(path)


def _finalize_content(text: str, warnings: list[str]) -> str:
    text = redact_source_secrets(text)
    text, truncated = _truncate_source_text(text)
    if truncated:
        warnings.append(f"source content truncated to {_MAX_SOURCE_CHARS} characters")
    return text.strip()


def extract_source_text(source: APIToolDraftSource) -> ExtractedDraftSource:
    warnings: list[str] = []

    if source.type == "text":
        content = _finalize_content(source.content or "", warnings)
        if not content:
            raise ValueError("text source content is empty")
        return ExtractedDraftSource(
            label="User text",
            source_type=source.source_type if source.source_type != "auto" else "text",
            content=content,
            warnings=warnings,
        )

    path, relative_path = resolve_workspace_source(source)
    if not path.is_file():
        raise ValueError(f"workspace_file source does not exist: {relative_path}")

    suffix = path.suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(f"unsupported workspace_file extension: {suffix}")

    detected_type = _source_type_from_path(path)
    source_type: SourceType = detected_type if source.source_type == "auto" else source.source_type
    parser: Optional[str] = None

    if suffix in TEXT_EXTENSIONS and suffix not in {".html", ".htm"}:
        content = _read_text_file(path)
    else:
        content, parser, extractor_errors = _extract_document_text(path)
        if extractor_errors:
            warnings.extend(extractor_errors)
        if not content.strip():
            detail = "; ".join(extractor_errors) if extractor_errors else "empty document extraction"
            raise ValueError(f"failed to extract document text from {relative_path}: {detail}")

    content = _finalize_content(content, warnings)
    if not content:
        raise ValueError(f"source content is empty: {relative_path}")

    return ExtractedDraftSource(
        label=f"Workspace file: {relative_path}",
        source_type=source_type,
        content=content,
        path=relative_path,
        parser=parser,
        warnings=warnings,
    )


def merge_source_contexts(sources: list[ExtractedDraftSource]) -> str:
    blocks: list[str] = []
    for source in sources:
        blocks.append(f"[{source.label}]\n{source.content}")
    return "\n\n".join(blocks).strip()


def extract_and_merge_sources(sources: list[APIToolDraftSource]) -> tuple[str, list[ExtractedDraftSource], list[str]]:
    extracted: list[ExtractedDraftSource] = []
    warnings: list[str] = []
    for source in sources:
        item = extract_source_text(source)
        extracted.append(item)
        warnings.extend(item.warnings)

    context = merge_source_contexts(extracted)
    if not context:
        raise ValueError("draft source content is empty")
    return context, extracted, warnings
