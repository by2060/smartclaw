"""Workflow lints and best-effort static checks."""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional, Set

from .models import Edge, Node, Workflow
from .node_type_spec import NODE_TYPE_SPECS


_OUTPUTS_SUBSCRIPT_RE = re.compile(r"""outputs\[\s*['"](?P<key>[^'"]+)['"]\s*\]""")
_CN_OUTPUT_LINE_RE = re.compile(r"输出[:：]\s*([^\n。；;]+)")
_CN_BULLET_KEY_RE = re.compile(r"^\s*[-*]\s*(?P<key>[A-Za-z0-9_\-]+)\s*[:：]\s*")
_CN_SECTION_OUTPUT_RE = re.compile(r"^\s*输出要求\s*[:：]?\s*$")

# Patterns that indicate an "expensive" node (LLM call / file write).
_EXPENSIVE_CALL_RE = re.compile(
    r"""llm\.ask\s*\(|tool\.run(?:_safe)?\s*\(\s*['"]write['"]"""
)


def _split_keys(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = re.split(r"[，,、\s]+", raw)
    return [p.strip() for p in parts if p and p.strip()]


def estimate_node_output_keys(node: Node) -> Set[str]:
    keys: set[str] = set()
    if node.type == "python" and node.code:
        for m in _OUTPUTS_SUBSCRIPT_RE.finditer(node.code):
            k = (m.group("key") or "").strip()
            if k:
                keys.add(k)
        return keys
    if node.type == "logic" and node.description:
        desc = node.description
        m = _CN_OUTPUT_LINE_RE.search(desc)
        if m:
            keys.update(_split_keys((m.group(1) or "").strip()))
        lines = desc.splitlines()
        in_output_section = False
        for ln in lines:
            if _CN_SECTION_OUTPUT_RE.match(ln):
                in_output_section = True
                continue
            if in_output_section:
                if not ln.strip():
                    continue
                bm = _CN_BULLET_KEY_RE.match(ln)
                if bm:
                    k = (bm.group("key") or "").strip()
                    if k:
                        keys.add(k)
                    continue
                break
    if node.type == "tool":
        keys.add(node.output_key or "result")
    if node.type == "llm":
        keys.add(node.output_key or "result")
    if node.type == "http_request":
        keys.add(node.response_key or "response")
        keys.add("status_code")
    if node.type == "subworkflow":
        keys.add(node.output_key or "output")
    return keys


def _is_missing_field(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def lint_node_type_specs(workflow: Workflow) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for node in workflow.nodes:
        spec = NODE_TYPE_SPECS.get(node.type)
        if spec is None:
            results.append({
                "kind": "node_type_unknown",
                "severity": "error",
                "node_id": node.id,
                "node_type": node.type,
                "message": f"Node {node.id!r} uses unsupported node type {node.type!r}.",
            })
            continue
        missing = [field for field in spec.required_fields if _is_missing_field(getattr(node, field, None))]
        if missing:
            results.append({
                "kind": "node_type_required_fields_missing",
                "severity": "error",
                "node_id": node.id,
                "node_type": node.type,
                "missing_fields": missing,
                "message": f"Node {node.id!r} of type {node.type!r} is missing required fields: {missing}.",
            })
    return results


def lint_workflow_mappings(workflow: Workflow) -> List[Dict[str, Any]]:
    nodes = workflow.nodes_by_id()
    warnings: list[dict[str, Any]] = []
    for e in workflow.edges:
        if not e.mapping:
            continue
        upstream = nodes.get(e.from_)
        upstream_out = estimate_node_output_keys(upstream) if upstream is not None else set()
        for dst, src in e.mapping.items():
            src_path = "" if src is None else str(src).strip()
            if not src_path or src_path == "$":
                continue
            if src_path.startswith("$."):
                src_path = src_path[2:]
            top_key = src_path.split(".", 1)[0] if src_path else ""
            if top_key and upstream_out and top_key not in upstream_out:
                warnings.append({
                    "kind": "mapping_src_key_not_in_upstream_outputs",
                    "edge_from": e.from_,
                    "edge_to": e.to,
                    "dst_key": dst,
                    "src_path": src,
                    "upstream_type": getattr(upstream, "type", None),
                    "estimated_upstream_output_keys": sorted(upstream_out)[:50],
                    "message": (
                        f"edge.mapping maps src {src!r} but upstream node {e.from_!r} "
                        "does not appear to write that key to outputs; mapping may produce missing value"
                    ),
                })
            if dst == src and not (e.const or {}):
                warnings.append({
                    "kind": "scheme_a_suggest_omit_identity_mapping",
                    "severity": "warning",
                    "edge_from": e.from_,
                    "edge_to": e.to,
                    "dst_key": dst,
                    "src_path": src,
                    "message": (
                        "edge.mapping is an identity mapping. Scheme A recommends omitting mapping "
                        "to pass through the full payload and reduce missing-key issues."
                    ),
                })
    return warnings


# ---------------------------------------------------------------------------
# Join-safety checks
# ---------------------------------------------------------------------------


def _is_node_expensive(node: Node) -> bool:
    """Heuristic: does this node contain LLM calls or file-write tool calls?"""
    code = node.code or ""
    desc = node.description or ""
    return bool(_EXPENSIVE_CALL_RE.search(code) or _EXPENSIVE_CALL_RE.search(desc))


def _build_branch_exclusive_groups(workflow: Workflow) -> Dict[str, Set[str]]:
    """Return {branch_node_id: set_of_direct_target_ids} for branch/loop nodes.

    Edges from the same branch/loop with different labels are mutually exclusive
    at runtime (only one label fires), so their targets form an exclusive group.
    """
    nodes = workflow.nodes_by_id()
    groups: Dict[str, Set[str]] = {}
    for e in workflow.edges:
        src = nodes.get(e.from_)
        if src and src.type in ("branch", "loop") and e.label is not None:
            groups.setdefault(e.from_, set()).add(e.to)
    return groups


def _incoming_edges_are_exclusive(
    incoming_edges: List[Edge],
    nodes: Dict[str, Node],
    exclusive_groups: Dict[str, Set[str]],
) -> bool:
    """Return whether incoming edges are mutually exclusive branch/loop paths."""
    if len(incoming_edges) < 2:
        return False

    sources = [e.from_ for e in incoming_edges]
    unique_sources = set(sources)

    # Direct shape:
    #   branch --label:a--> target
    #   branch --label:b--> target
    # Runtime selects by label, so distinct labelled edges from the same
    # branch/loop source to the same target are mutually exclusive.
    if len(unique_sources) == 1:
        src = nodes.get(sources[0])
        if src and src.type in ("branch", "loop"):
            labels = [e.label for e in incoming_edges]
            if (
                all(label is not None for label in labels)
                and len(set(labels)) == len(labels)
            ):
                return True

    # Fan-out then merge shape:
    #   branch --label:a--> a --> target
    #   branch --label:b--> b --> target
    # Duplicate incoming sources are not exclusive: a repeated source can be
    # selected more than once through duplicate edges.
    if len(sources) != len(unique_sources):
        return False
    for _branch_id, targets in exclusive_groups.items():
        if unique_sources.issubset(targets):
            return True
    return False


def lint_join_requirements(workflow: Workflow) -> List[Dict[str, Any]]:
    """Check nodes with multiple incoming edges that may need ``join=true``.

    Rules:
    - If a node has >=2 incoming edges from **non-exclusive** sources and
      ``join`` is not set, emit an **error** (the node will execute multiple
      times which is almost always unintended).
    - "Exclusive" means all incoming sources are targets of the same
      ``branch``/``loop`` node with different labels (only one fires at runtime).
    """
    nodes = workflow.nodes_by_id()
    exclusive_groups = _build_branch_exclusive_groups(workflow)
    results: List[Dict[str, Any]] = []

    # incoming_from: node_id -> list of incoming edges
    incoming: Dict[str, List[Edge]] = {n.id: [] for n in workflow.nodes}
    for e in workflow.edges:
        incoming.setdefault(e.to, []).append(e)

    for nid, incoming_edges in incoming.items():
        if len(incoming_edges) < 2:
            continue
        node = nodes.get(nid)
        if node is None:
            continue
        if getattr(node, "join", False):
            continue  # already has join, OK

        sources = [e.from_ for e in incoming_edges]
        unique_sources = set(sources)
        is_exclusive = _incoming_edges_are_exclusive(
            incoming_edges, nodes, exclusive_groups
        )

        if not is_exclusive:
            results.append({
                "kind": "multi_incoming_no_join",
                "severity": "error",
                "node_id": nid,
                "sources": sorted(sources),
                "message": (
                    f"Node {nid!r} has {len(sources)} incoming edges from "
                    f"non-exclusive sources {sorted(unique_sources)} but join=false. "
                    "This will cause the node to execute multiple times. "
                    "Set join=true on this node or restructure edges."
                ),
            })
    return results


def lint_edge_selection_requirements(workflow: Workflow) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    nodes = workflow.nodes_by_id()
    outgoing: Dict[str, List[Any]] = {node.id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        outgoing.setdefault(edge.from_, []).append(edge)

    for node_id, edges in outgoing.items():
        if len(edges) <= 1:
            continue
        node = nodes.get(node_id)
        if node is None or node.type not in {"branch", "loop", "logic"}:
            continue
        if not node.select_key:
            results.append({
                "kind": "edge_selection_missing_select_key",
                "severity": "error",
                "node_id": node.id,
                "node_type": node.type,
                "message": f"{node.type} node {node.id!r} has multiple outgoing edges but no select_key.",
            })
        default_edges = [edge for edge in edges if edge.label in (None, "")]
        if len(default_edges) > 1:
            results.append({
                "kind": "edge_selection_multiple_default_edges",
                "severity": "error",
                "node_id": node.id,
                "message": f"{node.type} node {node.id!r} has multiple default outgoing edges; keep at most one empty-label fallback edge.",
            })
        for edge in edges:
            if edge.label is None:
                results.append({
                    "kind": "edge_selection_missing_label",
                    "severity": "error",
                    "edge_from": edge.from_,
                    "edge_to": edge.to,
                    "message": f"Outgoing edge {edge.from_!r}->{edge.to!r} from {node.type} node must declare label; use an empty string only for the single fallback edge.",
                })
    return results


_SECRET_LITERAL_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|authorization)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)
_DANGEROUS_IMPORTS = {"subprocess", "socket", "requests", "httpx", "aiohttp", "urllib"}
_DANGEROUS_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_output"),
}
_DANGEROUS_BUILTIN_CALLS = {"eval", "exec", "compile"}


def _call_name(node: ast.AST) -> tuple[Optional[str], str]:
    if isinstance(node, ast.Name):
        return None, node.id
    if isinstance(node, ast.Attribute):
        owner, _ = _call_name(node.value)
        if owner is None and isinstance(node.value, ast.Name):
            owner = node.value.id
        return owner, node.attr
    return None, ""


def lint_python_nodes(workflow: Workflow) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for node in workflow.nodes:
        if node.type != "python" or not node.code:
            continue
        try:
            tree = ast.parse(node.code)
        except SyntaxError as exc:
            results.append({
                "kind": "python_syntax_error",
                "severity": "error",
                "node_id": node.id,
                "message": f"Python node {node.id!r} has syntax error: {exc}",
            })
            continue

        if _SECRET_LITERAL_RE.search(node.code):
            results.append({
                "kind": "hardcoded_secret_suspected",
                "severity": "warning",
                "node_id": node.id,
                "message": f"Python node {node.id!r} appears to contain a hardcoded secret/token/password.",
            })

        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    root = alias.name.split(".", 1)[0]
                    if root in _DANGEROUS_IMPORTS:
                        results.append({
                            "kind": "python_dangerous_import",
                            "severity": "error",
                            "node_id": node.id,
                            "import": alias.name,
                            "message": f"Python node {node.id!r} imports {alias.name!r}; use tool/http_request nodes instead.",
                        })
            elif isinstance(item, ast.ImportFrom):
                root = (item.module or "").split(".", 1)[0]
                if root in _DANGEROUS_IMPORTS:
                    results.append({
                        "kind": "python_dangerous_import",
                        "severity": "error",
                        "node_id": node.id,
                        "import": item.module,
                        "message": f"Python node {node.id!r} imports from {item.module!r}; use tool/http_request nodes instead.",
                    })
            elif isinstance(item, ast.Call):
                owner, name = _call_name(item.func)
                if (owner, name) in _DANGEROUS_CALLS or (owner is None and name in _DANGEROUS_BUILTIN_CALLS):
                    call_text = f"{owner}.{name}" if owner else name
                    results.append({
                        "kind": "python_dangerous_call",
                        "severity": "error",
                        "node_id": node.id,
                        "call": call_text,
                        "message": f"Python node {node.id!r} calls {call_text!r}; use a structured tool node instead.",
                    })
    return results


def lint_tool_nodes(workflow: Workflow) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        from flocks.tool.registry import ToolRegistry
    except Exception:
        ToolRegistry = None  # type: ignore[assignment]

    for node in workflow.nodes:
        if node.type == "tool":
            if ToolRegistry is None:
                continue
            tool = ToolRegistry.get(node.tool_name or "")
            if tool is None:
                results.append({
                    "kind": "tool_node_unknown_tool",
                    "severity": "error",
                    "node_id": node.id,
                    "tool_name": node.tool_name,
                    "message": f"tool node {node.id!r} references unknown tool {node.tool_name!r}.",
                })
                continue
            schema = tool.info.get_schema()
            allowed = set((schema.properties or {}).keys())
            args = node.tool_args or {}
            unknown = sorted(str(key) for key in args if str(key) not in allowed)
            if unknown:
                results.append({
                    "kind": "tool_node_unknown_args",
                    "severity": "error",
                    "node_id": node.id,
                    "tool_name": node.tool_name,
                    "unknown_args": unknown,
                    "allowed_args": sorted(allowed),
                    "message": f"tool node {node.id!r} passes parameters not declared by tool schema: {unknown}.",
                })
        elif node.type == "llm" and not node.output_key:
            results.append({
                "kind": "llm_output_key_missing",
                "severity": "warning",
                "node_id": node.id,
                "message": f"llm node {node.id!r} should set output_key explicitly.",
            })
    return results



def lint_expensive_node_multi_trigger(workflow: Workflow) -> List[Dict[str, Any]]:
    """Detect expensive nodes (LLM / write) reachable via multiple non-exclusive paths.

    Even if an expensive node has only one direct incoming edge, it may still
    be triggered multiple times if it sits downstream of a fan-out that does
    not converge through a join.  This check handles the simpler case:
    expensive node with >=2 incoming edges and no join.
    """
    nodes = workflow.nodes_by_id()
    exclusive_groups = _build_branch_exclusive_groups(workflow)
    results: List[Dict[str, Any]] = []

    incoming: Dict[str, List[Edge]] = {n.id: [] for n in workflow.nodes}
    for e in workflow.edges:
        incoming.setdefault(e.to, []).append(e)

    for nid, incoming_edges in incoming.items():
        if len(incoming_edges) < 2:
            continue
        node = nodes.get(nid)
        if node is None:
            continue
        if getattr(node, "join", False):
            continue
        if not _is_node_expensive(node):
            continue

        sources = [e.from_ for e in incoming_edges]
        is_exclusive = _incoming_edges_are_exclusive(
            incoming_edges, nodes, exclusive_groups
        )

        if not is_exclusive:
            results.append({
                "kind": "expensive_node_multi_trigger",
                "severity": "error",
                "node_id": nid,
                "sources": sorted(sources),
                "message": (
                    f"Expensive node {nid!r} (contains LLM/write calls) has "
                    f"{len(sources)} non-exclusive incoming edges but join=false. "
                    "This may cause costly duplicate execution. "
                    "Add a join node before this expensive node."
                ),
            })
    return results


# ---------------------------------------------------------------------------
# SW-001 / SW-002: Sub-workflow lint rules
# ---------------------------------------------------------------------------


def lint_subworkflow_depth(workflow: Workflow) -> List[Dict[str, Any]]:
    """SW-001: A workflow that is itself a sub-workflow must not nest further sub-workflows.

    This is a static check that detects if the given workflow contains
    ``subworkflow`` nodes.  The caller is expected to provide the context
    (i.e. whether this workflow is being used as a sub-workflow).
    Returns errors for each ``subworkflow`` node found so the caller can
    decide severity based on nesting context.
    """
    results: List[Dict[str, Any]] = []
    for node in workflow.nodes:
        if node.type == "subworkflow":
            results.append({
                "kind": "SW-001",
                "severity": "error",
                "node_id": node.id,
                "message": (
                    f"Node {node.id!r} is a subworkflow node. "
                    "Sub-workflows cannot nest further sub-workflows (max depth=1)."
                ),
            })
    return results


def lint_subworkflow_ids(
    workflow: Workflow,
    known_workflow_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """SW-002: Every subworkflow node's workflow_id must reference an existing workflow.

    If ``known_workflow_ids`` is None the check is skipped (IDs unknown at
    static-analysis time).  Pass a set of known IDs to enable full validation.
    """
    results: List[Dict[str, Any]] = []
    if known_workflow_ids is None:
        return results
    for node in workflow.nodes:
        if node.type == "subworkflow":
            wid = node.workflow_id or ""
            if not wid:
                results.append({
                    "kind": "SW-002",
                    "severity": "error",
                    "node_id": node.id,
                    "message": f"subworkflow node {node.id!r} has no workflow_id set.",
                })
            elif wid not in known_workflow_ids:
                results.append({
                    "kind": "SW-002",
                    "severity": "error",
                    "node_id": node.id,
                    "workflow_id": wid,
                    "message": (
                        f"subworkflow node {node.id!r} references workflow_id={wid!r} "
                        "which was not found in the known workflow registry."
                    ),
                })
    return results


# ---------------------------------------------------------------------------
# Unified lint entry-point
# ---------------------------------------------------------------------------


def lint_workflow(
    workflow: Workflow,
    *,
    known_workflow_ids: Optional[Set[str]] = None,
    is_sub_workflow: bool = False,
) -> List[Dict[str, Any]]:
    """Run all lint checks and return combined results.

    Each item is a dict with at least ``kind``, ``severity``, and ``message``.
    ``severity`` is one of ``"error"`` or ``"warning"``.

    Args:
        workflow: The workflow to lint.
        known_workflow_ids: If provided, SW-002 checks whether referenced
            subworkflow IDs exist in this set.
        is_sub_workflow: If True, SW-001 is activated to disallow nested
            subworkflow nodes.
    """
    results: List[Dict[str, Any]] = []
    # Node type spec checks (errors)
    results.extend(lint_node_type_specs(workflow))
    # Existing mapping checks (warnings)
    for item in lint_workflow_mappings(workflow):
        item.setdefault("severity", "warning")
        results.append(item)
    # Join safety (errors)
    results.extend(lint_join_requirements(workflow))
    # Edge selection and node static checks (errors/warnings)
    results.extend(lint_edge_selection_requirements(workflow))
    results.extend(lint_python_nodes(workflow))
    results.extend(lint_tool_nodes(workflow))
    # Expensive node multi-trigger (errors)
    results.extend(lint_expensive_node_multi_trigger(workflow))
    # SW-001: sub-workflow nesting depth
    if is_sub_workflow:
        results.extend(lint_subworkflow_depth(workflow))
    # SW-002: subworkflow_id existence
    results.extend(lint_subworkflow_ids(workflow, known_workflow_ids=known_workflow_ids))
    return results
