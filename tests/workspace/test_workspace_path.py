from types import SimpleNamespace

from smartclaw.server.routes import workspace as workspace_module


def test_normalize_workspace_path_handles_user_workspace_root(monkeypatch):
    user_workspace = "C:/smartclaw-user-workspace"
    monkeypatch.setattr(
        workspace_module,
        "_get_manager",
        lambda: SimpleNamespace(get_user_workspace_dir=lambda: user_workspace),
    )

    assert workspace_module._normalize_workspace_path(user_workspace) == ""


def test_normalize_workspace_path_strips_user_workspace_prefix(monkeypatch):
    user_workspace = "C:/smartclaw-user-workspace"
    monkeypatch.setattr(
        workspace_module,
        "_get_manager",
        lambda: SimpleNamespace(get_user_workspace_dir=lambda: user_workspace),
    )

    normalized = workspace_module._normalize_workspace_path(
        f"{user_workspace}/outputs/session/report.md"
    )

    assert normalized == "/outputs/session/report.md"
