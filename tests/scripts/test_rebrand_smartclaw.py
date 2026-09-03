import importlib

import pytest


@pytest.fixture
def rebrand_module():
    return importlib.import_module("automated_script.rebrand_smartclaw")


def test_should_skip_matches_exact_paths_without_skipping_same_named_files(
    rebrand_module, tmp_path, monkeypatch
):
    monkeypatch.setattr(rebrand_module, "ROOT", tmp_path)

    assert rebrand_module.should_skip(tmp_path / "aaa" / "bbb.py") is True
    assert rebrand_module.should_skip(tmp_path / "aaa" / "bbb.py" / "child.txt") is True
    assert rebrand_module.should_skip(
        tmp_path / ".smartclaw" / "plugins" / "tools" / "python" / "report_generator.py"
    ) is True
    assert rebrand_module.should_skip(
        tmp_path / "other" / "report_generator.py"
    ) is False


def test_should_skip_keeps_global_directory_and_filename_rules(
    rebrand_module, tmp_path, monkeypatch
):
    monkeypatch.setattr(rebrand_module, "ROOT", tmp_path)

    assert rebrand_module.should_skip(tmp_path / "node_modules" / "module.js") is True
    assert rebrand_module.should_skip(tmp_path / "nested" / rebrand_module.SCRIPT_PATH.name) is True
    assert rebrand_module.should_skip(tmp_path / "normal" / "keep.py") is False


def test_should_skip_handles_paths_outside_root_without_false_positive(rebrand_module, tmp_path, monkeypatch):
    monkeypatch.setattr(rebrand_module, "ROOT", tmp_path / "repo")
    assert rebrand_module.should_skip(tmp_path / "aaa" / "bbb.py") is False


def test_iter_repo_files_excludes_exact_skip_paths(rebrand_module, tmp_path, monkeypatch):
    monkeypatch.setattr(rebrand_module, "ROOT", tmp_path)
    keep = tmp_path / "keep.py"
    skipped = tmp_path / "aaa" / "bbb.py"
    nested_skipped = tmp_path / ".smartclaw" / "plugins" / "tools" / "python" / "report_generator.py"
    keep.write_text("keep")
    skipped.parent.mkdir(parents=True)
    skipped.write_text("skip")
    nested_skipped.parent.mkdir(parents=True)
    nested_skipped.write_text("skip")

    assert list(rebrand_module.iter_repo_files(tmp_path)) == [keep]

