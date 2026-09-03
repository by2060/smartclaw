import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def prune_module():
    return importlib.import_module("automated_script.prune_project")


def test_default_targets_include_jenkinsfiles(prune_module):
    assert "Jenkinsfile_docker" in prune_module.DEFAULT_TARGETS
    assert "Jenkinsfile_src" in prune_module.DEFAULT_TARGETS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("/foo/bar/", "foo/bar"), ("\\foo\\bar\\", "foo/bar"), (" foo ", " foo ")],
)
def test_normalize_target(prune_module, raw, expected):
    assert prune_module.normalize_target(raw) == expected


@pytest.mark.parametrize("raw", ["", ".", "/", "///"])
def test_normalize_target_rejects_empty_paths(prune_module, raw):
    with pytest.raises(ValueError):
        prune_module.normalize_target(raw)


def test_build_target_list_deduplicates_custom_targets_and_can_skip_defaults(prune_module):
    assert prune_module.build_target_list(["custom", "custom", "/custom/"], include_defaults=False) == ["custom"]
    assert prune_module.build_target_list(["Jenkinsfile_src"], include_defaults=True).count("Jenkinsfile_src") == 1


def test_resolve_target_rejects_absolute_and_parent_paths(prune_module, tmp_path, monkeypatch):
    monkeypatch.setattr(prune_module, "ROOT", tmp_path)
    for target in ["C:/outside.txt", "../outside.txt", "nested/../../outside.txt"]:
        with pytest.raises(ValueError):
            prune_module.resolve_target(target)


def test_resolve_target_allows_in_root_symlink_without_following_outside(prune_module, tmp_path, monkeypatch):
    monkeypatch.setattr(prune_module, "ROOT", tmp_path)
    link = tmp_path / "link"
    target = tmp_path / "outside"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if os.name == "nt":
            pytest.skip(f"当前 Windows 环境没有创建符号链接的权限：{error}")
        raise
    assert prune_module.resolve_target("link") == link


def test_delete_target_preview_does_not_mutate_file_or_directory(prune_module, tmp_path, monkeypatch):
    monkeypatch.setattr(prune_module, "ROOT", tmp_path)
    file_path = tmp_path / "file.txt"
    dir_path = tmp_path / "folder"
    file_path.write_text("keep")
    dir_path.mkdir()
    (dir_path / "child.txt").write_text("keep")

    file_result = prune_module.delete_target("file.txt", apply=False)
    dir_result = prune_module.delete_target("folder", apply=False)
    assert file_result.status == "将删除文件"
    assert dir_result.status == "将删除目录"
    assert file_path.exists() and dir_path.exists()


def test_delete_target_apply_removes_file_directory_and_symlink(prune_module, tmp_path, monkeypatch):
    monkeypatch.setattr(prune_module, "ROOT", tmp_path)
    file_path = tmp_path / "file.txt"
    dir_path = tmp_path / "folder"
    target = tmp_path / "target.txt"
    link = tmp_path / "link.txt"
    file_path.write_text("delete")
    dir_path.mkdir()
    target.write_text("target")
    try:
        link.symlink_to(target)
    except OSError as error:
        if os.name == "nt":
            pytest.skip(f"当前 Windows 环境没有创建符号链接的权限：{error}")
        raise

    assert prune_module.delete_target("file.txt", apply=True).status == "已删除文件"
    assert prune_module.delete_target("folder", apply=True).status == "已删除目录"
    assert prune_module.delete_target("link.txt", apply=True).status == "已删除符号链接"
    assert not file_path.exists() and not dir_path.exists() and not link.exists() and target.exists()
    assert prune_module.delete_target("missing.txt", apply=True).status == "不存在"


def test_print_summary_reports_each_result_category(prune_module, capsys):
    prune_module.print_summary([
        prune_module.DeletionResult("a", "已删除文件"),
        prune_module.DeletionResult("b", "不存在"),
        prune_module.DeletionResult("c", "将删除目录"),
    ])
    assert capsys.readouterr().out.strip() == "汇总：已删除 1 项，不存在 1 项，待删除 1 项，共 3 项。"


def test_main_list_targets_and_no_defaults_modes(prune_module, monkeypatch, capsys):
    monkeypatch.setattr(prune_module, "parse_args", lambda: type("Args", (), {
        "path": ["Jenkinsfile_src"], "no_defaults": True, "list_targets": True, "apply": False,
    })())
    assert prune_module.main() == 0
    assert capsys.readouterr().out.strip() == "Jenkinsfile_src"

    monkeypatch.setattr(prune_module, "parse_args", lambda: type("Args", (), {
        "path": [], "no_defaults": True, "list_targets": False, "apply": False,
    })())
    assert prune_module.main() == 0
    assert "没有可处理的目标" in capsys.readouterr().out
