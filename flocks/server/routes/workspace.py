"""
Workspace routes

Generic file-manager API for ~/.flocks/workspace/ plus a read-only view
of the agent memory directory (~/.flocks/data/memory/).

All `path` query/body parameters are **relative** to the respective root.
Absolute paths and path-traversal attempts are rejected with 400.

Endpoints
---------
Directory operations (workspace only)
  GET  /api/workspace/tree        list directory tree
  GET  /api/workspace/list        list single directory level
  POST /api/workspace/dir         create directory
  DELETE /api/workspace/dir       delete directory (recursive)

File operations (workspace only)
  POST   /api/workspace/upload        upload file(s)
  GET    /api/workspace/file          read text file content
  PUT    /api/workspace/file          write / update text file content
  DELETE /api/workspace/file          delete file
  GET    /api/workspace/download      download single file
  POST   /api/workspace/download/zip  batch download as zip
  POST   /api/workspace/move          move / rename

Memory view (read-only, points to data/memory/)
  GET /api/workspace/memory/list  list memory files
  GET /api/workspace/memory/file  read memory file content

Stats
  GET /api/workspace/stats        workspace + memory totals
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import stat as stat_module
import zipfile
from pathlib import Path
from typing import List, Optional, Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from flocks.workspace.manager import WorkspaceManager
from flocks.workspace.models import WorkspaceNode, WorkspaceStats
from flocks.utils.log import Log
# 记忆按账号隔离新增
from flocks.auth.context import get_current_auth_user
from flocks.memory.manager import _safe_scope_segment
# -----------end-------------------
router = APIRouter()
log = Log.create(service="workspace.routes")

# Upload size limit read at request time so env-var changes take effect
# without restarting the process.
_DEFAULT_MAX_UPLOAD_MB = 100
_ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv",
    ".pdf", ".doc", ".docx", ".html", ".htm", ".ppt", ".pptx", ".xls", ".xlsx",
}
_ALLOWED_UPLOAD_LABEL = (
    "txt, md, json, yaml, yml, xml, csv, pdf, doc, docx, html, htm, ppt, pptx, xls, xlsx"
)
_MAX_UPLOAD_RENAME_ATTEMPTS = 100


def _max_upload_bytes() -> int:
    return int(os.getenv("FLOCKS_WORKSPACE_MAX_UPLOAD_MB", str(_DEFAULT_MAX_UPLOAD_MB))) * 1024 * 1024


# ─── helpers ────────────────────────────────────────────────────────────────

def _get_manager() -> WorkspaceManager:
    mgr = WorkspaceManager.get_instance()
    mgr.ensure_dirs()
    return mgr


def _is_allowed_upload_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in _ALLOWED_UPLOAD_EXTENSIONS


def _resolve_upload_target(dest_dir: Path, filename: str, *, auto_rename: bool) -> Path:
    candidate = dest_dir / Path(filename).name
    if not auto_rename or not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while counter <= _MAX_UPLOAD_RENAME_ATTEMPTS:
        renamed = dest_dir / f"{stem} ({counter}){suffix}"
        if not renamed.exists():
            return renamed
        counter += 1

    raise ValueError(
        f"Too many conflicting filenames for upload: {filename}. "
        "Please rename the file and try again."
    )


def _sandbox_upload_path(relative_path: str) -> str | None:
    parts = Path(relative_path).parts
    if len(parts) >= 4 and parts[0] == "uploads" and parts[1] == "chat":
        return "/" + "/".join(("workspace", *parts))
    return None


def _is_chat_upload_dest(path: str) -> bool:
    parts = Path(path).parts
    return len(parts) >= 3 and parts[0] == "uploads" and parts[1] == "chat"


def _safe_session_component(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    safe = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_"
        for ch in raw
    ).strip("._-")
    return safe or None


def _request_upload_session_id(request: Request, dest: str) -> str | None:
    for key in ("sessionID", "sessionId", "session_id", "session"):
        value = request.query_params.get(key)
        if value:
            return _safe_session_component(value)

    for key in ("x-flocks-session-id", "x-flocks-sessionid"):
        value = request.headers.get(key)
        if value:
            return _safe_session_component(value)

    parts = Path(dest).parts
    if len(parts) >= 3 and parts[0] == "uploads" and parts[1] == "chat":
        return _safe_session_component(parts[2])
    return None


def _resolve_chat_upload_dest(dest: str, session_id: str | None) -> str:
    if _is_chat_upload_dest(dest):
        return dest
    if session_id and dest in {"", "upload", "uploads", "output", "outputs"}:
        return f"uploads/chat/{session_id}"
    raise ValueError("Chat uploads must be stored under uploads/chat/<session_id>")


def _normalize_workspace_path(path: str | None) -> str:
    """Accept sandbox-visible /workspace paths from tool output in the UI API."""

    if not path:
        return ""
    raw = str(path).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]

    # Some integrations mistakenly prepend the project/session directory to
    # sandbox-visible paths such as /workspace/outputs/... . Treat the
    # sandbox-visible segment as authoritative so downloads still work.
    for marker, replacement in (
        ("/workspace/uploads/chat/", "uploads/chat/"),
        ("/workspace/outputs/", "outputs/")
    ):
        marker_index = raw.find(marker)
        if marker_index >= 0:
            return replacement + raw[marker_index + len(marker):]

    if raw in {"/workspace", "workspace"}:
        return ""
    if raw.startswith("/workspace/"):
        raw = raw[len("/workspace/"):]
    elif raw.startswith("workspace/"):
        raw = raw[len("workspace/"):]
    if raw == "upload":
        raw = "uploads"
    elif raw.startswith("upload/"):
        raw = "uploads/" + raw[len("upload/"):]
    if raw.startswith("output/"):
        raw = "outputs/" + raw[len("output/"):]
    elif raw == "output":
        raw = "outputs"
    return raw


def _is_session_scoped_outputs_path(path: str) -> bool:
    parts = Path(path).parts
    return (
        len(parts) >= 3
        and parts[0] == "outputs"
        and len(parts[1]) == 10
        and parts[1][4] == "-"
        and parts[1][7] == "-"
        and parts[2].startswith("ses_")
    )


def _resolve_workspace_api_path(mgr: WorkspaceManager, path: str) -> Path:
    """Resolve UI/API paths, including sandbox aliases for user artifacts."""

    normalized = _normalize_workspace_path(path)
    if _is_chat_upload_dest(normalized) or _is_session_scoped_outputs_path(normalized):
        return mgr.resolve_user_workspace_path(normalized)
    return mgr.resolve_workspace_path(normalized)


def _display_root_for_path(mgr: WorkspaceManager, path: Path) -> Path:
    user_workspace = mgr.get_user_workspace_dir().resolve()
    try:
        if path.resolve().is_relative_to(user_workspace):
            return user_workspace
    except ValueError:
        pass
    return mgr.get_workspace_dir()


def _node_from_path(path: Path, root: Path) -> WorkspaceNode:
    """Build a WorkspaceNode from a filesystem path.

    Uses a single stat() call; type is derived from st_mode to avoid the
    extra syscall that Path.is_dir() would issue internally.
    """
    raw_stat = path.stat()
    is_dir = stat_module.S_ISDIR(raw_stat.st_mode)

    # Root node: relative_to returns Path('.'); normalise to '' so the
    # frontend can use it directly as an API path parameter.
    rel = str(path.relative_to(root))
    if rel == ".":
        rel = ""

    if is_dir:
        return WorkspaceNode(
            name=path.name,
            path=rel,
            type="directory",
            modified_at=raw_stat.st_mtime,
        )
    return WorkspaceNode(
        name=path.name,
        path=rel,
        type="file",
        size=raw_stat.st_size,
        modified_at=raw_stat.st_mtime,
        is_text_file=WorkspaceManager.is_text_file(path),
    )


def _build_tree_sync(directory: Path, root: Path, depth: int, current: int = 0) -> WorkspaceNode:
    """Blocking recursive tree build — call via asyncio.to_thread."""
    node = _node_from_path(directory, root)
    if current < depth:
        node.children = []
        for child in sorted(directory.iterdir()):
            if child.is_dir():
                node.children.append(_build_tree_sync(child, root, depth, current + 1))
            else:
                node.children.append(_node_from_path(child, root))
    return node


def _list_dir_sync(directory: Path, root: Path) -> list[WorkspaceNode]:
    """Blocking single-level directory listing — call via asyncio.to_thread."""
    return [_node_from_path(child, root) for child in sorted(directory.iterdir())]


def _dir_stats_sync(root: Path):
    """Blocking directory walk — call via asyncio.to_thread in async context."""
    file_count = 0
    dir_count = 0
    total_size = 0
    for item in root.rglob("*"):
        if item.is_file():
            file_count += 1
            total_size += item.stat().st_size
        elif item.is_dir():
            dir_count += 1
    return file_count, dir_count, total_size


# ─── directory operations ───────────────────────────────────────────────────

@router.get("/tree", response_model=WorkspaceNode, summary="List directory tree")
async def list_tree(
    path: str = Query("", description="Relative path from workspace root"),
    depth: int = Query(2, ge=1, le=5, description="Tree depth"),
):
    mgr = _get_manager()
    path = _normalize_workspace_path(path)
    try:
        base = _resolve_workspace_api_path(mgr, path) if path else mgr.get_workspace_dir()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not base.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return await asyncio.to_thread(_build_tree_sync, base, _display_root_for_path(mgr, base), depth)


@router.get("/list", response_model=List[WorkspaceNode], summary="List directory")
async def list_dir(
    path: str = Query("", description="Relative path from workspace root"),
):
    mgr = _get_manager()
    path = _normalize_workspace_path(path)
    try:
        base = _resolve_workspace_api_path(mgr, path) if path else mgr.get_workspace_dir()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not base.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return await asyncio.to_thread(_list_dir_sync, base, _display_root_for_path(mgr, base))


class DirCreateRequest(BaseModel):
    path: str


@router.post("/dir", summary="Create directory")
async def create_dir(body: DirCreateRequest):
    mgr = _get_manager()
    path = _normalize_workspace_path(body.path)
    try:
        target = _resolve_workspace_api_path(mgr, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    target.mkdir(parents=True, exist_ok=True)
    log.info("workspace.dir.created", {"path": path})
    return {"path": path, "created": True}


@router.delete("/dir", summary="Delete directory")
async def delete_dir(
    path: str = Query(..., description="Relative path to directory"),
):
    mgr = _get_manager()
    path = _normalize_workspace_path(path)
    try:
        target = _resolve_workspace_api_path(mgr, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Existence check first — resolve() on a non-existent path may behave
    # differently across OS; checking exists() before comparing is safer.
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    # Protect workspace root after confirming it exists
    if target.resolve() == mgr.get_workspace_dir().resolve():
        raise HTTPException(status_code=400, detail="Cannot delete workspace root")
    shutil.rmtree(target)
    log.info("workspace.dir.deleted", {"path": path})
    return {"path": path, "deleted": True}


# ─── file operations ────────────────────────────────────────────────────────

@router.post("/upload", summary="Upload file(s)")
async def upload_files(
    request: Request,
    dest: str = Query("", description="Destination directory (relative)"),
    purpose: Optional[Literal["chat"]] = Query(None, description="Upload purpose"),
    files: List[UploadFile] = File(...),
):
    mgr = _get_manager()
    dest = _normalize_workspace_path(dest)
    upload_session_id = _request_upload_session_id(request, dest)
    is_chat_upload = purpose == "chat" or bool(upload_session_id) or _is_chat_upload_dest(dest)
    try:
        if is_chat_upload:
            dest = _resolve_chat_upload_dest(dest, upload_session_id)
            dest_dir = mgr.resolve_user_workspace_path(dest)
            workspace_root = mgr.get_user_workspace_dir()
        else:
            dest_dir = mgr.resolve_workspace_path(dest) if dest else mgr.get_workspace_dir()
            workspace_root = mgr.get_workspace_dir()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dest_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = _max_upload_bytes()
    max_mb = max_bytes // (1024 * 1024)

    results = []
    conflict_detail: str | None = None
    for upload in files:
        raw_name: Optional[str] = upload.filename
        if not raw_name:
            results.append({"name": "", "error": "Filename is missing"})
            continue

        filename = Path(raw_name).name  # strip any dir component from client
        if is_chat_upload and not _is_allowed_upload_filename(filename):
            results.append({
                "name": filename,
                "error": f"Unsupported file type (allowed: {_ALLOWED_UPLOAD_LABEL})",
            })
            continue

        # Read file in chunks to enforce size limit without loading entire
        # content into memory before checking.
        chunks: list[bytes] = []
        total = 0
        too_large = False
        while True:
            chunk = await upload.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                too_large = True
                break
            chunks.append(chunk)

        if too_large:
            results.append({"name": raw_name, "error": f"File too large (max {max_mb} MB)"})
            continue

        content = b"".join(chunks)
        try:
            target = _resolve_upload_target(dest_dir, filename, auto_rename=is_chat_upload)
        except ValueError as exc:
            message = str(exc)
            results.append({"name": filename, "error": message})
            conflict_detail = message
            continue
        target.write_bytes(content)

        is_text = WorkspaceManager.is_text_file(target)
        log.info("workspace.file.uploaded", {
            "name": target.name,
            "size": total,
            "dest": dest,
            "purpose": "chat" if is_chat_upload else purpose,
            "is_text": is_text,
        })
        rel_path = str(target.relative_to(workspace_root))
        results.append({
            "name": target.name,
            "path": rel_path,
            "sandbox_path": _sandbox_upload_path(rel_path),
            "abs_path": str(target),
            "size": total,
            "is_text_file": is_text,
            "preview_warning": None if is_text else "Binary file — download only",
        })

    if conflict_detail is not None:
        return JSONResponse(status_code=409, content={"detail": conflict_detail, "uploaded": results})

    return {"uploaded": results}


@router.get("/file", summary="Read text file content")
async def read_file(
    path: str = Query(..., description="Relative path to file"),
):
    mgr = _get_manager()
    path = _normalize_workspace_path(path)
    try:
        target = _resolve_workspace_api_path(mgr, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    if not WorkspaceManager.is_text_file(target):
        raise HTTPException(
            status_code=400,
            detail="Binary file — use /download endpoint instead",
        )
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"path": path, "content": content}


class FileWriteRequest(BaseModel):
    path: str
    content: str


@router.put("/file", summary="Write file content")
async def write_file(body: FileWriteRequest):
    mgr = _get_manager()
    path = _normalize_workspace_path(body.path)
    try:
        target = _resolve_workspace_api_path(mgr, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(body.content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    log.info("workspace.file.written", {"path": path, "size": len(body.content)})
    return {"path": path, "written": True}


@router.delete("/file", summary="Delete file")
async def delete_file(
    path: str = Query(..., description="Relative path to file"),
):
    mgr = _get_manager()
    path = _normalize_workspace_path(path)
    try:
        target = _resolve_workspace_api_path(mgr, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    target.unlink()
    log.info("workspace.file.deleted", {"path": path})
    return {"path": path, "deleted": True}


@router.get("/download", summary="Download single file")
async def download_file(
    path: str = Query(..., description="Relative path to file"),
):
    mgr = _get_manager()
    path = _normalize_workspace_path(path)
    try:
        target = _resolve_workspace_api_path(mgr, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


class ZipDownloadRequest(BaseModel):
    paths: List[str]
    archive_name: str = "workspace_files.zip"


@router.post("/download/zip", summary="Batch download as zip")
async def download_zip(body: ZipDownloadRequest):
    mgr = _get_manager()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in body.paths:
            rel_path = _normalize_workspace_path(rel_path)
            try:
                target = _resolve_workspace_api_path(mgr, rel_path)
            except ValueError:
                continue
            if target.is_file():
                zf.write(target, arcname=rel_path)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{body.archive_name}"'},
    )


class MoveRequest(BaseModel):
    src: str
    dst: str


@router.post("/move", summary="Move / rename file or directory")
async def move_item(body: MoveRequest):
    mgr = _get_manager()
    src_path = _normalize_workspace_path(body.src)
    dst_path = _normalize_workspace_path(body.dst)
    try:
        src = mgr.resolve_workspace_path(src_path)
        dst = mgr.resolve_workspace_path(dst_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {src_path}")
    if dst.exists():
        raise HTTPException(status_code=409, detail=f"Destination already exists: {dst_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    log.info("workspace.item.moved", {"src": src_path, "dst": dst_path})
    return {"src": src_path, "dst": dst_path, "moved": True}


# ─── memory view (read-only) ────────────────────────────────────────────────

def _list_memory_sync(memory_dir: Path) -> List[WorkspaceNode]:
    """Blocking directory scan — call via asyncio.to_thread in async context."""
    nodes: List[WorkspaceNode] = []
    for item in sorted(memory_dir.rglob("*")):
        if item.is_file():
            rel = str(item.relative_to(memory_dir))
            st = item.stat()
            nodes.append(WorkspaceNode(
                name=item.name,
                path=rel,
                type="file",
                size=st.st_size,
                modified_at=st.st_mtime,
                is_text_file=WorkspaceManager.is_text_file(item),
            ))
    return nodes

# 记忆按账号隔离新增
def _current_memory_base(mgr: WorkspaceManager) -> Path:
    memory_dir = mgr.get_memory_dir()
    user = get_current_auth_user()
    if user and user.role != "admin":
        return memory_dir / "users" / _safe_scope_segment(user.id)
    return memory_dir


def _resolve_visible_memory_path(mgr: WorkspaceManager, path: str) -> Path:
    user = get_current_auth_user()
    if user and user.role != "admin":
        scoped = _safe_scope_segment(user.id)
        normalized = path.replace("\\", "/").lstrip("/")
        own_prefix = f"users/{scoped}/"
        if normalized.startswith("users/") and not normalized.startswith(own_prefix):
            raise PermissionError("Memory path belongs to a different account scope")
        if normalized.startswith(own_prefix):
            return mgr.resolve_memory_path(normalized)
        return mgr.resolve_memory_path(f"{own_prefix}{normalized}")
    return mgr.resolve_memory_path(path)

# ----------------------end-------------------------

def _current_memory_base(mgr: WorkspaceManager) -> Path:
    memory_dir = mgr.get_memory_dir()
    user = get_current_auth_user()
    if user and user.role != "admin":
        return memory_dir / "users" / _safe_scope_segment(user.id)
    return memory_dir


def _resolve_visible_memory_path(mgr: WorkspaceManager, path: str) -> Path:
    user = get_current_auth_user()
    if user and user.role != "admin":
        scoped = _safe_scope_segment(user.id)
        normalized = path.replace("\\", "/").lstrip("/")
        own_prefix = f"users/{scoped}/"
        if normalized.startswith("users/") and not normalized.startswith(own_prefix):
            raise PermissionError("Memory path belongs to a different account scope")
        if normalized.startswith(own_prefix):
            return mgr.resolve_memory_path(normalized)
        return mgr.resolve_memory_path(f"{own_prefix}{normalized}")
    return mgr.resolve_memory_path(path)


@router.get("/memory/list", response_model=List[WorkspaceNode], summary="List memory files")
async def list_memory():
    mgr = _get_manager()
    # 记忆按账号隔离修改
    # 删除
    '''
    memory_dir = mgr.get_memory_dir()
    if not memory_dir.exists():
        return []
    return await asyncio.to_thread(_list_memory_sync, memory_dir)
    '''
    # 新增
    memory_dir = _current_memory_base(mgr)
    if not memory_dir.exists():
        return []
    return await asyncio.to_thread(_list_memory_sync, memory_dir)
    # -------------end-------------------------

@router.get("/memory/file", summary="Read memory file content")
async def read_memory_file(
    path: str = Query(..., description="Relative path inside memory directory"),
):
    mgr = _get_manager()
    # 记忆按账号隔离修改
    # 删除
    '''
    try:
        target = mgr.resolve_memory_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    '''
    # 新增
    try:
        target = _resolve_visible_memory_path(mgr, path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # ------------end-------------------------
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Memory file not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"path": path, "content": content}


# ─── stats ──────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=WorkspaceStats, summary="Workspace statistics")
async def get_stats():
    mgr = _get_manager()
    ws_dir = mgr.get_workspace_dir()
    # 记忆按账号隔离修改
    # mem_dir = mgr.get_memory_dir()
    mem_dir = _current_memory_base(mgr)

    fc, dc, ts = await asyncio.to_thread(_dir_stats_sync, ws_dir) if ws_dir.exists() else (0, 0, 0)
    mfc, _, mts = await asyncio.to_thread(_dir_stats_sync, mem_dir) if mem_dir.exists() else (0, 0, 0)

    return WorkspaceStats(
        file_count=fc,
        dir_count=dc,
        total_size_bytes=ts,
        memory_file_count=mfc,
        memory_total_size_bytes=mts,
    )
