from types import SimpleNamespace

import pytest

from smartclaw.tool.code import grep as grep_module
from smartclaw.tool.file import glob as glob_module
from smartclaw.tool.file import list_tool as list_module
from smartclaw.tool.registry import ToolContext


def _context() -> ToolContext:
    return ToolContext(session_id="session-1", message_id="message-1", agent="titan")


@pytest.mark.asyncio
async def test_grep_success_formats_matching_file(monkeypatch, tmp_path):
    match = {
        "path": str(tmp_path / "sample.txt"),
        "lineNum": 3,
        "lineText": "needle",
        "modTime": 1.0,
    }
    monkeypatch.setattr(grep_module.Instance, "get_directory", lambda: str(tmp_path))
    monkeypatch.setattr(grep_module, "sandbox_search_roots", lambda *_args: [str(tmp_path)])
    monkeypatch.setattr(grep_module, "find_ripgrep", lambda: None)
    monkeypatch.setattr(grep_module, "fallback_grep", lambda *_args: [match])

    result = await grep_module.grep_tool(_context(), pattern="needle")

    assert result.success is True
    assert str(tmp_path / "sample.txt") in result.output
    assert "Line 3: needle" in result.output


@pytest.mark.asyncio
async def test_glob_success_formats_matching_path(monkeypatch, tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(glob_module.Instance, "get_directory", lambda: str(tmp_path))
    monkeypatch.setattr(glob_module.Instance, "get_worktree", lambda: str(tmp_path))
    monkeypatch.setattr(glob_module, "sandbox_search_roots", lambda *_args: [str(tmp_path)])
    monkeypatch.setattr(glob_module, "find_ripgrep", lambda: None)
    monkeypatch.setattr(glob_module, "fallback_glob", lambda *_args: [target.name])

    result = await glob_module.glob_tool(_context(), pattern="*.py")

    assert result.success is True
    assert str(target) in result.output


@pytest.mark.asyncio
async def test_list_success_renders_directory_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(list_module.Instance, "get_directory", lambda: str(tmp_path))
    monkeypatch.setattr(list_module.Instance, "get_worktree", lambda: str(tmp_path))
    monkeypatch.setattr(list_module, "find_ripgrep", lambda: None)
    monkeypatch.setattr(list_module, "fallback_list", lambda *_args: ["sample.txt"])
    monkeypatch.setattr(
        list_module,
        "resolve_sandbox_path",
        lambda *_args: (_ for _ in ()).throw(AssertionError("path resolution is not expected")),
    )

    result = await list_module.list_tool(_context())

    assert result.success is True
    assert "sample.txt" in result.output
    assert result.metadata["count"] == 1
