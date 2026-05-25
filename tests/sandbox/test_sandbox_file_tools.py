"""
Sandbox-aware file tool tests.
"""

import os
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


def _sandbox_ctx(
    workspace_dir: str,
    workspace_access: str = "none",
    upload_dir: str | None = None,
) -> ToolContext:
    sandbox = {
        "workspace_dir": workspace_dir,
        "workspace_access": workspace_access,
        "container_workdir": "/workspace",
    }
    if upload_dir:
        sandbox["upload_mounts"] = [{
            "host_dir": upload_dir,
            "container_dir": "/workspace/uploads/chat/sandbox-file-tools",
            "read_only": True,
        }]
    return ToolContext(
        session_id="sandbox-file-tools",
        message_id="sandbox-file-tools-msg",
        extra={"sandbox": sandbox},
    )


@pytest.mark.asyncio
async def test_read_tool_rejects_path_outside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir)
        result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath="/tmp/definitely-outside-sandbox.txt",
        )
        assert not result.success
        assert "Path escapes sandbox workspace" in (result.error or "")


@pytest.mark.asyncio
async def test_read_tool_reads_inside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        target = os.path.join(sandbox_dir, "notes.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("hello\nsandbox\n")

        ctx = _sandbox_ctx(sandbox_dir)
        result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath=target,
        )
        assert result.success
        assert "sandbox" in (result.output or "")


@pytest.mark.asyncio
async def test_read_tool_maps_container_upload_path() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir, tempfile.TemporaryDirectory() as upload_dir:
        target = os.path.join(upload_dir, "attached.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("hello from upload\n")

        ctx = _sandbox_ctx(sandbox_dir, upload_dir=upload_dir)
        result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath="/workspace/uploads/chat/sandbox-file-tools/attached.txt",
        )
        assert result.success
        assert "hello from upload" in (result.output or "")


@pytest.mark.asyncio
async def test_write_tool_blocked_in_ro_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir, workspace_access="ro")
        result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath=os.path.join(sandbox_dir, "a.py"),
            content="x",
        )
        assert not result.success
        assert "read-only workspace mode" in (result.error or "")


@pytest.mark.asyncio
async def test_write_tool_allows_session_outputs_in_ro_sandbox(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    sandbox_dir = tmp_path / "sandbox"
    home_dir.mkdir()
    sandbox_dir.mkdir()
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
        result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath="/workspace/outputs/report.md",
            content="ok",
        )

        output_dir = WorkspaceManager.get_instance().get_outputs_dir("sandbox-file-tools")
        expected = output_dir / "report.md"
        assert result.success, result.error
        assert expected.read_text(encoding="utf-8") == "ok"
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_write_tool_allows_session_artifacts_in_ro_sandbox(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    sandbox_dir = tmp_path / "sandbox"
    home_dir.mkdir()
    sandbox_dir.mkdir()
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
        result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath="/workspace/artifacts/payload.md",
            content="artifact",
        )

        output_dir = WorkspaceManager.get_instance().get_outputs_dir("sandbox-file-tools")
        expected = output_dir / "artifacts" / "payload.md"
        assert result.success, result.error
        assert expected.read_text(encoding="utf-8") == "artifact"
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_write_tool_blocks_container_upload_path() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir, tempfile.TemporaryDirectory() as upload_dir:
        ctx = _sandbox_ctx(sandbox_dir, workspace_access="rw", upload_dir=upload_dir)
        result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath="/workspace/uploads/chat/sandbox-file-tools/attached.txt",
            content="overwrite",
        )
        assert not result.success
        assert "Upload mounts are read-only" in (result.error or "")


@pytest.mark.asyncio
async def test_edit_tool_rejects_path_outside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        outside_file = os.path.join(tempfile.gettempdir(), "sandbox-edit-outside.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("hello")
        try:
            ctx = _sandbox_ctx(sandbox_dir, workspace_access="rw")
            result = await ToolRegistry.execute(
                "edit",
                ctx=ctx,
                filePath=outside_file,
                oldString="hello",
                newString="world",
            )
            assert not result.success
            assert "Path escapes sandbox workspace" in (result.error or "")
        finally:
            try:
                os.remove(outside_file)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_edit_tool_allows_session_outputs_in_ro_sandbox(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    sandbox_dir = tmp_path / "sandbox"
    home_dir.mkdir()
    sandbox_dir.mkdir()
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        output_dir = WorkspaceManager.get_instance().get_outputs_dir("sandbox-file-tools")
        target = output_dir / "report.md"
        target.write_text("old", encoding="utf-8")

        ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
        result = await ToolRegistry.execute(
            "edit",
            ctx=ctx,
            filePath="/workspace/outputs/report.md",
            oldString="old",
            newString="new",
        )

        assert result.success, result.error
        assert target.read_text(encoding="utf-8") == "new"
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_edit_tool_blocks_workspace_in_ro_sandbox(tmp_path) -> None:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    target = sandbox_dir / "notes.txt"
    target.write_text("old", encoding="utf-8")

    ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
    result = await ToolRegistry.execute(
        "edit",
        ctx=ctx,
        filePath=str(target),
        oldString="old",
        newString="new",
    )

    assert not result.success
    assert "read-only workspace mode" in (result.error or "")
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_multiedit_tool_allows_session_outputs_in_ro_sandbox(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    sandbox_dir = tmp_path / "sandbox"
    home_dir.mkdir()
    sandbox_dir.mkdir()
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        output_dir = WorkspaceManager.get_instance().get_outputs_dir("sandbox-file-tools")
        target = output_dir / "report.md"
        target.write_text("alpha beta", encoding="utf-8")

        ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
        result = await ToolRegistry.execute(
            "multiedit",
            ctx=ctx,
            filePath="/workspace/outputs/report.md",
            edits=[
                {"oldString": "alpha", "newString": "one"},
                {"oldString": "beta", "newString": "two"},
            ],
        )

        assert result.success, result.error
        assert target.read_text(encoding="utf-8") == "one two"
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_apply_patch_allows_session_outputs_in_ro_sandbox(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    sandbox_dir = tmp_path / "sandbox"
    home_dir.mkdir()
    sandbox_dir.mkdir()
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
        result = await ToolRegistry.execute(
            "apply_patch",
            ctx=ctx,
            patchText=(
                "*** Begin Patch\n"
                "*** Add File: /workspace/outputs/patched.md\n"
                "patched\n"
                "*** End Patch"
            ),
        )

        expected = WorkspaceManager.get_instance().get_outputs_dir("sandbox-file-tools") / "patched.md"
        assert result.success, result.error
        assert expected.read_text(encoding="utf-8") == "patched\n"
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_apply_patch_blocks_workspace_in_ro_sandbox(tmp_path) -> None:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()

    ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
    result = await ToolRegistry.execute(
        "apply_patch",
        ctx=ctx,
        patchText=(
            "*** Begin Patch\n"
            "*** Add File: /workspace/blocked.md\n"
            "blocked\n"
            "*** End Patch"
        ),
    )

    assert not result.success
    assert "read-only mode" in (result.error or "")
    assert not (sandbox_dir / "blocked.md").exists()


@pytest.mark.asyncio
async def test_glob_finds_session_uploads_by_default() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir, tempfile.TemporaryDirectory() as upload_dir:
        Path(upload_dir, "report.docx").write_text("placeholder", encoding="utf-8")

        ctx = _sandbox_ctx(sandbox_dir, upload_dir=upload_dir)
        result = await ToolRegistry.execute("glob", ctx=ctx, pattern="**/*.docx")
        assert result.success
        assert "/workspace/uploads/chat/sandbox-file-tools/report.docx" in (result.output or "")


def _write_minimal_docx(path: Path, text: str) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


@pytest.mark.asyncio
async def test_doc_parser_maps_container_upload_path(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir, tempfile.TemporaryDirectory() as upload_dir:
        docx_path = Path(upload_dir) / "brief.docx"
        _write_minimal_docx(docx_path, "Uploaded brief text")

        ctx = _sandbox_ctx(sandbox_dir, upload_dir=upload_dir)
        output_path = Path(sandbox_dir) / "brief.md"
        result = await ToolRegistry.execute(
            "doc_parser",
            ctx=ctx,
            input_path="/workspace/uploads/chat/sandbox-file-tools/brief.docx",
            output_path=str(output_path),
        )
        assert result.success
        assert "Uploaded brief text" in output_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_doc_parser_allows_output_alias_in_ro_sandbox(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    sandbox_dir = tmp_path / "sandbox"
    home_dir.mkdir()
    sandbox_dir.mkdir()
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        source = sandbox_dir / "brief.docx"
        _write_minimal_docx(source, "Sandbox output text")

        ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
        result = await ToolRegistry.execute(
            "doc_parser",
            ctx=ctx,
            input_path=str(source),
            output_path="/workspace/outputs/brief.md",
        )

        expected = WorkspaceManager.get_instance().get_outputs_dir("sandbox-file-tools") / "brief.md"
        assert result.success, result.error
        assert "Sandbox output text" in expected.read_text(encoding="utf-8")
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_doc_parser_blocks_workspace_output_in_ro_sandbox(tmp_path) -> None:
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    source = sandbox_dir / "brief.docx"
    _write_minimal_docx(source, "Blocked output text")

    ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="ro")
    result = await ToolRegistry.execute(
        "doc_parser",
        ctx=ctx,
        input_path=str(source),
        output_path="/workspace/brief.md",
    )

    assert not result.success
    assert "read-only mode" in (result.error or "")
    assert not (sandbox_dir / "brief.md").exists()


@pytest.mark.asyncio
async def test_doc_parser_default_output_is_readable_in_sandbox(monkeypatch) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    with (
        tempfile.TemporaryDirectory() as sandbox_dir,
        tempfile.TemporaryDirectory() as upload_dir,
        tempfile.TemporaryDirectory() as home_dir,
    ):
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: Path(home_dir))
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(Path(home_dir) / ".flocks" / "workspace"))
        docx_path = Path(upload_dir) / "brief.docx"
        _write_minimal_docx(docx_path, "Uploaded brief text")

        ctx = _sandbox_ctx(sandbox_dir, upload_dir=upload_dir)
        result = await ToolRegistry.execute(
            "doc_parser",
            ctx=ctx,
            filePath="/workspace/uploads/chat/sandbox-file-tools/brief.docx",
        )
        try:
            assert result.success, result.error
            output_payload = (
                json.loads(result.output)
                if isinstance(result.output, str)
                else result.output
            )
            output_path = Path(output_payload["output_path"])
            assert output_path.exists()
            assert output_path.is_relative_to(Path(home_dir) / ".flocks" / "workspace" / "outputs")

            read_result = await ToolRegistry.execute(
                "read",
                ctx=ctx,
                filePath=str(output_path),
            )
            assert read_result.success, read_result.error
            assert "Uploaded brief text" in (read_result.output or "")
        finally:
            WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_read_maps_container_upload_path_without_sandbox_context(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        upload_dir = (
            WorkspaceManager.get_instance()
            .resolve_user_workspace_path("uploads/chat/ses_upload_alias")
        )
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "notes.txt").write_text("uploaded note", encoding="utf-8")

        ctx = ToolContext(
            session_id="ses_upload_alias",
            message_id="sandbox-file-tools-msg",
        )
        result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath="/workspace/uploads/chat/ses_upload_alias/notes.txt",
        )
        assert result.success, result.error
        assert "uploaded note" in (result.output or "")
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_doc_parser_accepts_output_alias_for_upload_path(monkeypatch, tmp_path) -> None:
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)
    try:
        upload_dir = (
            WorkspaceManager.get_instance()
            .resolve_user_workspace_path("uploads/chat/ses_doc_alias")
        )
        upload_dir.mkdir(parents=True, exist_ok=True)
        docx_path = upload_dir / "brief.docx"
        _write_minimal_docx(docx_path, "Alias output text")
        output_path = tmp_path / "brief.md"

        ctx = ToolContext(
            session_id="ses_doc_alias",
            message_id="sandbox-file-tools-msg",
        )
        result = await ToolRegistry.execute(
            "doc_parser",
            ctx=ctx,
            input_path="/workspace/uploads/chat/ses_doc_alias/brief.docx",
            output=str(output_path),
        )
        assert result.success, result.error
        assert "Alias output text" in output_path.read_text(encoding="utf-8")
    finally:
        WorkspaceManager._instance = previous_instance


@pytest.mark.asyncio
async def test_sandbox_context_mounts_main_session_uploads(monkeypatch, tmp_path) -> None:
    from flocks.sandbox.context import resolve_sandbox_context
    from flocks.workspace.manager import WorkspaceManager

    previous_instance = WorkspaceManager._instance
    WorkspaceManager._instance = None
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home_dir)

    captured_container_kwargs = {}

    async def fake_ensure_sandbox_container(**_kwargs):
        captured_container_kwargs.update(_kwargs)
        return "flocks-sbx-test"

    async def fake_maybe_prune_sandboxes(_cfg):
        return None

    monkeypatch.setattr(
        "flocks.sandbox.context.ensure_sandbox_container",
        fake_ensure_sandbox_container,
    )
    monkeypatch.setattr(
        "flocks.sandbox.context.maybe_prune_sandboxes",
        fake_maybe_prune_sandboxes,
    )

    try:
        main_upload_dir = (
            WorkspaceManager.get_instance()
            .resolve_user_workspace_path("uploads/chat/ses_main_uploads")
        )
        main_upload_dir.mkdir(parents=True, exist_ok=True)
        (main_upload_dir / "brief.docx").write_text("placeholder", encoding="utf-8")

        sandbox_ctx = await resolve_sandbox_context(
            config_data={
                "sandbox": {
                    "mode": "on",
                    "scope": "session",
                    "workspace_access": "rw",
                }
            },
            session_key="ses_child_uploads",
            main_session_key="ses_main_uploads",
            workspace_dir=str(tmp_path / "project"),
        )

        assert sandbox_ctx is not None
        mounted_dirs = {mount["container_dir"] for mount in sandbox_ctx.upload_mounts}
        assert "/workspace/uploads/chat/ses_child_uploads" in mounted_dirs
        assert "/workspace/uploads/chat/ses_main_uploads" in mounted_dirs
        binds = set(captured_container_kwargs["cfg"].docker.binds or [])
        output_dir = WorkspaceManager.get_instance().get_outputs_dir("ses_main_uploads")
        assert f"{output_dir.resolve()}:/workspace/outputs" in binds
        assert f"{output_dir.resolve()}:/workspace/output" in binds
        assert f"{(output_dir / 'artifacts').resolve()}:/workspace/artifacts" in binds
    finally:
        WorkspaceManager._instance = previous_instance
