"""Workflow node type specifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class NodeTypeSpec:
    type: str
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    output_keys: tuple[str, ...] = ()
    supports_branch_labels: bool = False
    executor: Literal["runtime", "control", "codegen"] = "runtime"
    lint_rules: tuple[str, ...] = ()
    ui_schema: dict[str, Any] = field(default_factory=dict)
    prompt_guidance: str = ""


NODE_TYPE_SPECS: dict[str, NodeTypeSpec] = {
    "python": NodeTypeSpec(
        type="python",
        required_fields=("code",),
        optional_fields=("description", "join", "join_mode", "join_conflict", "join_namespace_key"),
        supports_branch_labels=False,
        executor="runtime",
        lint_rules=("python_ast", "hardcoded_secret", "dangerous_import", "dangerous_call"),
        prompt_guidance="Use only for deterministic data cleanup, field mapping, and lightweight aggregation.",
    ),
    "logic": NodeTypeSpec(
        type="logic",
        required_fields=("description",),
        optional_fields=("code", "select_key", "join", "join_mode", "join_conflict", "join_namespace_key"),
        supports_branch_labels=True,
        executor="codegen",
        lint_rules=("edge_selection",),
        prompt_guidance="Use for previews or codegen-backed fallback logic; set select_key when multiple outgoing edges exist.",
    ),
    "branch": NodeTypeSpec(
        type="branch",
        required_fields=("select_key",),
        optional_fields=("description",),
        supports_branch_labels=True,
        executor="control",
        lint_rules=("edge_selection",),
        prompt_guidance="Use for deterministic routing by select_key and labelled outgoing edges.",
    ),
    "loop": NodeTypeSpec(
        type="loop",
        required_fields=("select_key",),
        optional_fields=("description",),
        supports_branch_labels=True,
        executor="control",
        lint_rules=("edge_selection",),
        prompt_guidance="Use for continue/exit style routing by select_key and labelled outgoing edges.",
    ),
    "tool": NodeTypeSpec(
        type="tool",
        required_fields=("tool_name",),
        optional_fields=("tool_args", "output_key", "description"),
        output_keys=("result",),
        supports_branch_labels=False,
        executor="runtime",
        lint_rules=("tool_exists", "tool_args_schema"),
        prompt_guidance="Prefer for deterministic tool calls; tool_args must match the registered tool schema.",
    ),
    "llm": NodeTypeSpec(
        type="llm",
        required_fields=("prompt",),
        optional_fields=("model", "output_key", "description"),
        output_keys=("result",),
        supports_branch_labels=False,
        executor="runtime",
        lint_rules=("llm_output_key"),
        prompt_guidance="Prefer for summarization, extraction, classification, and reasoning; set output_key explicitly.",
    ),
    "http_request": NodeTypeSpec(
        type="http_request",
        required_fields=("method", "url"),
        optional_fields=("headers", "body", "response_key", "description"),
        output_keys=("response", "status_code"),
        supports_branch_labels=False,
        executor="runtime",
        lint_rules=("http_required_fields", "secret_handling"),
        prompt_guidance="Prefer for direct HTTP calls; never hardcode credentials in url, headers, or body.",
    ),
    "subworkflow": NodeTypeSpec(
        type="subworkflow",
        required_fields=("workflow_id",),
        optional_fields=("inputs_mapping", "inputs_const", "output_key", "description"),
        output_keys=("output",),
        supports_branch_labels=False,
        executor="runtime",
        lint_rules=("subworkflow_exists", "subworkflow_depth"),
        prompt_guidance="Use to reuse an existing workflow; nested subworkflows are not allowed.",
    ),
}


def get_node_type_spec(node_type: str) -> NodeTypeSpec | None:
    return NODE_TYPE_SPECS.get(node_type)


def list_node_type_specs() -> list[NodeTypeSpec]:
    return list(NODE_TYPE_SPECS.values())
