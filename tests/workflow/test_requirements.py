from types import SimpleNamespace
from unittest.mock import patch

import pytest

from smartclaw.workflow import requirements as module


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/workspace/cache", "/srv/project/cache"),
        (r"\workspace\cache", "/srv/project/cache"),
        ("/workspace", "/srv/project"),
        ("workspace", "/srv/project"),
        ("/other/cache", "/other/cache"),
    ],
)
def test_to_container_abs_path_rewrites_workspace_paths(path, expected):
    assert module._to_container_abs_path("/srv/project/", path) == expected


def test_sandbox_installer_uses_rewritten_marker_and_site_package_paths():
    installer = module.SandboxRequirementsInstaller(
        installer="pip",
        marker_root="/workspace/.smartclaw/requirements",
        site_packages_dir="/workspace/.smartclaw/site-packages",
    )
    run_results = [
        SimpleNamespace(returncode=1),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0),
    ]

    with patch.object(module.subprocess, "run", side_effect=run_results) as run:
        installed = installer.ensure_installed(
            ["requests==2.32.0"],
            {
                "container_name": "workflow-test",
                "container_workdir": "/srv/project",
            },
        )

    assert installed is True
    assert run.call_count == 4
    commands = [call.args[0] for call in run.call_args_list]
    assert all(command[:7] == ["docker", "exec", "-i", "-w", "/srv/project", "workflow-test", "python3"] for command in commands)
    assert "/srv/project/.smartclaw/requirements/" in commands[0][-1]
    assert "/srv/project/.smartclaw/site-packages" in commands[1][-1]
    assert commands[2][-3:] == ["--target", "/srv/project/.smartclaw/site-packages", "requests==2.32.0"]
    assert "/srv/project/.smartclaw/requirements/" in commands[3][-1]
