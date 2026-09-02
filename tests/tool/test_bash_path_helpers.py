"""
bash.py 路径处理辅助函数单元测试

覆盖：
- _looks_like_shell_file_write / _looks_like_generated_document_write / _looks_like_temporary_script_write
- _already_session_scoped_output
- _next_available_path
- _host_path_variants
- _normalize_container_plugin_paths
- _rewrite_project_plugin_host_paths_for_sandbox
- _path_is_within
- _sandbox_project_plugins_container_root / _sandbox_project_plugins_host_root
- _is_allowed_temporary_script_path
- _is_smartclaw_plugin_write_path（补充边界用例）
- _is_user_smartclaw_plugin_write_path（补充边界用例）
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from smartclaw.tool.code.bash import (
    OUTPUT_FILE_EXTENSIONS,
    SCRIPT_FILE_EXTENSIONS,
    _already_session_scoped_output,
    _host_path_variants,
    _is_allowed_temporary_script_path,
    _is_smartclaw_plugin_write_path,
    _is_user_smartclaw_plugin_write_path,
    _looks_like_generated_document_write,
    _looks_like_shell_file_write,
    _looks_like_temporary_script_write,
    _next_available_path,
    _normalize_container_plugin_paths,
    _path_is_within,
    _rewrite_project_plugin_host_paths_for_sandbox,
    _sandbox_project_plugins_container_root,
    _sandbox_project_plugins_host_root,
)
from smartclaw.sandbox.types import BashSandboxConfig
from smartclaw.tool.registry import ToolContext


# ─────────────────────────────────────────────────────────────────────────────
# 辅助工厂
# ─────────────────────────────────────────────────────────────────────────────

def _sandbox(
    *,
    container_workdir: str = "/workspace",
    project_plugins_dir: str | None = None,
    agent_workspace_dir: str | None = None,
) -> BashSandboxConfig:
    return BashSandboxConfig(
        container_name="test-container",
        workspace_dir="/host/ws",
        container_workdir=container_workdir,
        project_plugins_dir=project_plugins_dir,
        agent_workspace_dir=agent_workspace_dir,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _looks_like_shell_file_write
# ─────────────────────────────────────────────────────────────────────────────

class TestLooksLikeShellFileWrite:
    """直接测试底层正则引擎，使用固定的后缀列表。"""

    EXTS = ("csv", "json", "md", "txt")

    def _check(self, command: str) -> str | None:
        return _looks_like_shell_file_write(command, self.EXTS)

    # ── >> 重定向 ──────────────────────────────────────────────────────────────

    def test_append_redirect_absolute_path(self):
        assert self._check("echo hello >> /workspace/outputs/report.csv") == "/workspace/outputs/report.csv"

    def test_overwrite_redirect_absolute_path(self):
        assert self._check("echo hello > /workspace/outputs/out.json") == "/workspace/outputs/out.json"

    def test_redirect_relative_path(self):
        assert self._check("echo data > results/summary.txt") == "results/summary.txt"

    def test_redirect_dotslash_path(self):
        assert self._check("echo data > ./report.md") == "./report.md"

    def test_redirect_quoted_path(self):
        # 路径 pattern 排除空格字符（[^\s'\"...]），含空格的路径不被检测
        # 带引号但无空格的路径可正常检测
        assert self._check('echo data >> "/workspace/outputs/report.csv"') == "/workspace/outputs/report.csv"

    def test_redirect_path_with_spaces_not_detected(self):
        # 含空格的路径无法被当前正则检测（已知限制）
        assert self._check('echo data >> "/workspace/outputs/my report.csv"') is None

    def test_redirect_no_match_unknown_extension(self):
        assert self._check("echo data >> /workspace/out.png") is None

    def test_redirect_stdin_redirection_not_matched(self):
        # < 不是写操作
        assert self._check("cat < /workspace/input.csv") is None

    def test_redirect_heredoc_not_matched(self):
        # << 是 heredoc，不是写文件
        assert self._check("python << EOF\nprint(1)\nEOF") is None

    # ── tee ────────────────────────────────────────────────────────────────────

    def test_tee_absolute_path(self):
        assert self._check("echo data | tee /workspace/outputs/out.txt") == "/workspace/outputs/out.txt"

    def test_tee_append_flag(self):
        assert self._check("echo data | tee -a /workspace/outputs/out.md") == "/workspace/outputs/out.md"

    def test_tee_relative_path(self):
        assert self._check("echo data | tee report.csv") == "report.csv"

    def test_tee_quoted_path(self):
        assert self._check("echo data | tee '/workspace/out.json'") == "/workspace/out.json"

    def test_tee_unknown_extension_no_match(self):
        assert self._check("echo data | tee /workspace/out.png") is None

    # ── Python open() ──────────────────────────────────────────────────────────

    def test_python_open_write_mode(self):
        assert self._check("open('/workspace/result.csv', 'w')") == "/workspace/result.csv"

    def test_python_open_append_mode(self):
        assert self._check('open("/tmp/log.txt", "a")') == "/tmp/log.txt"

    def test_python_open_read_mode_no_match(self):
        assert self._check("open('/workspace/data.csv', 'r')") is None

    def test_python_open_relative_path(self):
        assert self._check("open('output.json', 'w')") == "output.json"

    # ── Path.write_text / write_bytes ─────────────────────────────────────────

    def test_pathlib_write_text(self):
        assert self._check("Path('/workspace/report.md').write_text(content)") == "/workspace/report.md"

    def test_pathlib_write_bytes(self):
        assert self._check('Path("/tmp/data.csv").write_bytes(b"ok")') == "/tmp/data.csv"

    def test_pathlib_relative_write_text(self):
        assert self._check("Path('results/out.txt').write_text('hello')") == "results/out.txt"

    # ── PowerShell ─────────────────────────────────────────────────────────────

    def test_powershell_out_file(self):
        # 路径 pattern 要求以 / 开头或相对路径，Windows 盘符路径 C:/... 不匹配
        result = _looks_like_shell_file_write(
            "Get-Content log | Out-File -FilePath '/output/report.csv'",
            self.EXTS,
        )
        assert result == "/output/report.csv"

    def test_powershell_out_file_windows_drive_not_matched(self):
        # C:/ 开头的绝对路径不匹配当前路径 pattern（已知限制）
        result = _looks_like_shell_file_write(
            "Get-Content log | Out-File -FilePath 'C:/output/report.csv'",
            self.EXTS,
        )
        assert result is None

    def test_powershell_set_content(self):
        result = self._check("Set-Content -Path 'out.json' 'data'")
        assert result == "out.json"

    def test_powershell_add_content(self):
        result = self._check("Add-Content -FilePath 'app.txt' -Value 'line'")
        assert result == "app.txt"

    # ── 不匹配场景 ─────────────────────────────────────────────────────────────

    def test_no_write_operation_no_match(self):
        assert self._check("ls /workspace/outputs/") is None

    def test_plain_echo_no_match(self):
        assert self._check("echo report.csv") is None

    def test_cat_read_no_match(self):
        assert self._check("cat /workspace/data.csv") is None

    def test_empty_command(self):
        assert self._check("") is None

    def test_session_scoped_output_path_detected(self):
        # 含日期 + session ID 的深层绝对路径，路径中包含隐藏目录 (.smartclaw) 和混合大小写 session ID
        cmd = (
            "hostname -I > /opt/home/smartclaw/.smartclaw/workspace/outputs/"
            "2026-07-08/ses_0bde9c089ffeGHv7F2g9xCEw0G/hostname.txt"
        )
        assert self._check(cmd) == (
            "/opt/home/smartclaw/.smartclaw/workspace/outputs/"
            "2026-07-08/ses_0bde9c089ffeGHv7F2g9xCEw0G/hostname.txt"
        )


# ─────────────────────────────────────────────────────────────────────────────
# _looks_like_generated_document_write
# ─────────────────────────────────────────────────────────────────────────────

class TestLooksLikeGeneratedDocumentWrite:
    """验证 OUTPUT_FILE_EXTENSIONS 的覆盖面。"""

    def test_csv_redirect(self):
        assert _looks_like_generated_document_write("echo a >> report.csv") == "report.csv"

    def test_json_tee(self):
        assert _looks_like_generated_document_write("echo {} | tee /out/data.json") == "/out/data.json"

    def test_md_write_text(self):
        assert _looks_like_generated_document_write("Path('doc.md').write_text('x')") == "doc.md"

    def test_xlsx_redirect(self):
        assert _looks_like_generated_document_write("cmd > report.xlsx") == "report.xlsx"

    def test_yaml_redirect(self):
        assert _looks_like_generated_document_write("cmd > config.yaml") == "config.yaml"

    def test_yml_redirect(self):
        assert _looks_like_generated_document_write("cmd > config.yml") == "config.yml"

    def test_html_redirect(self):
        assert _looks_like_generated_document_write("cmd > index.html") == "index.html"

    def test_htm_redirect(self):
        assert _looks_like_generated_document_write("cmd > page.htm") == "page.htm"

    def test_log_redirect(self):
        assert _looks_like_generated_document_write("cmd > app.log") == "app.log"

    def test_pdf_redirect(self):
        assert _looks_like_generated_document_write("cmd > report.pdf") == "report.pdf"

    def test_all_extensions_covered(self):
        """OUTPUT_FILE_EXTENSIONS 中的每个扩展名都应能匹配。"""
        for ext in OUTPUT_FILE_EXTENSIONS:
            cmd = f"echo data > out.{ext}"
            result = _looks_like_generated_document_write(cmd)
            assert result is not None, f"Extension .{ext} not detected"

    def test_script_extension_not_matched(self):
        """脚本扩展名不应被 generated_document 匹配。"""
        assert _looks_like_generated_document_write("echo x > script.py") is None
        assert _looks_like_generated_document_write("echo x > script.sh") is None


# ─────────────────────────────────────────────────────────────────────────────
# _looks_like_temporary_script_write
# ─────────────────────────────────────────────────────────────────────────────

class TestLooksLikeTemporaryScriptWrite:
    """验证 SCRIPT_FILE_EXTENSIONS 的覆盖面。"""

    def test_py_redirect(self):
        assert _looks_like_temporary_script_write("echo code > helper.py") == "helper.py"

    def test_sh_redirect(self):
        assert _looks_like_temporary_script_write("echo #!/bin/sh > setup.sh") == "setup.sh"

    def test_bash_redirect(self):
        assert _looks_like_temporary_script_write("echo x > run.bash") == "run.bash"

    def test_js_redirect(self):
        assert _looks_like_temporary_script_write("echo x > tool.js") == "tool.js"

    def test_ts_redirect(self):
        assert _looks_like_temporary_script_write("echo x > tool.ts") == "tool.ts"

    def test_ps1_redirect(self):
        assert _looks_like_temporary_script_write("echo x > script.ps1") == "script.ps1"

    def test_all_extensions_covered(self):
        """SCRIPT_FILE_EXTENSIONS 中的每个扩展名都应能匹配。"""
        for ext in SCRIPT_FILE_EXTENSIONS:
            cmd = f"echo code > helper.{ext}"
            result = _looks_like_temporary_script_write(cmd)
            assert result is not None, f"Extension .{ext} not detected"

    def test_document_extension_not_matched(self):
        """文档扩展名不应被 temporary_script 匹配。"""
        assert _looks_like_temporary_script_write("echo x > report.csv") is None
        assert _looks_like_temporary_script_write("echo x > doc.md") is None


# ─────────────────────────────────────────────────────────────────────────────
# _already_session_scoped_output
# ─────────────────────────────────────────────────────────────────────────────

class TestAlreadySessionScopedOutput:

    def test_date_and_session_prefix(self):
        assert _already_session_scoped_output("/2026-07-08/ses_abc123/report.csv") is True

    def test_leading_slash_stripped(self):
        assert _already_session_scoped_output("2026-01-01/ses_xyz/file.txt") is True

    def test_only_date_no_session(self):
        assert _already_session_scoped_output("/2026-07-08/") is False

    def test_no_date_prefix(self):
        assert _already_session_scoped_output("/report.csv") is False

    def test_empty_string(self):
        assert _already_session_scoped_output("") is False

    def test_invalid_date_format(self):
        # 月份超范围，但正则只检查 \d{4}-\d{2}-\d{2} 格式，不验证日历合法性
        assert _already_session_scoped_output("/2026-99-99/session/file.txt") is True

    def test_non_date_first_part(self):
        assert _already_session_scoped_output("/outputs/session/file.txt") is False

    def test_only_date(self):
        assert _already_session_scoped_output("/2026-07-08") is False

    def test_date_and_empty_session(self):
        # 双斜杠经 split+filter 后空段被去除，等同单斜杠；第二段为 file.txt 非空，返回 True
        assert _already_session_scoped_output("/2026-07-08//file.txt") is True


# ─────────────────────────────────────────────────────────────────────────────
# _next_available_path
# ─────────────────────────────────────────────────────────────────────────────

class TestNextAvailablePath:

    def test_nonexistent_file_returns_original(self, tmp_path):
        target = tmp_path / "report.csv"
        assert _next_available_path(target) == target

    def test_existing_file_returns_indexed(self, tmp_path):
        target = tmp_path / "report.csv"
        target.write_text("original")
        result = _next_available_path(target)
        assert result == tmp_path / "report_1.csv"

    def test_multiple_existing_files_increments_index(self, tmp_path):
        target = tmp_path / "report.csv"
        target.write_text("v0")
        (tmp_path / "report_1.csv").write_text("v1")
        (tmp_path / "report_2.csv").write_text("v2")
        result = _next_available_path(target)
        assert result == tmp_path / "report_3.csv"

    def test_file_without_extension(self, tmp_path):
        target = tmp_path / "output"
        target.write_text("data")
        result = _next_available_path(target)
        assert result == tmp_path / "output_1"

    def test_file_with_dotted_stem(self, tmp_path):
        target = tmp_path / "report.v2.csv"
        target.write_text("data")
        result = _next_available_path(target)
        assert result == tmp_path / "report.v2_1.csv"


# ─────────────────────────────────────────────────────────────────────────────
# _host_path_variants
# ─────────────────────────────────────────────────────────────────────────────

class TestHostPathVariants:

    def test_posix_path_deduplicates(self):
        p = Path("/home/user/project")
        variants = _host_path_variants(p)
        # 在 POSIX 系统上三个变体相同，应去重
        assert len(variants) >= 1
        assert str(p) in variants

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_windows_path_includes_forward_slash_variant(self):
        p = Path("C:\\Users\\foo\\bar")
        variants = _host_path_variants(p)
        assert "C:/Users/foo/bar" in variants
        assert "C:\\Users\\foo\\bar" in variants

    def test_no_duplicates(self):
        p = Path("/tmp/test")
        variants = _host_path_variants(p)
        assert len(variants) == len(set(variants))

    def test_all_variants_nonempty(self):
        p = Path("/workspace/plugins")
        for v in _host_path_variants(p):
            assert v  # 不能有空字符串


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_container_plugin_paths
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeContainerPluginPaths:

    def test_backslash_in_suffix_normalized(self):
        cmd = r"cat /workspace/.smartclaw/plugins\tools\api.yaml"
        result = _normalize_container_plugin_paths(cmd, "/workspace/.smartclaw/plugins")
        assert "\\" not in result
        assert "/workspace/.smartclaw/plugins/tools/api.yaml" in result

    def test_forward_slash_unchanged(self):
        cmd = "cat /workspace/.smartclaw/plugins/tools/api.yaml"
        result = _normalize_container_plugin_paths(cmd, "/workspace/.smartclaw/plugins")
        assert result == cmd

    def test_container_root_trailing_slash_handled(self):
        # container_root 末尾带 /，应被 rstrip 处理
        cmd = "cat /workspace/.smartclaw/plugins/test.yaml"
        result = _normalize_container_plugin_paths(cmd, "/workspace/.smartclaw/plugins/")
        assert "/workspace/.smartclaw/plugins/test.yaml" in result

    def test_unrelated_path_unchanged(self):
        cmd = "cat /workspace/outputs/report.csv"
        result = _normalize_container_plugin_paths(cmd, "/workspace/.smartclaw/plugins")
        assert result == cmd


# ─────────────────────────────────────────────────────────────────────────────
# _sandbox_project_plugins_host_root / _sandbox_project_plugins_container_root
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxProjectPluginRoots:

    def test_host_root_from_project_plugins_dir(self, tmp_path):
        plugins = tmp_path / "project" / ".smartclaw" / "plugins"
        sb = _sandbox(project_plugins_dir=str(plugins))
        result = _sandbox_project_plugins_host_root(sb)
        assert result == plugins

    def test_host_root_from_agent_workspace_dir(self, tmp_path):
        workspace = tmp_path / "workspace"
        sb = _sandbox(agent_workspace_dir=str(workspace))
        result = _sandbox_project_plugins_host_root(sb)
        assert result == workspace / ".smartclaw" / "plugins"

    def test_host_root_project_plugins_dir_takes_precedence(self, tmp_path):
        plugins = tmp_path / "project" / ".smartclaw" / "plugins"
        workspace = tmp_path / "workspace"
        sb = _sandbox(project_plugins_dir=str(plugins), agent_workspace_dir=str(workspace))
        result = _sandbox_project_plugins_host_root(sb)
        assert result == plugins

    def test_host_root_none_when_no_config(self):
        sb = _sandbox()
        assert _sandbox_project_plugins_host_root(sb) is None

    def test_container_root_default_workdir(self):
        sb = _sandbox(container_workdir="/workspace")
        assert _sandbox_project_plugins_container_root(sb) == "/workspace/.smartclaw/plugins"

    def test_container_root_custom_workdir(self):
        sb = _sandbox(container_workdir="/app")
        assert _sandbox_project_plugins_container_root(sb) == "/app/.smartclaw/plugins"

    def test_container_root_trailing_slash_stripped(self):
        sb = _sandbox(container_workdir="/workspace/")
        assert _sandbox_project_plugins_container_root(sb) == "/workspace/.smartclaw/plugins"

    def test_container_root_empty_workdir_falls_back(self):
        sb = _sandbox(container_workdir="")
        # rstrip('/') of '' is '', then `or "/workspace"` kicks in
        assert _sandbox_project_plugins_container_root(sb) == "/workspace/.smartclaw/plugins"


# ─────────────────────────────────────────────────────────────────────────────
# _rewrite_project_plugin_host_paths_for_sandbox
# ─────────────────────────────────────────────────────────────────────────────

class TestRewriteProjectPluginHostPathsForSandbox:

    def test_host_path_replaced_with_container_path(self, tmp_path):
        plugins = tmp_path / "project" / ".smartclaw" / "plugins"
        sb = _sandbox(project_plugins_dir=str(plugins))
        cmd = f"cat {plugins}/tools/api.yaml"
        result = _rewrite_project_plugin_host_paths_for_sandbox(cmd, sb)
        assert str(plugins) not in result
        assert "/workspace/.smartclaw/plugins" in result

    def test_no_host_root_returns_command_unchanged(self):
        sb = _sandbox()  # no project_plugins_dir, no agent_workspace_dir
        cmd = "cat /workspace/.smartclaw/plugins/tool.yaml"
        assert _rewrite_project_plugin_host_paths_for_sandbox(cmd, sb) == cmd

    def test_backslash_in_suffix_normalized_after_rewrite(self, tmp_path):
        plugins = tmp_path / "project" / ".smartclaw" / "plugins"
        sb = _sandbox(project_plugins_dir=str(plugins))
        # 构造一个包含反斜杠后缀的命令
        cmd = f"cat {plugins}\\tools\\api.yaml"
        result = _rewrite_project_plugin_host_paths_for_sandbox(cmd, sb)
        assert "\\" not in result.split("/workspace")[1]  # 替换后部分无反斜杠


# ─────────────────────────────────────────────────────────────────────────────
# _path_is_within
# ─────────────────────────────────────────────────────────────────────────────

class TestPathIsWithin:

    def test_child_path_within_root(self, tmp_path):
        child = tmp_path / "a" / "b.txt"
        assert _path_is_within(child, tmp_path) is True

    def test_root_itself_is_within(self, tmp_path):
        assert _path_is_within(tmp_path, tmp_path) is True

    def test_sibling_not_within(self, tmp_path):
        sibling = tmp_path.parent / "other"
        assert _path_is_within(sibling, tmp_path) is False

    def test_traversal_not_within(self, tmp_path):
        traversal = tmp_path / ".." / "etc"
        assert _path_is_within(traversal, tmp_path) is False

    def test_string_arguments_accepted(self, tmp_path):
        child = str(tmp_path / "sub" / "file.txt")
        root = str(tmp_path)
        assert _path_is_within(child, root) is True


# ─────────────────────────────────────────────────────────────────────────────
# _is_allowed_temporary_script_path
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAllowedTemporaryScriptPath:

    def _ctx(self) -> ToolContext:
        return ToolContext(session_id="ses_test", message_id="m-test")

    def test_tmp_prefix_allowed(self, tmp_path, monkeypatch):
        ctx = self._ctx()
        monkeypatch.setattr(
            "smartclaw.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        assert _is_allowed_temporary_script_path("/tmp/helper.sh", "/workspace", ctx) is True

    def test_var_tmp_prefix_allowed(self, tmp_path, monkeypatch):
        ctx = self._ctx()
        monkeypatch.setattr(
            "smartclaw.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        assert _is_allowed_temporary_script_path("/var/tmp/run.py", "/workspace", ctx) is True

    def test_container_workdir_prefix_allowed(self, tmp_path, monkeypatch):
        """路径统一后，container_workdir 下的脚本路径应被允许（替代原 /workspace/ 检查）"""
        workdir = str(tmp_path / "agent_workspace")
        (tmp_path / "agent_workspace").mkdir()
        ctx = ToolContext(
            session_id="ses_test",
            message_id="m-test",
            extra={"sandbox": {"container_workdir": workdir}},
        )
        monkeypatch.setattr(
            "smartclaw.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        script_path = f"{workdir}/outputs/x.py"
        assert _is_allowed_temporary_script_path(script_path, workdir, ctx) is True

    def test_project_root_not_allowed(self, tmp_path, monkeypatch):
        ctx = self._ctx()
        # 使用一个绝对路径但不在 /tmp 或 artifacts 下的路径
        # 通过伪造 artifacts_dir 为一个不相关目录来确保检查失败
        unrelated_artifacts = tmp_path / "unrelated_artifacts"
        unrelated_artifacts.mkdir()
        monkeypatch.setattr(
            "smartclaw.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: unrelated_artifacts,
        )
        # 伪造 gettempdir 返回一个绝对不包含 cwd 的独立目录
        fake_tmp = tmp_path / "fake_tmp"
        fake_tmp.mkdir()
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(fake_tmp))
        # 使用完全不在 /tmp、/workspace 或 artifacts 下的路径
        project_dir = tmp_path / "project_root"
        project_dir.mkdir()
        assert _is_allowed_temporary_script_path("helper.py", str(project_dir), ctx) is False

    def test_artifacts_dir_allowed(self, tmp_path, monkeypatch):
        ctx = self._ctx()
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True)
        monkeypatch.setattr(
            "smartclaw.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: artifacts,
        )
        script = artifacts / "process.py"
        assert _is_allowed_temporary_script_path(str(script), str(tmp_path / "project"), ctx) is True

    def test_tempfile_gettempdir_allowed(self, tmp_path, monkeypatch):
        import tempfile as tf
        ctx = self._ctx()
        real_tmp = Path(tf.gettempdir())
        monkeypatch.setattr(
            "smartclaw.tool.code.bash._artifacts_dir_for_session",
            lambda _ctx: tmp_path / "artifacts",
        )
        script = real_tmp / "run.py"
        assert _is_allowed_temporary_script_path(str(script), "/project", ctx) is True


# ─────────────────────────────────────────────────────────────────────────────
# _is_smartclaw_plugin_write_path（补充边界用例）
# ─────────────────────────────────────────────────────────────────────────────

class TestIsSmartClawPluginWritePathExtra:

    def test_dotslash_prefix_matched(self, tmp_path):
        assert _is_smartclaw_plugin_write_path("./.smartclaw/plugins/tool.yaml", str(tmp_path)) is True

    def test_workspace_container_prefix_no_longer_matched(self, tmp_path):
        # 路径统一后 /workspace/.smartclaw/plugins/ 硬编码检查已移除；
        # 不在 base_dir/.smartclaw/plugins 实际目录下时，应返回 False
        assert _is_smartclaw_plugin_write_path("/workspace/.smartclaw/plugins/agent.yaml", str(tmp_path)) is False

    def test_relative_path_resolved_against_base(self, tmp_path):
        plugins = tmp_path / ".smartclaw" / "plugins"
        plugins.mkdir(parents=True)
        rel = plugins / "my_tool.yaml"
        assert _is_smartclaw_plugin_write_path(str(rel), str(tmp_path)) is True

    def test_outputs_path_not_matched(self, tmp_path):
        assert _is_smartclaw_plugin_write_path("outputs/report.md", str(tmp_path)) is False

    def test_smartclaw_workspace_outputs_not_matched(self, tmp_path):
        # 路径含 .smartclaw 隐藏目录，但指向 workspace/outputs 而非 plugins，不应误判为插件路径
        path = (
            "/opt/home/smartclaw/.smartclaw/workspace/outputs/"
            "2026-07-08/ses_0bde9c089ffeGHv7F2g9xCEw0G/hostname.txt"
        )
        assert _is_smartclaw_plugin_write_path(path, "/opt/home/smartclaw") is False
        assert _is_smartclaw_plugin_write_path(path, str(tmp_path)) is False

    def test_user_home_plugin_not_matched_as_project(self, tmp_path):
        user_plugin = str(Path.home() / ".smartclaw" / "plugins" / "tool.yaml")
        assert _is_smartclaw_plugin_write_path(user_plugin, str(tmp_path)) is False


# ─────────────────────────────────────────────────────────────────────────────
# _is_user_smartclaw_plugin_write_path（补充边界用例）
# ─────────────────────────────────────────────────────────────────────────────

class TestIsUserSmartClawPluginWritePathExtra:

    def test_home_plugins_path_matched(self):
        target = str(Path.home() / ".smartclaw" / "plugins" / "tools" / "api" / "demo.yaml")
        assert _is_user_smartclaw_plugin_write_path(target) is True

    def test_relative_path_returns_false(self):
        assert _is_user_smartclaw_plugin_write_path(".smartclaw/plugins/tool.yaml") is False

    def test_workspace_path_not_matched(self):
        assert _is_user_smartclaw_plugin_write_path("/workspace/.smartclaw/plugins/tool.yaml") is False

    def test_project_plugin_path_not_matched(self, tmp_path):
        project_plugin = str(tmp_path / ".smartclaw" / "plugins" / "tool.yaml")
        assert _is_user_smartclaw_plugin_write_path(project_plugin) is False

