"""
沙箱上下文解析

对齐 OpenClaw sandbox/context.ts：
- 核心入口: 根据配置 + 会话信息组装完整沙箱上下文
- 协调 workspace 创建、容器 ensure、注册表更新
"""

import os
from typing import Any, Dict, Optional

from .config import resolve_sandbox_config_for_agent
from .docker import ensure_sandbox_container
from .prune import maybe_prune_sandboxes
from .runtime_status import resolve_sandbox_runtime_status
from .shared import (
    get_default_workspace_root,
    resolve_sandbox_scope_key,
    resolve_sandbox_workspace_dir,
)
from .types import SandboxContext, SandboxWorkspaceInfo
from .uploads import get_session_upload_mounts, upload_mount_binds
from .workspace import ensure_sandbox_workspace

from flocks.utils.log import Log
from flocks.workspace.manager import WorkspaceManager

log = Log.create(service="sandbox.context")


async def resolve_sandbox_context(
    config_data: Optional[Dict[str, Any]] = None,
    session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    main_session_key: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> Optional[SandboxContext]:
    """
    解析沙箱上下文。

    对齐 OpenClaw resolveSandboxContext：
    1. 判定是否需要沙箱化
    2. 解析配置
    3. 触发自动清理
    4. 创建沙箱工作区
    5. 确保容器就绪
    6. 返回完整上下文

    Args:
        config_data: 完整配置字典
        session_key: 当前会话标识
        agent_id: Agent 标识
        main_session_key: 主会话标识
        workspace_dir: Agent 工作区目录

    Returns:
        SandboxContext 或 None（不需要沙箱化时）
    """
    raw_session_key = (session_key or "").strip()
    if not raw_session_key:
        return None

    # 判定是否需要沙箱化
    runtime = resolve_sandbox_runtime_status(
        config_data=config_data,
        session_key=raw_session_key,
        agent_id=agent_id,
        main_session_key=main_session_key,
    )
    if not runtime.sandboxed:
        return None

    # 解析配置
    cfg = resolve_sandbox_config_for_agent(config_data, runtime.agent_id)

    # 触发自动清理
    await maybe_prune_sandboxes(cfg)

    # 解析工作区路径
    agent_workspace_dir = os.path.expanduser(
        workspace_dir or os.getcwd()
    )
    workspace_root = os.path.expanduser(
        cfg.workspace_root or get_default_workspace_root()
    )
    scope_key = resolve_sandbox_scope_key(cfg.scope, raw_session_key)

    sandbox_workspace_dir = (
        workspace_root
        if cfg.scope == "shared"
        else resolve_sandbox_workspace_dir(workspace_root, scope_key)
    )

    # 决定实际工作目录
    effective_workspace_dir = (
        agent_workspace_dir
        if cfg.workspace_access == "rw"
        else sandbox_workspace_dir
    )

    # 创建沙箱工作区
    if effective_workspace_dir == sandbox_workspace_dir:
        await ensure_sandbox_workspace(
            workspace_dir=sandbox_workspace_dir,
            seed_from=agent_workspace_dir,
        )
        os.makedirs(
            os.path.join(effective_workspace_dir, ".flocks", "workspace"),
            exist_ok=True,
        )
    else:
        os.makedirs(effective_workspace_dir, exist_ok=True)
    # 文件上传目录挂载到沙箱新增
    upload_mounts = []
    seen_upload_sessions = set()
    for upload_session_key, create_upload_dir in (
        (raw_session_key, True),
        ((main_session_key or "").strip(), False),
    ):
        if not upload_session_key or upload_session_key in seen_upload_sessions:
            continue
        seen_upload_sessions.add(upload_session_key)
        upload_mounts.extend(
            get_session_upload_mounts(
                upload_session_key,
                container_workdir=cfg.docker.workdir,
                create=create_upload_dir,
            )
        )
    upload_binds = upload_mount_binds(upload_mounts)
    output_session_key = ((main_session_key or "").strip() or raw_session_key)
    workspace_manager = WorkspaceManager.get_instance()
    output_dir = workspace_manager.get_outputs_dir(output_session_key)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    container_workdir = cfg.docker.workdir.replace("\\", "/").rstrip("/") or "/workspace"
    user_workspace_dir = workspace_manager.get_user_workspace_dir()
    user_outputs_root = user_workspace_dir / "outputs"
    user_outputs_root.mkdir(parents=True, exist_ok=True)
    project_plugins_dir = os.path.join(agent_workspace_dir, ".flocks", "plugins")
    os.makedirs(project_plugins_dir, exist_ok=True)
    '''output_binds = [
        f"{output_dir.resolve()}:{container_workdir}/outputs",
        f"{output_dir.resolve()}:{container_workdir}/output",
        f"{artifacts_dir.resolve()}:{container_workdir}/artifacts",
        f"{user_outputs_root.resolve()}:{container_workdir}/.flocks/workspace/outputs:rw",
    ]'''
    output_binds = [
        f"{output_dir.resolve()}:{container_workdir}/outputs:rw",
        f"{artifacts_dir.resolve()}:{container_workdir}/artifacts:rw"
    ]
    plugin_binds = [
        f"{os.path.abspath(project_plugins_dir)}:{container_workdir}/.flocks/plugins:rw",
    ]
    artifact_binds = [*upload_binds, *output_binds, *plugin_binds]
    if artifact_binds:
        docker_cfg = cfg.docker.model_copy(deep=True)
        binds = list(docker_cfg.binds or [])
        for bind in artifact_binds:
            if bind not in binds:
                binds.append(bind)
        env = dict(docker_cfg.env or {})
        env["FLOCKS_WORKSPACE_DIR"] = container_workdir
        env["FLOCKS_OUTPUTS_DIR"] = f"{container_workdir}/outputs"
        env["FLOCKS_ARTIFACTS_DIR"] = f"{container_workdir}/outputs/artifacts"
        env["FLOCKS_SESSION_ID"] = output_session_key
        docker_cfg.binds = binds
        docker_cfg.env = env
        cfg = cfg.model_copy(update={"docker": docker_cfg})
    # -----------------end---------------------------
    # 确保容器就绪
    container_name = await ensure_sandbox_container(
        session_key=raw_session_key,
        workspace_dir=effective_workspace_dir,
        agent_workspace_dir=agent_workspace_dir,
        cfg=cfg,
    )

    log.info(
        "sandbox.context_resolved",
        {
            "session_key": raw_session_key,
            "container": container_name,
            "workspace_access": cfg.workspace_access,
            "scope": cfg.scope,
        },
    )

    return SandboxContext(
        enabled=True,
        session_key=raw_session_key,
        workspace_dir=effective_workspace_dir,
        agent_workspace_dir=agent_workspace_dir,
        workspace_access=cfg.workspace_access,
        container_name=container_name,
        container_workdir=cfg.docker.workdir,
        docker=cfg.docker,
        tools=cfg.tools,
        # 文件上传目录挂载到沙箱新增
        upload_mounts=[mount.model_dump() for mount in upload_mounts],
    )


async def ensure_sandbox_workspace_for_session(
    config_data: Optional[Dict[str, Any]] = None,
    session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    main_session_key: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> Optional[SandboxWorkspaceInfo]:
    """
    确保沙箱工作区存在（轻量版，不创建容器）。

    对齐 OpenClaw ensureSandboxWorkspaceForSession。
    """
    raw_session_key = (session_key or "").strip()
    if not raw_session_key:
        return None

    runtime = resolve_sandbox_runtime_status(
        config_data=config_data,
        session_key=raw_session_key,
        agent_id=agent_id,
        main_session_key=main_session_key,
    )
    if not runtime.sandboxed:
        return None

    cfg = resolve_sandbox_config_for_agent(config_data, runtime.agent_id)

    agent_workspace_dir = os.path.expanduser(workspace_dir or os.getcwd())
    workspace_root = os.path.expanduser(
        cfg.workspace_root or get_default_workspace_root()
    )
    scope_key = resolve_sandbox_scope_key(cfg.scope, raw_session_key)

    sandbox_workspace_dir = (
        workspace_root
        if cfg.scope == "shared"
        else resolve_sandbox_workspace_dir(workspace_root, scope_key)
    )

    effective_workspace_dir = (
        agent_workspace_dir
        if cfg.workspace_access == "rw"
        else sandbox_workspace_dir
    )

    if effective_workspace_dir == sandbox_workspace_dir:
        await ensure_sandbox_workspace(
            workspace_dir=sandbox_workspace_dir,
            seed_from=agent_workspace_dir,
        )
    else:
        os.makedirs(effective_workspace_dir, exist_ok=True)

    return SandboxWorkspaceInfo(
        workspace_dir=effective_workspace_dir,
        container_workdir=cfg.docker.workdir,
    )
