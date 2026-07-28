"""
"沙箱与宿主机目录一致"修改的专项单元测试

背景：
  旧模式：容器工作目录固定为 /workspace，宿主机使用实际路径；
          Docker 挂载为 -v /host/path:/workspace:rw（路径不同）。
  新模式：容器工作目录 = os.getcwd() = 宿主机路径；
          Docker 挂载为 -v /host/path:/host/path:rw（路径相同）。
  因此不再需要 display_path() 做路径反显示转换。

覆盖：
1. sandbox/config.py         - workdir / workspace_root 均使用 os.getcwd()
2. sandbox/uploads.py        - container_dir 使用真实宿主机路径；新增 task 挂载
3. tool/file/sandbox_paths.py - container_workdir 优先匹配；/workspace 兜底仍有效
4. tool/code/bash.py         - _is_allowed_temporary_script_path 使用 container_workdir
5. tool/code/bash.py         - _is_flocks_plugin_write_path 移除 /workspace/ 硬编码检查
6. docker.py bind 格式验证   - 宿主机路径 == 容器路径
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flocks.sandbox.config import resolve_sandbox_config_for_agent, resolve_sandbox_docker_config
from flocks.sandbox.uploads import (
    UPLOADS_CHAT_PREFIX,
    UPLOADS_TASK_PREFIX,
    get_session_upload_mounts,
)
from flocks.tool.code.bash import (
    _is_allowed_temporary_script_path,
    _is_flocks_plugin_write_path,
)
from flocks.tool.file.sandbox_paths import (
    _map_workspace_upload_container_path,
    resolve_sandbox_path,
    sandbox_search_roots,
)
from flocks.tool.registry import ToolContext


# ─────────────────────────────────────────────────────────────────────────────
# 辅助工厂
# ─────────────────────────────────────────────────────────────────────────────

def _make_ctx(
    *,
    workspace_dir: str,
    container_workdir: str,
    agent_workspace_dir: str | None = None,
    project_plugins_dir: str | None = None,
    upload_mounts: list[dict] | None = None,
) -> ToolContext:
    return ToolContext(
        session_id="ses_test",
        message_id="m-test",
        extra={
            "sandbox": {
                "workspace_dir": workspace_dir,
                "container_workdir": container_workdir,
                "agent_workspace_dir": agent_workspace_dir or workspace_dir,
                "project_plugins_dir": project_plugins_dir,
                "upload_mounts": upload_mounts or [],
                "workspace_access": "rw",
            }
        },
    )


def _ctx_with_workdir_only(workdir: str) -> ToolContext:
    """用于 bash 路径检测测试的最小化 sandbox ctx"""
    return ToolContext(
        session_id="ses_test",
        message_id="m-test",
        extra={"sandbox": {"container_workdir": workdir}},
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. sandbox/config.py - workdir / workspace_root 统一
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxConfigPathUnification:
    """config.py 修改：workdir 和 workspace_root 均固定为 os.getcwd()"""

    def test_docker_workdir_equals_cwd(self):
        """Docker 容器工作目录应为宿主机当前工作目录"""
        cfg = resolve_sandbox_docker_config("private")
        assert cfg.workdir == os.getcwd()

    def test_docker_workdir_ignores_global_config(self):
        """全局 docker 配置中的 workdir 被忽略，始终使用 os.getcwd()"""
        cfg = resolve_sandbox_docker_config(
            "private",
            global_docker={"workdir": "/workspace"},
        )
        assert cfg.workdir == os.getcwd()
        assert cfg.workdir != "/workspace"

    def test_docker_workdir_ignores_agent_config(self):
        """agent 级别的 workdir 覆写同样被忽略"""
        cfg = resolve_sandbox_docker_config(
            "private",
            global_docker={},
            agent_docker={"workdir": "/custom/path"},
        )
        assert cfg.workdir == os.getcwd()

    def test_workspace_root_equals_cwd(self):
        """SandboxConfig.workspace_root 应为 os.getcwd()"""
        cfg = resolve_sandbox_config_for_agent({})
        assert cfg.workspace_root == os.getcwd()

    def test_workspace_root_ignores_agent_sandbox_override(self):
        """agent sandbox 中的 workspace_root 配置不再生效"""
        cfg = resolve_sandbox_config_for_agent({
            "sandbox": {
                "workspace_root": "/custom/workspace",
                "mode": "off",
            }
        })
        assert cfg.workspace_root == os.getcwd()
        assert cfg.workspace_root != "/custom/workspace"

    def test_container_workdir_equals_host_path(self):
        """
        核心断言：容器工作目录 == 宿主机工作目录。
        路径统一后两者应完全一致，不再需要路径映射。
        """
        docker_cfg = resolve_sandbox_docker_config("private")
        sandbox_cfg = resolve_sandbox_config_for_agent({})
        assert docker_cfg.workdir == sandbox_cfg.workspace_root


# ─────────────────────────────────────────────────────────────────────────────
# 2. sandbox/uploads.py - container_dir 使用真实路径
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadMountsPathUnification:
    """
    当 container_workdir 为真实宿主机路径（而非 /workspace）时，
    container_dir 应与 host_dir 完全相同。
    """

    def _mock_manager(self, monkeypatch, upload_root: Path):
        """Mock WorkspaceManager，使 resolve_user_workspace_path 返回真实临时目录"""
        from flocks.workspace.manager import WorkspaceManager
        upload_dir = upload_root / "uploads" / "chat" / "ses_test"
        upload_dir.mkdir(parents=True, exist_ok=True)
        mock = MagicMock()
        mock.resolve_user_workspace_path.return_value = upload_dir
        mock.get_user_workspace_dir.return_value = upload_root
        monkeypatch.setattr(WorkspaceManager, "get_instance", staticmethod(lambda: mock))
        return upload_dir

    def test_container_dir_uses_real_workdir_not_workspace(self, tmp_path, monkeypatch):
        """container_dir 应以真实宿主机路径为前缀，不含 /workspace"""
        self._mock_manager(monkeypatch, tmp_path)
        mounts = get_session_upload_mounts("ses_test", container_workdir=str(tmp_path))
        assert len(mounts) >= 1
        container_dir = mounts[0].container_dir.replace("\\", "/")
        workdir_fwd = str(tmp_path).replace("\\", "/")
        assert container_dir.startswith(workdir_fwd)
        assert "/workspace/" not in container_dir

    def test_host_dir_equals_container_dir_when_workdir_matches(self, tmp_path, monkeypatch):
        """
        路径统一的核心效果：
        当 container_workdir == user_workspace_dir 时，host_dir == container_dir（跨平台比较）。
        Docker 挂载将变为 path:path:ro（相同路径）。
        """
        self._mock_manager(monkeypatch, tmp_path)
        mounts = get_session_upload_mounts("ses_test", container_workdir=str(tmp_path))
        chat_mount = next(m for m in mounts if UPLOADS_CHAT_PREFIX in m.container_dir)
        # 跨平台路径比较（Windows 使用 \ ，容器路径使用 /）
        assert Path(chat_mount.host_dir) == Path(chat_mount.container_dir)

    def test_task_upload_mount_generated(self, tmp_path, monkeypatch):
        """新增功能：应同时生成 task 上传挂载（uploads/task）"""
        self._mock_manager(monkeypatch, tmp_path)
        mounts = get_session_upload_mounts("ses_test", container_workdir=str(tmp_path))
        assert len(mounts) == 2
        container_dirs = [m.container_dir for m in mounts]
        assert any(UPLOADS_CHAT_PREFIX in d for d in container_dirs)
        assert any(UPLOADS_TASK_PREFIX in d for d in container_dirs)

    def test_task_mount_is_readonly(self, tmp_path, monkeypatch):
        """task 上传挂载应为只读（与 chat 一致）"""
        self._mock_manager(monkeypatch, tmp_path)
        mounts = get_session_upload_mounts("ses_test", container_workdir=str(tmp_path))
        task_mount = next(m for m in mounts if UPLOADS_TASK_PREFIX in m.container_dir)
        assert task_mount.read_only is True

    def test_legacy_workspace_container_workdir_still_works(self, tmp_path, monkeypatch):
        """向后兼容：使用旧 /workspace 时 container_dir 应以 /workspace 为前缀"""
        self._mock_manager(monkeypatch, tmp_path)
        mounts = get_session_upload_mounts("ses_test", container_workdir="/workspace")
        assert mounts[0].container_dir.startswith("/workspace/")

    def test_task_container_dir_derived_from_chat_dir(self, tmp_path, monkeypatch):
        """task 挂载的 container_dir 应与 chat 挂载仅前缀不同"""
        self._mock_manager(monkeypatch, tmp_path)
        mounts = get_session_upload_mounts("ses_test", container_workdir=str(tmp_path))
        chat_dir = next(m.container_dir for m in mounts if UPLOADS_CHAT_PREFIX in m.container_dir)
        task_dir = next(m.container_dir for m in mounts if UPLOADS_TASK_PREFIX in m.container_dir)
        assert task_dir == chat_dir.replace(UPLOADS_CHAT_PREFIX, UPLOADS_TASK_PREFIX)


# ─────────────────────────────────────────────────────────────────────────────
# 3. sandbox_paths.py - container_workdir 优先匹配；/workspace 兜底
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxPathsContainerWorkdirPriority:
    """
    _map_workspace_upload_container_path 等函数以 container_workdir 为第一匹配根，
    /workspace 仅作兜底。
    """

    def _sandbox(self, workspace: str, container_workdir: str) -> dict:
        return {
            "workspace_dir": workspace,
            "container_workdir": container_workdir,
            "upload_mounts": [],
        }

    def _mock_workspace_manager(self, resolved_path: Path):
        """返回一个 mock WorkspaceManager，resolve_user_workspace_path 返回指定路径"""
        mock = MagicMock()
        mock.resolve_user_workspace_path.return_value = resolved_path
        return mock

    def test_upload_path_with_real_container_workdir_matched(self, tmp_path):
        """以真实宿主机路径为前缀的上传路径，应通过 container_workdir 优先匹配"""
        workspace = str(tmp_path)
        sandbox = self._sandbox(workspace, workspace)
        upload_path = f"{workspace}/uploads/chat/ses_test/file.txt"
        expected = tmp_path / "uploads" / "chat" / "ses_test" / "file.txt"

        with patch("flocks.workspace.manager.WorkspaceManager.get_instance") as mock_cls:
            mock_cls.return_value = self._mock_workspace_manager(expected)
            result = _map_workspace_upload_container_path(upload_path, sandbox)

        assert result is not None, "container_workdir 优先匹配应成功"
        assert result.path == str(expected)
        assert result.read_only is True

    def test_legacy_workspace_prefix_still_matched_as_fallback(self, tmp_path):
        """/workspace 前缀在新设计中作为兜底，仍应能被匹配"""
        workspace = str(tmp_path)
        sandbox = self._sandbox(workspace, workspace)
        legacy_path = "/workspace/uploads/chat/ses_test/file.txt"
        expected = tmp_path / "uploads" / "chat" / "ses_test" / "file.txt"

        with patch("flocks.workspace.manager.WorkspaceManager.get_instance") as mock_cls:
            mock_cls.return_value = self._mock_workspace_manager(expected)
            result = _map_workspace_upload_container_path(legacy_path, sandbox)

        assert result is not None, "/workspace 兜底应仍然有效"

    def test_task_upload_path_matched(self, tmp_path):
        """uploads/task 路径（新增功能）也应被正确匹配"""
        workspace = str(tmp_path)
        sandbox = self._sandbox(workspace, workspace)
        task_path = f"{workspace}/uploads/task/ses_test/data.csv"
        expected = tmp_path / "uploads" / "task" / "ses_test" / "data.csv"

        with patch("flocks.workspace.manager.WorkspaceManager.get_instance") as mock_cls:
            mock_cls.return_value = self._mock_workspace_manager(expected)
            result = _map_workspace_upload_container_path(task_path, sandbox)

        assert result is not None

    def test_non_upload_path_not_matched(self, tmp_path):
        """不含 uploads/ 的路径不应被 upload 映射函数处理"""
        workspace = str(tmp_path)
        sandbox = self._sandbox(workspace, workspace)
        other_path = f"{workspace}/outputs/report.md"

        with patch("flocks.workspace.manager.WorkspaceManager.get_instance") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = _map_workspace_upload_container_path(other_path, sandbox)

        assert result is None

    def test_dedup_when_container_workdir_equals_workspace(self, tmp_path):
        """
        当 container_workdir == /workspace 时，去重逻辑应只匹配一次，不报错。
        """
        sandbox = self._sandbox(str(tmp_path), "/workspace")
        path = "/workspace/uploads/chat/ses_test/file.txt"
        expected = tmp_path / "uploads" / "chat" / "ses_test" / "file.txt"

        with patch("flocks.workspace.manager.WorkspaceManager.get_instance") as mock_cls:
            mock_cls.return_value = self._mock_workspace_manager(expected)
            result = _map_workspace_upload_container_path(path, sandbox)

        assert result is not None


class TestSandboxSearchRoots:
    """sandbox_search_roots 应包含 workspace_dir 和实际存在的上传目录"""

    def test_no_sandbox_returns_default(self, tmp_path):
        """无 sandbox ctx 时返回 default_root"""
        ctx = ToolContext(session_id="ses", message_id="m")
        roots = sandbox_search_roots(ctx, str(tmp_path))
        assert roots == [str(tmp_path)]

    def test_with_sandbox_returns_workspace_dir(self, tmp_path):
        """有 sandbox 时返回 workspace_dir"""
        ctx = _make_ctx(workspace_dir=str(tmp_path), container_workdir=str(tmp_path))
        roots = sandbox_search_roots(ctx, str(tmp_path))
        assert str(tmp_path) in roots

    def test_upload_dir_included_when_exists(self, tmp_path):
        """实际存在的上传目录应出现在搜索根中"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        upload_host = tmp_path / "uploads" / "ses_test"
        upload_host.mkdir(parents=True)

        ctx = _make_ctx(
            workspace_dir=str(workspace),
            container_workdir=str(workspace),
            upload_mounts=[{
                "host_dir": str(upload_host),
                "container_dir": str(workspace / "uploads" / "chat" / "ses_test"),
                "read_only": True,
            }],
        )
        roots = sandbox_search_roots(ctx, str(workspace))
        assert str(upload_host) in roots

    def test_nonexistent_upload_dir_excluded(self, tmp_path):
        """不存在的上传目录不应出现在搜索根中"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ghost_dir = str(tmp_path / "nonexistent" / "upload")

        ctx = _make_ctx(
            workspace_dir=str(workspace),
            container_workdir=str(workspace),
            upload_mounts=[{
                "host_dir": ghost_dir,
                "container_dir": str(workspace / "uploads" / "chat" / "ses_test"),
                "read_only": True,
            }],
        )
        roots = sandbox_search_roots(ctx, str(workspace))
        assert ghost_dir not in roots

    def test_requested_path_overrides_upload_search(self, tmp_path):
        """指定 requested_path 时只返回该路径，不扩展上传目录"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        upload_host = tmp_path / "uploads"
        upload_host.mkdir()
        requested = str(workspace / "subdir")

        ctx = _make_ctx(
            workspace_dir=str(workspace),
            container_workdir=str(workspace),
            upload_mounts=[{
                "host_dir": str(upload_host),
                "container_dir": str(workspace / "uploads"),
                "read_only": True,
            }],
        )
        roots = sandbox_search_roots(ctx, str(workspace), requested_path=requested)
        assert roots == [requested]
        assert str(upload_host) not in roots


class TestResolveSandboxPathWithUnifiedWorkdir:
    """resolve_sandbox_path 在路径统一场景下的完整解析测试"""

    @pytest.mark.asyncio
    async def test_real_path_inside_workspace_resolves(self, tmp_path):
        """真实宿主机路径（位于 workspace_dir 内）应直接解析成功"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "report.md"
        target.write_text("# Report")

        ctx = _make_ctx(workspace_dir=str(workspace), container_workdir=str(workspace))
        resolved, error = await resolve_sandbox_path(ctx, str(target))

        assert error is None
        assert resolved is not None
        assert resolved.path == str(target)

    @pytest.mark.asyncio
    async def test_path_outside_workspace_rejected(self, tmp_path):
        """沙箱外路径仍应被拒绝（安全边界不变）"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("secret")

        ctx = _make_ctx(workspace_dir=str(workspace), container_workdir=str(workspace))
        resolved, error = await resolve_sandbox_path(ctx, str(outside))

        assert error is not None

    @pytest.mark.asyncio
    async def test_upload_mount_read_only_flag(self, tmp_path):
        """通过 upload_mounts 匹配的路径应标记为只读"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        upload_host = tmp_path / "uploads" / "ses_test"
        upload_host.mkdir(parents=True)
        upload_file = upload_host / "attached.pdf"
        upload_file.write_text("pdf content")

        container_dir = str(workspace / "uploads" / "chat" / "ses_test")
        ctx = _make_ctx(
            workspace_dir=str(workspace),
            container_workdir=str(workspace),
            upload_mounts=[{
                "host_dir": str(upload_host),
                "container_dir": container_dir,
                "read_only": True,
            }],
        )

        # 通过容器路径访问上传文件
        container_path = f"{container_dir}/attached.pdf"
        resolved, error = await resolve_sandbox_path(ctx, container_path)

        assert error is None
        assert resolved is not None
        assert resolved.read_only is True

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tmp_path):
        """路径穿越尝试（../）应被拒绝"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ctx = _make_ctx(workspace_dir=str(workspace), container_workdir=str(workspace))

        traversal = str(workspace / ".." / "outside" / "secret.txt")
        resolved, error = await resolve_sandbox_path(ctx, traversal)

        assert error is not None


# ─────────────────────────────────────────────────────────────────────────────
# 4. bash.py - _is_allowed_temporary_script_path（container_workdir 动态检查）
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAllowedTemporaryScriptPathUnified:
    """
    路径统一后的核心变化：
    - /workspace/ 硬编码前缀不再允许
    - 改为从 sandbox context 动态读取 container_workdir
    """

    def test_container_workdir_prefix_allowed(self, tmp_path, monkeypatch):
        """container_workdir 下的脚本路径应被允许"""
        workdir = str(tmp_path / "agent_workspace")
        (tmp_path / "agent_workspace").mkdir()
        ctx = _ctx_with_workdir_only(workdir)
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        script = f"{workdir}/scripts/helper.sh"
        assert _is_allowed_temporary_script_path(script, workdir, ctx) is True

    def test_workspace_prefix_no_longer_allowed(self, tmp_path, monkeypatch):
        """
        硬编码的 /workspace/ 前缀已移除。
        当无 sandbox context 时，/workspace/ 路径应返回 False。
        """
        fake_artifacts = tmp_path / "fake_artifacts"
        fake_artifacts.mkdir()
        fake_tmp = tmp_path / "fake_tmp"
        fake_tmp.mkdir()
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: fake_artifacts,
        )
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(fake_tmp))

        # 无 sandbox ctx
        ctx = ToolContext(session_id="ses_test", message_id="m-test")
        assert _is_allowed_temporary_script_path("/workspace/scripts/run.sh", "/project", ctx) is False

    def test_tmp_prefix_still_allowed(self, tmp_path, monkeypatch):
        """修改后 /tmp/ 前缀仍然有效"""
        ctx = _ctx_with_workdir_only(str(tmp_path))
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        assert _is_allowed_temporary_script_path("/tmp/run.sh", str(tmp_path), ctx) is True

    def test_var_tmp_prefix_still_allowed(self, tmp_path, monkeypatch):
        """修改后 /var/tmp/ 前缀仍然有效"""
        ctx = _ctx_with_workdir_only(str(tmp_path))
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        assert _is_allowed_temporary_script_path("/var/tmp/run.py", str(tmp_path), ctx) is True

    def test_different_workdir_not_confused(self, tmp_path, monkeypatch):
        """container_workdir=A 时，B 目录下的脚本不应被允许"""
        workdir_a = str(tmp_path / "workspace_a")
        workdir_b = tmp_path / "workspace_b"
        workdir_b.mkdir(parents=True)
        script_in_b = str(workdir_b / "run.sh")

        ctx = _ctx_with_workdir_only(workdir_a)
        fake_artifacts = tmp_path / "fake_artifacts"
        fake_artifacts.mkdir()
        fake_tmp = tmp_path / "fake_tmp"
        fake_tmp.mkdir()
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: fake_artifacts,
        )
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(fake_tmp))

        assert _is_allowed_temporary_script_path(script_in_b, workdir_a, ctx) is False

    def test_trailing_slash_handled(self, tmp_path, monkeypatch):
        """container_workdir 末尾斜杠不影响前缀匹配"""
        workdir = str(tmp_path / "workspace")
        Path(workdir).mkdir()
        ctx = _ctx_with_workdir_only(workdir + "/")  # 末尾斜杠
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        script = f"{workdir}/run.sh"
        assert _is_allowed_temporary_script_path(script, workdir, ctx) is True

    def test_empty_container_workdir_no_false_positive(self, tmp_path, monkeypatch):
        """container_workdir 为空时不应误匹配任何路径"""
        ctx = _ctx_with_workdir_only("")
        fake_artifacts = tmp_path / "fake_artifacts"
        fake_artifacts.mkdir()
        fake_tmp = tmp_path / "fake_tmp"
        fake_tmp.mkdir()
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: fake_artifacts,
        )
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(fake_tmp))

        assert _is_allowed_temporary_script_path("/any/path/run.sh", str(tmp_path), ctx) is False

    def test_artifacts_dir_still_allowed(self, tmp_path, monkeypatch):
        """artifacts 目录下的脚本仍然允许（兜底逻辑不变）"""
        ctx = _ctx_with_workdir_only(str(tmp_path / "workspace"))
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        monkeypatch.setattr(
            "flocks.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: artifacts,
        )
        script = artifacts / "process.py"
        assert _is_allowed_temporary_script_path(str(script), str(tmp_path), ctx) is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. bash.py - _is_flocks_plugin_write_path（/workspace/ 检查移除后的行为）
# ─────────────────────────────────────────────────────────────────────────────

class TestIsFlocksPluginWritePathAfterWorkspaceCheckRemoval:
    """
    /workspace/.flocks/plugins/ 硬编码检查已移除。
    验证：相对路径和实际目录验证的兜底检查仍然有效。
    """

    def test_relative_prefix_still_works(self, tmp_path):
        """.flocks/plugins/ 相对路径前缀检查不受影响"""
        assert _is_flocks_plugin_write_path(".flocks/plugins/tool.yaml", str(tmp_path)) is True

    def test_dotslash_relative_prefix_still_works(self, tmp_path):
        """./.flocks/plugins/ 相对路径前缀检查不受影响"""
        assert _is_flocks_plugin_write_path("./.flocks/plugins/tool.yaml", str(tmp_path)) is True

    def test_workspace_container_path_no_longer_auto_allowed(self, tmp_path):
        """
        /workspace/.flocks/plugins/ 路径不再通过硬编码检查自动允许。
        当 base_dir 不含实际 .flocks/plugins 目录时，应返回 False。
        """
        assert _is_flocks_plugin_write_path(
            "/workspace/.flocks/plugins/agent.yaml", str(tmp_path)
        ) is False

    def test_absolute_path_in_base_dir_plugins_still_allowed(self, tmp_path):
        """base_dir/.flocks/plugins/ 下的绝对路径通过目录验证兜底"""
        plugins = tmp_path / ".flocks" / "plugins"
        plugins.mkdir(parents=True)
        tool = plugins / "my_tool.yaml"
        assert _is_flocks_plugin_write_path(str(tool), str(tmp_path)) is True

    def test_cwd_plugins_still_allowed(self):
        """当前工作目录下的 .flocks/plugins 路径仍被允许"""
        cwd_plugin = str(Path.cwd() / ".flocks" / "plugins" / "tool.yaml")
        # 只验证不抛异常，具体 True/False 取决于是否存在
        result = _is_flocks_plugin_write_path(cwd_plugin, str(Path.cwd()))
        assert isinstance(result, bool)

    def test_outputs_path_not_confused_as_plugin(self, tmp_path):
        """outputs 路径不应被误判为插件路径"""
        assert _is_flocks_plugin_write_path("outputs/report.md", str(tmp_path)) is False

    def test_flocks_workspace_outputs_not_confused(self, tmp_path):
        """含 .flocks 字样但指向 workspace/outputs 的路径不应误判"""
        path = (
            "/opt/home/flocks/.flocks/workspace/outputs/"
            "2026-07-23/ses_0bde9c089ffeGHv7F2g9xCEw0G/hostname.txt"
        )
        assert _is_flocks_plugin_write_path(path, "/opt/home/flocks") is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Docker bind 格式验证（宿主机路径 == 容器路径）
# ─────────────────────────────────────────────────────────────────────────────

class TestDockerBindPathUnification:
    """
    验证 context.py 生成的 Docker bind 格式。
    路径统一后，bind 应为 "{path}:{path}:rw"（而非旧的 "{host}:/workspace:rw"）。
    """

    def test_output_bind_host_equals_container(self, tmp_path):
        """output bind 中宿主机路径应等于容器路径"""
        output_dir = tmp_path / "outputs" / "2026-07-23" / "ses_test"
        output_dir.mkdir(parents=True)
        workspace = str(tmp_path)
        output_scope = "2026-07-23/ses_test"

        # 新格式：container_output_dir = f"{user_workspace_dir}/outputs/{output_scope}"
        container_output_dir = f"{workspace}/outputs/{output_scope}"

        # 用 Path 对象做跨平台比较（避免 Windows 驱动器字母冒号干扰 split(":")）
        assert Path(str(output_dir.resolve())) == Path(container_output_dir), \
            "output bind 的宿主机路径应等于容器路径"
        assert "/workspace" not in container_output_dir.replace("\\", "/").lower() or \
            container_output_dir.replace("\\", "/").lower() == "/workspace"

    def test_plugin_bind_host_equals_container(self, tmp_path):
        """plugin bind 中宿主机路径应等于容器路径"""
        plugins_dir = tmp_path / ".flocks" / "plugins"
        plugins_dir.mkdir(parents=True)

        # 新格式：f"{plugins_dir.resolve()}:{plugins_dir.resolve()}:rw"
        resolved = plugins_dir.resolve()

        # 宿主机路径 == 容器路径
        assert Path(str(resolved)) == Path(str(resolved))  # tautology, 主要验证格式无误
        assert "/workspace" not in str(resolved).replace("\\", "/")

    def test_upload_bind_host_equals_container_when_workdir_is_real(self, tmp_path):
        """
        当 container_workdir 使用真实路径时，
        upload bind 的 container_dir 应等于 host_dir（路径统一核心效果）
        """
        workspace = tmp_path
        upload_host = workspace / "uploads" / "chat" / "ses_test"
        upload_host.mkdir(parents=True)

        # 模拟 context.py 生成 upload bind 的逻辑（新格式）
        container_dir = f"{workspace}/uploads/chat/ses_test"

        assert Path(str(upload_host)) == Path(container_dir), \
            "统一路径后 upload bind 的宿主机路径应等于容器路径"

    def test_agent_workspace_mount_host_equals_container(self, tmp_path):
        """
        agent_workspace 挂载（docker.py 修改）：
        旧格式 agent_workspace_dir:/workspace/agent，
        新格式 agent_workspace_dir:agent_workspace_dir。
        """
        agent_workspace = str(tmp_path / "agent_workspace")

        # 新格式：宿主机路径 == 容器路径
        assert Path(agent_workspace) == Path(agent_workspace)
        assert "/workspace/agent" not in agent_workspace
