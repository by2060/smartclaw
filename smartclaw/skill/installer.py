"""
Skill Installer

Handles:
1. Installing skills from external sources (GitHub, raw URL, local path, clawhub)
2. Installing a skill's declared tool dependencies (brew, npm, uv, pip, go)

Source scheme routing:
  safeskill:<name>         → SafeSkill registry API (reserved for future)
  clawhub:<name>           → clawhub.com registry API
  github:<owner>/<repo>    → GitHub raw download
  https://...              → Direct HTTP download
  /local/path or ./path    → Local filesystem copy
  <owner>/<repo>           → Shorthand for GitHub
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional

from smartclaw.skill.skill import Skill, SkillInfo, SkillInstallSpec
from smartclaw.utils.log import Log


log = Log.create(service="skill.installer")

# skill的安装接口改造添加
def _managed_marker_name(skill_name: str) -> str:
    return f".{skill_name}.skill.json"


def _managed_marker_path(skill_dir: Path, skill_name: str) -> Path:
    return skill_dir / _managed_marker_name(skill_name)
# ---------------end---------------------------

# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------

@dataclass
class SkillInstallResult:
    success: bool
    skill_name: Optional[str] = None
    location: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


@dataclass
class DepInstallResult:
    success: bool
    spec_id: Optional[str] = None
    command: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: Optional[str] = None
    message: str = ""


# ---------------------------------------------------------------------------
# Source Resolution
# ---------------------------------------------------------------------------

def _user_skills_root() -> Path:
    return Path.home() / ".smartclaw" / "plugins" / "skills"

# skill输出到项目级插件路径修改
def _project_skills_root() -> Path:
    from smartclaw.project.instance import Instance  # avoid circular import
    project_dir = Instance.get_directory() or os.getcwd()
    return Path(project_dir) / ".smartclaw" / "plugins" / "skills"


def _resolve_install_root(scope: str) -> Path:
    """Return the install root directory for the given scope."""
    return _project_skills_root()


def _resolve_source(source: str) -> dict:
    """
    Parse source string into a typed dict with keys: kind, value.

    Supported kinds:
      safeskill  – reserved, future SafeSkill registry
      clawhub    – clawhub.com registry
      github     – GitHub raw download
      url        – arbitrary HTTPS URL
      local      – local filesystem path
    """
    source = source.strip()

    if source.startswith("safeskill:"):
        return {"kind": "safeskill", "value": source[len("safeskill:"):]}

    if source.startswith("clawhub:"):
        return {"kind": "clawhub", "value": source[len("clawhub:"):]}

    if source.startswith("github:"):
        return {"kind": "github", "value": source[len("github:"):]}
    # skill的安装接口改造添加
    if source.startswith("workspace:"):
        return {"kind": "workspace", "value": source[len("workspace:"):].lstrip("/\\")}
    # ---------------------end-------------------------------
    if source.startswith(("http://", "https://")):
        # Detect GitHub URLs and handle them specially
        gh_match = re.match(
            r"https?://github\.com/([^/]+/[^/]+)(?:/tree/[^/]+)?(/.*)?$",
            source,
        )
        if gh_match:
            repo = gh_match.group(1).rstrip("/")
            subpath = (gh_match.group(2) or "").strip("/")
            return {"kind": "github", "value": f"{repo}/{subpath}" if subpath else repo}
        return {"kind": "url", "value": source}

    if source.startswith(("/", "./", "../", "~/")) or re.match(r"^[a-zA-Z]:[\\/]", source):
        return {"kind": "local", "value": os.path.expanduser(source)}

    # skill的安装接口改造添加
    if source.startswith("uploads/") or source.startswith("uploads\\"):
        return {"kind": "workspace", "value": source}

    # Bare "owner/repo" or "owner/repo/subpath" shorthand → GitHub
    if re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+$", source):
        return {"kind": "github", "value": source}

    return {"kind": "url", "value": source}


# ---------------------------------------------------------------------------
# Skill Installer
# ---------------------------------------------------------------------------

class SkillInstaller:
    """Install skills from external sources and manage skill dependencies."""

    # ------------------------------------------------------------------
    # Install skill itself
    # ------------------------------------------------------------------

    # skill安装接口改造添加
    @staticmethod
    def _resolve_workspace_source(path: str) -> Path:
        from smartclaw.workspace.manager import WorkspaceManager

        mgr = WorkspaceManager.get_instance()
        mgr.ensure_dirs()
        return mgr.resolve_workspace_path(path)

    @staticmethod
    def _remove_empty_upload_parents(start_dir: Path) -> None:
        try:
            from smartclaw.workspace.manager import WorkspaceManager

            mgr = WorkspaceManager.get_instance()
            workspace = mgr.get_workspace_dir().resolve()
            uploads_root = (workspace / "uploads" / "skills").resolve()
            current = start_dir.resolve()
            while current != uploads_root and current.is_relative_to(uploads_root):
                try:
                    current.rmdir()
                except OSError:
                    break
                current = current.parent
        except Exception:
            return

    @classmethod
    def _write_managed_marker(
        cls,
        skill_dir: Path,
        skill_name: str,
        source: Optional[str],
        deletable: Optional[bool],
    ) -> None:
        marker = _managed_marker_path(skill_dir, skill_name)
        if deletable is True:
            payload = {
                "name": skill_name,
                "managed_by": "api-install",
                "deletable": True,
                "source": source or "",
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
            marker.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        elif marker.exists():
            marker.unlink()

    @classmethod
    def is_deletable_project_skill(cls, skill: SkillInfo) -> bool:
        skill_dir = Path(skill.location).parent
        marker = _managed_marker_path(skill_dir, skill.name)
        if not marker.exists():
            return False
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            return False
        return data.get("name") == skill.name and data.get("deletable") is True
    # -----------------------end---------------------------------
    @classmethod
    async def install_from_source(
        cls,
        source: str,
        scope: str = "project",
        # skill删除接口改造添加
        deletable: Optional[bool] = None,
    ) -> SkillInstallResult:
        """
        Install a skill from an external source.

        Args:
            source: Source string (URL, GitHub, clawhub:<name>, local path …)
            scope:  "global" → ~/.smartclaw/plugins/skills/
                    "project" → .smartclaw/plugins/skills/ (cwd)

        Returns:
            SkillInstallResult
        """
        resolved = _resolve_source(source)
        kind = resolved["kind"]
        value = resolved["value"]

        log.info("skill.install.start", {"source": source, "kind": kind, "scope": scope})
        # skill安装接口改造修改
        cleanup_path: Optional[Path] = None

        try:
            if kind == "safeskill":
                result = await cls._install_from_safeskill(value, scope)
            elif kind == "clawhub":
                result = await cls._install_from_clawhub(value, scope, deletable=deletable, source=source)
            elif kind == "github":
                result = await cls._install_from_github(value, scope, deletable=deletable, source=source)
            elif kind == "url":
                result = await cls._install_from_url(
                    value,
                    scope,
                    skill_name_hint=None,
                    source=source,
                    deletable=deletable,
                )
            elif kind == "workspace":
                workspace_path = cls._resolve_workspace_source(value)
                if workspace_path.suffix.lower() == ".zip":
                    cleanup_path = workspace_path
                result = await cls._install_from_local(
                    str(workspace_path),
                    scope,
                    deletable=deletable,
                    source=source,
                )
            elif kind == "local":
                local_path = Path(value).expanduser()
                if local_path.suffix.lower() == ".zip":
                    cleanup_path = local_path
                result = await cls._install_from_local(
                    value,
                    scope,
                    deletable=deletable,
                    source=source,
                )
            else:
                result = SkillInstallResult(
                    success=False,
                    error=f"Unsupported source kind: {kind}",
                )
            return result
        finally:
            if cleanup_path and cleanup_path.exists() and cleanup_path.is_file():
                try:
                    cleanup_path.unlink()
                    cls._remove_empty_upload_parents(cleanup_path.parent)
                    log.info("skill.install.source_zip.cleaned", {"path": str(cleanup_path)})
                except Exception as exc:
                    log.warn("skill.install.source_zip.cleanup_failed", {
                        "path": str(cleanup_path),
                        "error": str(exc),
                    })

    @classmethod
    async def _install_from_safeskill(cls, name: str, scope: str) -> SkillInstallResult:
        """Reserved for future SafeSkill registry integration."""
        return SkillInstallResult(
            success=False,
            error=(
                "SafeSkill registry is not yet available. "
                "Use a GitHub URL or clawhub:<name> instead."
            ),
        )

    @classmethod
    async def _install_from_clawhub(
        cls,
        name: str,
        scope: str,
        deletable: Optional[bool] = None,
        source: Optional[str] = None,
    ) -> SkillInstallResult:
        """Download a skill from clawhub.ai registry (ZIP bundle)."""
        try:
            import httpx
        except ImportError:
            return SkillInstallResult(
                success=False,
                error="httpx is required to download skills. Run: uv add httpx",
            )

        zip_url = f"https://wry-manatee-359.convex.site/api/v1/download?slug={name}"
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(zip_url)
                if resp.status_code == 404:
                    return SkillInstallResult(
                        success=False,
                        error=(
                            f"Could not find skill '{name}' on clawhub. "
                            "Try using a direct GitHub URL instead."
                        ),
                    )
                if resp.status_code != 200:
                    return SkillInstallResult(
                        success=False,
                        error=f"clawhub returned HTTP {resp.status_code} for skill '{name}'",
                    )
                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type:
                    return SkillInstallResult(
                        success=False,
                        error=(
                            f"Could not find skill '{name}' on clawhub. "
                            "Try using a direct GitHub URL instead."
                        ),
                    )
                zip_bytes = resp.content
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Download failed: {exc}")

        return cls._install_from_zip_bytes(
            zip_bytes,
            scope,
            source=source or f"clawhub:{name}",
            deletable=deletable,
            label=f"clawhub skill '{name}'",
        )

    @classmethod
    async def _install_from_github(
        cls,
        repo_path: str,
        scope: str,
        deletable: Optional[bool] = None,
        source: Optional[str] = None,
    ) -> SkillInstallResult:
        """
        Download an entire skill directory from a GitHub repository using the
        GitHub Contents API, preserving the full folder structure.

        repo_path formats:
          owner/repo               → downloads repo root
          owner/repo/subpath       → downloads subpath/ directory
        """
        try:
            import httpx
        except ImportError:
            return SkillInstallResult(
                success=False,
                error="httpx is required to download skills. Run: uv add httpx",
            )

        parts = repo_path.strip("/").split("/")
        if len(parts) < 2:
            return SkillInstallResult(
                success=False,
                error=f"Invalid GitHub repo path: {repo_path!r}. Expected owner/repo[/subpath]",
            )

        owner, repo = parts[0], parts[1]
        subpath = "/".join(parts[2:]) if len(parts) > 2 else ""

        # Candidate directory paths to try (in order)
        if subpath:
            candidate_paths = [subpath, f"skills/{subpath}"]
        else:
            candidate_paths = [""]

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"Accept": "application/vnd.github+json"},
        ) as client:
            for branch in ("main", "master"):
                for dir_path in candidate_paths:
                    result = await cls._download_github_dir(
                        client, owner, repo, branch, dir_path, scope, deletable, source
                    )
                    if result.success:
                        return result

        return SkillInstallResult(
            success=False,
            error=f"Could not find a skill directory in GitHub repo: {owner}/{repo}",
        )

    @classmethod
    async def _download_github_dir(
        cls,
        client: Any,
        owner: str,
        repo: str,
        branch: str,
        dir_path: str,
        scope: str,
        deletable: Optional[bool] = None,
        source: Optional[str] = None,
    ) -> SkillInstallResult:
        """
        Recursively download all files in a GitHub directory via the Contents API
        and save them to the skill install root, preserving directory structure.
        """
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}?ref={branch}"
        resp = await client.get(api_url)
        if resp.status_code != 200:
            return SkillInstallResult(
                success=False,
                error=f"GitHub API {resp.status_code} for {api_url}",
            )

        entries = resp.json()
        if not isinstance(entries, list):
            return SkillInstallResult(
                success=False,
                error=f"Expected directory listing from GitHub API, got: {type(entries)}",
            )

        # Verify SKILL.md exists in this directory
        names = {e["name"] for e in entries if e.get("type") == "file"}
        if "SKILL.md" not in names:
            return SkillInstallResult(
                success=False,
                error=f"No SKILL.md found at {dir_path or 'repo root'} on branch {branch}",
            )

        # Determine skill name from SKILL.md content first
        skill_md_entry = next(e for e in entries if e["name"] == "SKILL.md")
        skill_md_resp = await client.get(skill_md_entry["download_url"])
        if skill_md_resp.status_code != 200:
            return SkillInstallResult(
                success=False,
                error=f"Failed to download SKILL.md: HTTP {skill_md_resp.status_code}",
            )
        skill_md_content = skill_md_resp.text

        # Parse skill name from frontmatter
        data = Skill._parse_frontmatter(skill_md_content)
        name = (data.get("name") or "").strip()
        if not name or not Skill._is_valid_name(name):
            return SkillInstallResult(
                success=False,
                error=f"Invalid or missing skill name in SKILL.md: {name!r}",
            )

        skill_dir = _resolve_install_root(scope) / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Recursively download all files preserving directory structure
        file_count = await cls._download_github_entries(
            client, entries, skill_dir, relative_base=""
        )
        cls._write_managed_marker(skill_dir, name, source, deletable)

        Skill.clear_cache()
        log.info("skill.install.github.ok", {
            "name": name,
            "path": str(skill_dir),
            "files": file_count,
        })
        return SkillInstallResult(
            success=True,
            skill_name=name,
            location=str(skill_dir / "SKILL.md"),
            message=f"Skill '{name}' installed to {skill_dir} ({file_count} files)",
        )

    @classmethod
    async def _download_github_entries(
        cls,
        client: Any,
        entries: list,
        base_dir: Path,
        relative_base: str,
    ) -> int:
        """
        Recursively download files from a GitHub Contents API listing.
        Returns total number of files written.
        """
        count = 0
        for entry in entries:
            entry_type = entry.get("type")
            entry_name = entry.get("name", "")
            rel_path = f"{relative_base}/{entry_name}".lstrip("/")

            if entry_type == "file":
                download_url = entry.get("download_url")
                if not download_url:
                    continue
                file_resp = await client.get(download_url)
                if file_resp.status_code != 200:
                    log.warn("skill.install.github.file.skip", {
                        "path": rel_path,
                        "status": file_resp.status_code,
                    })
                    continue
                dest = base_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(file_resp.content)
                count += 1
                log.debug("skill.install.github.file", {"path": rel_path})

            elif entry_type == "dir":
                # Fetch subdirectory listing
                sub_url = entry.get("url")
                if not sub_url:
                    continue
                sub_resp = await client.get(sub_url)
                if sub_resp.status_code != 200:
                    log.warn("skill.install.github.dir.skip", {
                        "path": rel_path,
                        "status": sub_resp.status_code,
                    })
                    continue
                sub_entries = sub_resp.json()
                if isinstance(sub_entries, list):
                    count += await cls._download_github_entries(
                        client, sub_entries, base_dir, rel_path
                    )
        return count

    @staticmethod
    def _safe_zip_path(name: str) -> Optional[PurePosixPath]:
        normalized = name.replace("\\", "/").strip("/")
        if not normalized:
            return None
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            return None
        return path

    @staticmethod
    def _should_skip_packaged_path(path: PurePosixPath) -> bool:
        skip_parts = {".git", "__MACOSX", "__pycache__", ".venv", "node_modules"}
        if any(part in skip_parts for part in path.parts):
            return True
        if path.name in {".DS_Store", "_meta.json"}:
            return True
        if path.name.startswith(".") and path.name.endswith(".skill.json"):
            return True
        return False

    @classmethod
    def _zip_skill_root(cls, zf: zipfile.ZipFile) -> tuple[PurePosixPath, str, str]:
        candidates: list[tuple[int, PurePosixPath, str, str]] = []

        for info in zf.infolist():
            if info.is_dir():
                continue
            path = cls._safe_zip_path(info.filename)
            if path is None or path.name != "SKILL.md":
                continue
            try:
                content = zf.read(info).decode("utf-8")
            except Exception:
                continue
            data = Skill._parse_frontmatter(content)
            skill_name = (data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            if not Skill._is_valid_name(skill_name) or not Skill._is_valid_description(description):
                continue
            root = PurePosixPath(*path.parts[:-1]) if len(path.parts) > 1 else PurePosixPath(".")
            candidates.append((len(path.parts), root, content, skill_name))

        if not candidates:
            raise ValueError("No valid SKILL.md found in zip package")

        candidates.sort(key=lambda item: item[0])
        _, root, content, skill_name = candidates[0]
        return root, content, skill_name

    @classmethod
    def _install_from_zip_bytes(
        cls,
        zip_bytes: bytes,
        scope: str,
        *,
        source: Optional[str],
        deletable: Optional[bool],
        label: str,
    ) -> SkillInstallResult:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                root, _skill_md_content, skill_name = cls._zip_skill_root(zf)
                install_root = _resolve_install_root(scope)
                install_root.mkdir(parents=True, exist_ok=True)
                target_dir = install_root / skill_name
                staging_parent = Path(tempfile.mkdtemp(prefix=f".{skill_name}-", dir=install_root))
                staging_dir = staging_parent / skill_name
                staging_dir.mkdir(parents=True, exist_ok=True)

                copied = 0
                root_parts = () if str(root) == "." else root.parts
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    path = cls._safe_zip_path(info.filename)
                    if path is None:
                        log.warn("skill.install.zip.unsafe_path", {"entry": info.filename})
                        continue
                    if root_parts:
                        if path.parts[: len(root_parts)] != root_parts:
                            continue
                        rel = PurePosixPath(*path.parts[len(root_parts):])
                    else:
                        rel = path
                    if not rel.parts or cls._should_skip_packaged_path(rel):
                        continue

                    dest = (staging_dir / Path(*rel.parts)).resolve()
                    if not dest.is_relative_to(staging_dir.resolve()):
                        log.warn("skill.install.zip.escape", {"entry": info.filename})
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(info))
                    copied += 1

                if not (staging_dir / "SKILL.md").exists():
                    raise ValueError("Selected zip skill root did not produce SKILL.md")

                cls._write_managed_marker(staging_dir, skill_name, source, deletable)
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.move(str(staging_dir), str(target_dir))
                shutil.rmtree(staging_parent, ignore_errors=True)

        except zipfile.BadZipFile:
            return SkillInstallResult(success=False, error=f"Invalid ZIP file for {label}")
        except Exception as exc:
            try:
                if "staging_parent" in locals():
                    shutil.rmtree(staging_parent, ignore_errors=True)
            except Exception:
                pass
            return SkillInstallResult(success=False, error=f"Failed to extract {label}: {exc}")

        skill_path = target_dir / "SKILL.md"
        Skill.clear_cache()
        log.info("skill.install.zip.ok", {
            "name": skill_name,
            "path": str(target_dir),
            "files": copied,
        })
        return SkillInstallResult(
            success=True,
            skill_name=skill_name,
            location=str(skill_path),
            message=f"Skill '{skill_name}' installed to {target_dir} ({copied} files)",
        )

    @classmethod
    def _find_local_skill_root(cls, local_path: Path) -> Optional[Path]:
        current = local_path
        for _ in range(3):
            if (current / "SKILL.md").is_file():
                return current
            children = [p for p in current.iterdir() if p.is_dir()]
            if len(children) != 1:
                return None
            current = children[0]
        return None

    @classmethod
    def _install_from_skill_dir(
        cls,
        source_dir: Path,
        scope: str,
        *,
        source: Optional[str],
        deletable: Optional[bool],
    ) -> SkillInstallResult:
        skill_root = cls._find_local_skill_root(source_dir)
        if skill_root is None:
            return SkillInstallResult(success=False, error=f"No SKILL.md found in directory: {source_dir}")

        try:
            skill_md_content = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Cannot read SKILL.md: {exc}")

        data = Skill._parse_frontmatter(skill_md_content)
        skill_name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        if not Skill._is_valid_name(skill_name) or not Skill._is_valid_description(description):
            return SkillInstallResult(success=False, error=f"Invalid SKILL.md metadata in directory: {source_dir}")

        install_root = _resolve_install_root(scope)
        install_root.mkdir(parents=True, exist_ok=True)
        target_dir = install_root / skill_name
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{skill_name}-", dir=install_root))
        staging_dir = staging_parent / skill_name

        try:
            shutil.copytree(
                skill_root,
                staging_dir,
                ignore=shutil.ignore_patterns(
                    ".git",
                    "__pycache__",
                    ".venv",
                    "node_modules",
                    _managed_marker_name(skill_name),
                ),
            )
            cls._write_managed_marker(staging_dir, skill_name, source, deletable)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.move(str(staging_dir), str(target_dir))
            shutil.rmtree(staging_parent, ignore_errors=True)
        except Exception as exc:
            shutil.rmtree(staging_parent, ignore_errors=True)
            return SkillInstallResult(success=False, error=f"Failed to copy skill directory: {exc}")

        Skill.clear_cache()
        skill_path = target_dir / "SKILL.md"
        return SkillInstallResult(
            success=True,
            skill_name=skill_name,
            location=str(skill_path),
            message=f"Skill '{skill_name}' installed to {target_dir}",
        )

    @classmethod
    async def _install_from_url(
        cls,
        url: str,
        scope: str,
        skill_name_hint: Optional[str] = None,
        source: Optional[str] = None,
        deletable: Optional[bool] = None,
    ) -> SkillInstallResult:
        """Download a SKILL.md from an arbitrary HTTPS URL."""
        try:
            import httpx
        except ImportError:
            return SkillInstallResult(
                success=False,
                error="httpx is required to download skills. Run: uv add httpx",
            )

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return SkillInstallResult(
                        success=False,
                        error=f"HTTP {resp.status_code} fetching {url}",
                    )
                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type:
                    return SkillInstallResult(
                        success=False,
                        error=f"URL returned an HTML page instead of a SKILL.md file: {url}",
                    )
                content = resp.text
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Download failed: {exc}")

        return cls._save_skill_content(
            content,
            scope,
            skill_name_hint=skill_name_hint,
            source=source or url,
            deletable=deletable,
        )

    @classmethod
    async def _install_from_local(
        cls,
        path: str,
        scope: str,
        deletable: Optional[bool] = None,
        source: Optional[str] = None,
    ) -> SkillInstallResult:
        """Install a skill from a local SKILL.md file or directory."""
        local_path = Path(path).expanduser()

        if local_path.is_dir():
            return cls._install_from_skill_dir(
                local_path,
                scope,
                source=source or path,
                deletable=deletable,
            )

        if not local_path.exists():
            return SkillInstallResult(success=False, error=f"File not found: {path}")

        if local_path.suffix.lower() == ".zip":
            try:
                zip_bytes = local_path.read_bytes()
            except Exception as exc:
                return SkillInstallResult(success=False, error=f"Cannot read zip file: {exc}")
            return cls._install_from_zip_bytes(
                zip_bytes,
                scope,
                source=source or path,
                deletable=deletable,
                label=str(local_path),
            )

        try:
            content = local_path.read_text(encoding="utf-8")
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Cannot read file: {exc}")

        return cls._save_skill_content(content, scope, source=source or path, deletable=deletable)

    @classmethod
    def _save_skill_content(
        cls,
        content: str,
        scope: str,
        skill_name_hint: Optional[str] = None,
        source: Optional[str] = None,
        deletable: Optional[bool] = None,
    ) -> SkillInstallResult:
        """Parse content, validate, and persist to the skills directory."""
        # Reject HTML content (e.g. a web page was downloaded instead of raw SKILL.md)
        stripped = content.lstrip()
        if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
            return SkillInstallResult(
                success=False,
                error="Downloaded content is an HTML page, not a valid SKILL.md file. "
                      "Use a direct raw file URL (e.g. raw.githubusercontent.com).",
            )

        # Parse frontmatter to extract name
        data = Skill._parse_frontmatter(content)
        name = (data.get("name") or skill_name_hint or "").strip()

        if not name:
            return SkillInstallResult(
                success=False,
                error="Cannot determine skill name from SKILL.md frontmatter.",
            )

        if not Skill._is_valid_name(name):
            return SkillInstallResult(
                success=False,
                error=f"Invalid skill name: {name!r}. Must match [a-z0-9]+(-[a-z0-9]+)*",
            )

        skill_dir = _resolve_install_root(scope) / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")
        cls._write_managed_marker(skill_dir, name, source, deletable)

        Skill.clear_cache()
        log.info("skill.install.saved", {"name": name, "path": str(skill_path)})

        return SkillInstallResult(
            success=True,
            skill_name=name,
            location=str(skill_path),
            message=f"Skill '{name}' installed to {skill_path}",
        )

    # ------------------------------------------------------------------
    # Install skill dependencies
    # ------------------------------------------------------------------

    @classmethod
    async def install_deps(
        cls,
        skill_name: str,
        install_id: Optional[str] = None,
        timeout_ms: int = 300_000,
    ) -> List[DepInstallResult]:
        """
        Install a skill's declared tool dependencies.

        Args:
            skill_name: Name of the skill
            install_id: If set, only install the spec with this id
            timeout_ms: Subprocess timeout in milliseconds (default 5 min)

        Returns:
            List of DepInstallResult, one per executed spec
        """
        skill = await Skill.get(skill_name)
        if not skill:
            return [DepInstallResult(
                success=False,
                error=f"Skill not found: {skill_name}",
            )]

        specs = skill.install_specs or []
        if not specs:
            return [DepInstallResult(
                success=True,
                message=f"Skill '{skill_name}' has no install specs.",
            )]

        if install_id is not None:
            specs = [s for s in specs if s.id == install_id]
            if not specs:
                return [DepInstallResult(
                    success=False,
                    error=f"No install spec with id='{install_id}' in skill '{skill_name}'",
                )]

        results: List[DepInstallResult] = []
        timeout_sec = timeout_ms / 1000

        for spec in specs:
            result = await cls._execute_install_spec(spec, timeout_sec)
            results.append(result)

        return results

    @classmethod
    async def _execute_install_spec(
        cls,
        spec: SkillInstallSpec,
        timeout_sec: float,
    ) -> DepInstallResult:
        """Build and execute an install command for one SkillInstallSpec."""
        cmd = cls._build_install_command(spec)
        if not cmd:
            return DepInstallResult(
                success=False,
                spec_id=spec.id,
                error=f"Cannot build install command for kind={spec.kind!r}",
            )

        log.info("skill.dep.install.start", {"kind": spec.kind, "cmd": cmd})
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_sec
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return DepInstallResult(
                    success=False,
                    spec_id=spec.id,
                    command=cmd,
                    error=f"Install timed out after {timeout_sec}s",
                )

            stdout = stdout_b.decode(errors="replace")
            stderr = stderr_b.decode(errors="replace")
            returncode = proc.returncode if proc.returncode is not None else 0
            success = returncode == 0

            log.info("skill.dep.install.done", {
                "kind": spec.kind,
                "returncode": returncode,
                "success": success,
            })
            return DepInstallResult(
                success=success,
                spec_id=spec.id,
                command=cmd,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
                error=None if success else f"Command exited with code {returncode}",
            )
        except Exception as exc:
            return DepInstallResult(
                success=False,
                spec_id=spec.id,
                command=cmd,
                error=str(exc),
            )

    @staticmethod
    def _build_install_command(spec: SkillInstallSpec) -> Optional[List[str]]:
        """Return the argv list for the install spec, or None if unsupported."""
        current_os = platform.system().lower()  # darwin / linux / windows

        # OS guard
        if spec.os:
            os_map = {"darwin": "darwin", "linux": "linux", "windows": "win32"}
            allowed = {os_map.get(o, o) for o in spec.os}
            if current_os not in allowed and f"{current_os}" not in spec.os:
                log.warn("skill.dep.install.os_skip", {
                    "kind": spec.kind,
                    "spec_os": spec.os,
                    "current_os": current_os,
                })
                return None

        if spec.kind == "brew":
            if not spec.formula:
                return None
            return ["brew", "install", spec.formula]

        if spec.kind == "npm":
            if not spec.package:
                return None
            return ["npm", "install", "-g", "--ignore-scripts", spec.package]

        if spec.kind == "uv":
            if not spec.package:
                return None
            return ["uv", "tool", "install", spec.package]

        if spec.kind == "pip":
            if not spec.package:
                return None
            return [sys.executable, "-m", "pip", "install", spec.package]

        if spec.kind == "go":
            if not (spec.module or spec.package):
                return None
            return ["go", "install", spec.module or spec.package]

        # download kind is handled separately (binary download, not a package manager)
        return None

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    @classmethod
    async def uninstall(cls, skill_name: str) -> SkillInstallResult:
        """
        Remove a user-managed skill from ~/.smartclaw/plugins/skills/.

        Only skills installed under the user skills root can be removed via API.
        """
        skill = await Skill.get(skill_name)
        if not skill:
            return SkillInstallResult(
                success=False,
                error=f"Skill not found: {skill_name}",
            )

        skill_path = Path(skill.location)
        skill_dir = skill_path.parent

        if not skill_path.is_relative_to(_user_skills_root()):
            return SkillInstallResult(
                success=False,
                error=(
                    f"Skill '{skill_name}' is not user-managed "
                    f"(location: {skill.location}). Only skills installed to "
                    f"~/.smartclaw/plugins/skills/ can be removed."
                ),
            )

        try:
            shutil.rmtree(skill_dir)
            Skill.clear_cache()
            log.info("skill.uninstall.ok", {"name": skill_name, "dir": str(skill_dir)})
            return SkillInstallResult(
                success=True,
                skill_name=skill_name,
                message=f"Skill '{skill_name}' removed.",
            )
        except Exception as exc:
            return SkillInstallResult(success=False, error=str(exc))
