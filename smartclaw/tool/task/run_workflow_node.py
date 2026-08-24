"""
Run Workflow Node Tool - Execute a single workflow node in isolation.

Intended for step-by-step testing and debugging during workflow development.
Follows the same interface as the POST /api/workflow/{id}/run-node endpoint.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

from smartclaw.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from smartclaw.utils.log import Log
from smartclaw.workflow.artifact_service import WorkflowArtifactFiles, write_workflow_artifact
from smartclaw.workflow.fs_store import read_workflow_from_fs, resolve_workflow_id_from_source


log = Log.create(service="tool.run_workflow_node")

DESCRIPTION = """Execute a single workflow node in isolation for step-by-step testing.

Use this tool when testing a workflow node-by-node (BFS order):
1. Call with the first node and the sample input data.
2. Pass each node's `outputs` as `inputs` to the next node.
3. Fix errors in `workflow.json`, then re-run the failing node until `success=true`.
4. After all nodes pass, run the full workflow with `run_workflow`.

Parameters:
- workflow: Workflow definition (dict) or absolute path to workflow.json.
- node_id: ID of the node to execute (must exist in the workflow).
- inputs: Input data for the node (use previous node's outputs for downstream nodes).

Returns:
- node_id, outputs, stdout, error, traceback, duration_ms, success
"""

DESCRIPTION_CN = """隔离执行单个工作流节点，用于逐步测试。

当按节点（BFS 顺序）测试工作流时使用此工具：
1. 使用第一个节点和示例输入数据调用。
2. 将每个节点的 `outputs` 作为下一个节点的 `inputs`。
3. 修复 `workflow.json` 中的错误，然后重新运行失败节点，直到 `success=true`。
4. 所有节点通过后，使用 `run_workflow` 运行完整工作流。

参数：
- workflow：工作流定义（dict）或 workflow.json 的绝对路径。
- node_id：要执行的节点 ID（必须存在于工作流中）。
- inputs：节点输入数据（下游节点可使用前一节点的 outputs）。

返回：
- node_id、outputs、stdout、error、traceback、duration_ms、success
"""


def _load_workflow_source(workflow: Union[Dict[str, Any], str]) -> tuple[Dict[str, Any], Optional[str], Optional[str]]:
    """Resolve workflow parameter to (workflow dict, workflow path, workflow id)."""
    if isinstance(workflow, dict):
        workflow_id = resolve_workflow_id_from_source(workflow)
        return workflow, None, workflow_id
    raw = str(workflow).strip()
    try:
        workflow_dict = json.loads(raw)
        if not isinstance(workflow_dict, dict):
            raise ValueError("workflow JSON string must be an object")
        workflow_id = resolve_workflow_id_from_source(workflow_dict)
        return workflow_dict, None, workflow_id
    except json.JSONDecodeError:
        existing = read_workflow_from_fs(raw)
        if existing is not None:
            return existing["workflowJson"], str(existing.get("workflowPath") or ""), str(existing.get("id") or raw)
        p = Path(raw).expanduser()
        if p.exists() and p.is_file():
            with open(p, encoding="utf-8") as f:
                workflow_dict = json.load(f)
            return workflow_dict, str(p), resolve_workflow_id_from_source(p)
        raise ValueError(
            f"Unsupported workflow value. Provide a workflow ID, workflow dict, or a valid workflow.json file path. Got: {raw!r}"
        )


def _run_node_sync(
    workflow_dict: Dict[str, Any],
    node_id: str,
    inputs: Dict[str, Any],
    sandbox: Optional[Dict[str, Any]] = None,
    workflow_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous helper: run one node via WorkflowEngine.run_node()."""
    from smartclaw.workflow.models import Workflow as WfModel
    from smartclaw.workflow.engine import WorkflowEngine
    from smartclaw.workflow.repl_runtime import PythonExecRuntime, SandboxPythonExecRuntime

    wf = WfModel.from_dict(workflow_dict)
    runtime = SandboxPythonExecRuntime(sandbox=sandbox) if sandbox else PythonExecRuntime()
    engine = WorkflowEngine(wf, runtime=runtime, workflow_path=workflow_path)
    step = engine.run_node(node_id, inputs)
    return {
        "node_id": step.node_id,
        "outputs": step.outputs,
        "stdout": step.stdout or "",
        "error": step.error,
        "traceback": step.traceback,
        "duration_ms": step.duration_ms,
        "success": step.error is None,
    }


async def _record_node_test_result(
    workflow_id: Optional[str],
    node_id: str,
    result: Dict[str, Any],
    ctx: ToolContext,
) -> None:
    if not workflow_id:
        return
    try:
        data = read_workflow_from_fs(workflow_id) or {}
        current = data.get("nodeTestResults") or {}
        if not isinstance(current, dict):
            current = {}
        current[node_id] = dict(result)
        await write_workflow_artifact(
            workflow_id,
            WorkflowArtifactFiles(node_test_results=current),
            mode="update",
            actor=ctx.agent,
            event_publisher=ctx.event_publish_callback,
        )
    except Exception as exc:
        log.warning("run_workflow_node.record_failed", {
            "workflow_id": workflow_id,
            "node_id": node_id,
            "error": str(exc),
        })


def _format_node_result(result: Dict[str, Any]) -> str:
    """Format node result as a readable string."""
    lines = []
    node_id = result.get("node_id", "?")
    success = result.get("success", False)
    duration_ms = result.get("duration_ms")

    status_icon = "✓" if success else "✗"
    dur_str = f" ({duration_ms:.1f}ms)" if duration_ms is not None else ""
    lines.append(f"[{status_icon}] Node: {node_id}{dur_str}")

    stdout = result.get("stdout", "")
    if stdout and stdout.strip():
        lines.append("\nStdout:")
        for line in stdout.rstrip().splitlines():
            lines.append(f"  {line}")

    if not success:
        error = result.get("error", "")
        lines.append(f"\nError: {error}")
        tb = result.get("traceback", "")
        if tb:
            lines.append("Traceback:")
            for line in tb.rstrip().splitlines():
                lines.append(f"  {line}")
    else:
        outputs = result.get("outputs", {})
        if outputs:
            lines.append("\nOutputs:")
            try:
                lines.append(json.dumps(outputs, indent=2, ensure_ascii=False))
            except Exception:
                lines.append(str(outputs))

    return "\n".join(lines)


@ToolRegistry.register_function(
    name="run_workflow_node",
    description=DESCRIPTION,
    description_cn=DESCRIPTION_CN,
    category=ToolCategory.SYSTEM,
    requires_confirmation=False,
    parameters=[
        ToolParameter(
            name="workflow",
            type=ParameterType.OBJECT,
            description="Workflow definition (dict) or absolute path to workflow.json.",
            required=True,
            json_schema={
                "anyOf": [
                    {"type": "object", "description": "Workflow JSON as a dict"},
                    {"type": "string", "description": "Absolute path to workflow.json"},
                ]
            },
        ),
        ToolParameter(
            name="node_id",
            type=ParameterType.STRING,
            description="ID of the node to execute.",
            required=True,
        ),
        ToolParameter(
            name="inputs",
            type=ParameterType.OBJECT,
            description="Input data for the node. Use the previous node's outputs for downstream nodes.",
            required=False,
            default={},
            json_schema={"type": "object", "additionalProperties": True},
        ),
    ],
)
async def run_workflow_node_tool(
    ctx: ToolContext,
    workflow: Union[Dict[str, Any], str],
    node_id: str,
    inputs: Optional[Dict[str, Any]] = None,
) -> ToolResult:
    """Execute a single workflow node in isolation."""
    try:
        workflow_dict, workflow_path, workflow_id = _load_workflow_source(workflow)
    except (ValueError, json.JSONDecodeError, FileNotFoundError) as e:
        return ToolResult(success=False, error=str(e))

    workflow_name = str(workflow_dict.get("name") or workflow_id or "unknown workflow")
    display_workflow_id = str(workflow_id or workflow_dict.get("id") or workflow_name)
    workflow_id_for_permission = workflow_id if workflow_path else None
    from smartclaw.agent.controls import agent_allowed_workflows, agent_allows_workflow_execution

    if not await agent_allows_workflow_execution(
        ctx.agent,
        workflow_id=workflow_id_for_permission,
        workflow_path=workflow_path,
        session_id=ctx.session_id,
        extra=getattr(ctx, "extra", None),
    ):
        allowed = await agent_allowed_workflows(ctx.agent)
        allowed_text = ", ".join(allowed) or "none"
        return ToolResult(
            success=False,
            error=(
                f'Agent "{ctx.agent}" is not allowed to execute workflow '
                f'"{display_workflow_id}". Allowed workflows: {allowed_text}'
            ),
            metadata={
                "blocked_by_agent_workflows": True,
                "agent": ctx.agent,
                "workflow_id": display_workflow_id,
                "workflow_name": workflow_name,
            },
        )

    node_inputs = dict(inputs or {})
    if workflow_path:
        resolved_workflow_path = str(Path(workflow_path).expanduser().resolve())
        node_inputs.setdefault("_workflow_path", resolved_workflow_path)
        node_inputs.setdefault("_workflow_dir", str(Path(resolved_workflow_path).parent))

    log.info("run_workflow_node.start", {
        "node_id": node_id,
        "workflow_name": workflow_dict.get("name", "?"),
    })

    NODE_TIMEOUT_SECONDS = 120
    sandbox = None
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    raw_sandbox = extra.get("sandbox")
    if isinstance(raw_sandbox, dict):
        sandbox = dict(raw_sandbox)
    elif hasattr(raw_sandbox, "model_dump"):
        dumped = raw_sandbox.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            sandbox = dumped

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_node_sync, workflow_dict, node_id, node_inputs, sandbox, workflow_path),
            timeout=NODE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        msg = f"Node '{node_id}' timed out after {NODE_TIMEOUT_SECONDS}s. Check for infinite loops, blocking I/O, or slow external calls."
        log.error("run_workflow_node.timeout", {"node_id": node_id, "timeout_s": NODE_TIMEOUT_SECONDS})
        return ToolResult(success=False, error=msg)
    except KeyError:
        nodes = list(workflow_dict.get("nodes", []))
        node_ids = [n.get("id") for n in nodes if isinstance(n, dict)]
        return ToolResult(
            success=False,
            error=f"Node '{node_id}' not found in workflow. Available nodes: {node_ids}",
        )
    except Exception as e:
        log.error("run_workflow_node.error", {"node_id": node_id, "error": str(e)})
        return ToolResult(success=False, error=f"Failed to run node '{node_id}': {e}")

    record_payload = {
        **result,
        "inputs": node_inputs,
        "checkedAt": int(time.time() * 1000),
    }
    await _record_node_test_result(workflow_id, node_id, record_payload, ctx)
    output_text = _format_node_result(result)

    log.info("run_workflow_node.done", {
        "node_id": node_id,
        "success": result["success"],
        "duration_ms": result.get("duration_ms"),
    })

    return ToolResult(
        success=result["success"],
        output=output_text,
        error=result.get("error"),
        title=f"Node: {node_id}",
        metadata={
            "node_id": result["node_id"],
            "outputs": result["outputs"],
            "stdout": result["stdout"],
            "error": result.get("error"),
            "traceback": result.get("traceback"),
            "duration_ms": result.get("duration_ms"),
            "success": result["success"],
        },
    )
