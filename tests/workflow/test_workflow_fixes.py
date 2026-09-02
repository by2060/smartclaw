"""Tests for workflow reliability fixes:
- run_safe() unified tool output envelope
- Join lint checks (multi-incoming-no-join, expensive-node-multi-trigger)
- Engine input-hash dedup
"""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from smartclaw.tool import ToolResult
from smartclaw.workflow.models import Workflow
from smartclaw.workflow.engine import WorkflowEngine
from smartclaw.workflow.repl_runtime import PythonExecRuntime
from smartclaw.workflow.tools import ToolFacade
from smartclaw.workflow.tools_adapter import SmartClawToolAdapter
from smartclaw.workflow.workflow_lint import (
    lint_expensive_node_multi_trigger,
    lint_join_requirements,
    lint_workflow,
)


# ---------------------------------------------------------------------------
# Helper: mock adapter that returns controllable outputs
# ---------------------------------------------------------------------------

class _MockToolAdapter(SmartClawToolAdapter):
    """SmartClawToolAdapter subclass that bypasses real tool registry."""

    def __init__(self, outputs: Dict[str, Any] | None = None):
        # Skip super().__init__ to avoid ToolRegistry.init()
        self._ctx = None
        self._executor = None
        self._outputs = outputs or {}
        self.calls = []

    def _execute_tool_result(self, name: str, kwargs: Dict[str, Any]) -> ToolResult:
        self.calls.append((name, dict(kwargs)))
        if name in self._outputs:
            val = self._outputs[name]
            if isinstance(val, Exception):
                from smartclaw.workflow.errors import NodeExecutionError
                raise NodeExecutionError(node_id="<tool>", message=str(val))
            if isinstance(val, ToolResult):
                return val
            return ToolResult(success=True, output=val)
        return ToolResult(success=True, output=f"mock_output_for_{name}")


# ===================================================================
# 方案 1: run_safe() tests
# ===================================================================


class TestRunSafe:
    """Tests for SmartClawToolAdapter.run_safe() and ToolFacade.run_safe()."""

    def test_run_safe_str_output(self):
        adapter = _MockToolAdapter(outputs={"grep": "line1\nline2"})
        result = adapter.run_safe("grep", pattern="x")
        assert result["success"] is True
        assert result["text"] == "line1\nline2"
        assert result["obj"] == "line1\nline2"
        assert result["error"] is None

    def test_run_safe_preserves_tool_metadata(self):
        adapter = _MockToolAdapter(outputs={
            "write": ToolResult(
                success=True,
                output="Wrote file successfully.",
                title="report.md",
                metadata={"filepath": "C:/outputs/ses_123/report.md"},
            )
        })
        result = adapter.run_safe("write", filePath="C:/outputs/default-session/report.md")
        assert result["success"] is True
        assert result["metadata"]["filepath"] == "C:/outputs/ses_123/report.md"
        assert result["title"] == "report.md"

    def test_run_safe_dict_output(self):
        adapter = _MockToolAdapter(outputs={
            "memory_search": {"results": [{"id": 1}], "count": 1}
        })
        result = adapter.run_safe("memory_search", query="test")
        assert result["success"] is True
        assert isinstance(result["text"], str)
        assert '"results"' in result["text"]
        assert result["obj"] == {"results": [{"id": 1}], "count": 1}
        assert result["error"] is None

    def test_run_safe_none_output(self):
        adapter = _MockToolAdapter(outputs={"empty_tool": None})
        result = adapter.run_safe("empty_tool")
        assert result["success"] is True
        assert result["text"] == ""
        assert result["obj"] is None

    def test_run_safe_error(self):
        adapter = _MockToolAdapter(outputs={
            "bad_tool": Exception("connection timeout")
        })
        result = adapter.run_safe("bad_tool")
        assert result["success"] is False
        assert result["text"] == ""
        assert result["obj"] is None
        assert "connection timeout" in result["error"]

    def test_run_safe_unknown_tool(self):
        """Mock adapter returns a default string for unknown tools.
        In production, ToolRegistry.get() raises, but the mock doesn't.
        Here we test that run_safe still returns a valid envelope."""
        adapter = _MockToolAdapter(outputs={})
        result = adapter.run_safe("nonexistent")
        # Mock returns "mock_output_for_nonexistent" for unknown tools
        assert result["success"] is True
        assert "mock_output_for_nonexistent" in result["text"]

    def test_run_safe_explicit_error(self):
        """Explicit error entry in outputs -> run_safe catches and wraps."""
        adapter = _MockToolAdapter(outputs={
            "failing": Exception("service unavailable")
        })
        result = adapter.run_safe("failing")
        assert result["success"] is False
        assert result["obj"] is None
        assert "service unavailable" in result["error"]

    def test_facade_run_safe_delegates(self):
        adapter = _MockToolAdapter(outputs={"websearch": "search results"})
        facade = ToolFacade(adapter)
        result = facade.run_safe("websearch", query="test")
        assert result["success"] is True
        assert result["text"] == "search results"

    def test_run_safe_list_output(self):
        adapter = _MockToolAdapter(outputs={"list_tool": [1, 2, 3]})
        result = adapter.run_safe("list_tool")
        assert result["success"] is True
        assert result["obj"] == [1, 2, 3]
        assert result["text"] == "[1, 2, 3]"


class TestToolNodeRegistry:
    """Tests for workflow tool nodes using the runtime registry."""

    def test_tool_node_uses_runtime_tool_registry(self):
        adapter = _MockToolAdapter(outputs={"context_tool": "ok"})
        wf = Workflow.from_dict({
            "name": "tool_node_registry",
            "start": "call_tool",
            "nodes": [
                {
                    "id": "call_tool",
                    "type": "tool",
                    "tool_name": "context_tool",
                    "tool_args": {"static": "node"},
                    "output_key": "value",
                },
            ],
            "edges": [],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(tool_registry=adapter),
        )

        result = engine.run_node("call_tool", {"dynamic": "input"})

        assert result.error is None
        assert result.outputs == {"value": "ok"}
        assert adapter.calls == [("context_tool", {"static": "node", "dynamic": "input"})]


# ===================================================================
# 方案 2 Layer 2: Lint join checks
# ===================================================================


class TestLintJoinRequirements:
    """Tests for lint_join_requirements()."""

    def test_multi_incoming_no_join_error(self):
        """Node with 2+ non-exclusive incoming edges and no join -> error."""
        wf = Workflow.from_dict({
            "name": "bad_join",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {"id": "c", "type": "python", "code": "outputs['z'] = inputs.get('x', 0)"},
            ],
            "edges": [
                {"from": "a", "to": "c"},
                {"from": "b", "to": "c"},
            ],
        })
        results = lint_join_requirements(wf)
        assert len(results) == 1
        assert results[0]["kind"] == "multi_incoming_no_join"
        assert results[0]["severity"] == "error"
        assert results[0]["node_id"] == "c"

    def test_exclusive_branch_no_error(self):
        """Edges from same branch with different labels are exclusive -> no error."""
        wf = Workflow.from_dict({
            "name": "ok_branch",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['flag'] = True"},
                {"id": "br", "type": "branch", "select_key": "flag"},
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['x'] = 2"},
                {"id": "merge", "type": "python", "code": "outputs['r'] = inputs.get('x')"},
            ],
            "edges": [
                {"from": "start", "to": "br"},
                {"from": "br", "to": "a", "label": "true"},
                {"from": "br", "to": "b", "label": "false"},
                {"from": "a", "to": "merge"},
                {"from": "b", "to": "merge"},
            ],
        })
        results = lint_join_requirements(wf)
        assert len(results) == 0

    def test_direct_branch_labels_to_same_target_no_error(self):
        """Distinct labels from one branch to one target are exclusive."""
        wf = Workflow.from_dict({
            "name": "direct_branch_target",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['verdict'] = 'true_positive'"},
                {"id": "check_verdict", "type": "branch", "select_key": "verdict"},
                {"id": "generate_response_decision", "type": "python", "code": "outputs['ok'] = True"},
            ],
            "edges": [
                {"from": "start", "to": "check_verdict"},
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "true_positive",
                },
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "false_positive",
                },
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "needs_confirmation",
                },
            ],
        })
        results = lint_join_requirements(wf)
        assert len(results) == 0

    def test_duplicate_branch_label_to_same_target_still_errors(self):
        """Duplicate labels can select multiple edges, so they are not exclusive."""
        wf = Workflow.from_dict({
            "name": "duplicate_direct_branch_target",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['verdict'] = 'true_positive'"},
                {"id": "check_verdict", "type": "branch", "select_key": "verdict"},
                {"id": "generate_response_decision", "type": "python", "code": "outputs['ok'] = True"},
            ],
            "edges": [
                {"from": "start", "to": "check_verdict"},
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "true_positive",
                },
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "true_positive",
                },
            ],
        })
        results = lint_join_requirements(wf)
        assert len(results) == 1
        assert results[0]["kind"] == "multi_incoming_no_join"

    def test_join_true_no_error(self):
        """Node with join=true should not trigger the lint."""
        wf = Workflow.from_dict({
            "name": "ok_join",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {"id": "c", "type": "python", "code": "pass", "join": True},
            ],
            "edges": [
                {"from": "a", "to": "c"},
                {"from": "b", "to": "c"},
            ],
        })
        results = lint_join_requirements(wf)
        assert len(results) == 0

    def test_mixed_exclusive_and_non_exclusive(self):
        """Branch targets + extra direct edge -> error (not fully exclusive)."""
        wf = Workflow.from_dict({
            "name": "mixed",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['flag'] = True"},
                {"id": "br", "type": "branch", "select_key": "flag"},
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['x'] = 2"},
                {"id": "merge", "type": "python", "code": "pass"},
            ],
            "edges": [
                {"from": "start", "to": "br"},
                {"from": "br", "to": "a", "label": "true"},
                {"from": "br", "to": "b", "label": "false"},
                {"from": "a", "to": "merge"},
                {"from": "b", "to": "merge"},
                {"from": "start", "to": "merge"},  # extra non-exclusive edge
            ],
        })
        results = lint_join_requirements(wf)
        assert len(results) == 1
        assert results[0]["node_id"] == "merge"


class TestLintExpensiveNodeMultiTrigger:
    """Tests for lint_expensive_node_multi_trigger()."""

    def test_expensive_node_multi_incoming_error(self):
        """Expensive node (LLM call) with multiple non-exclusive edges -> error."""
        wf = Workflow.from_dict({
            "name": "expensive_bad",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {
                    "id": "expensive",
                    "type": "python",
                    "code": "result = llm.ask('summarize')\noutputs['summary'] = result",
                },
            ],
            "edges": [
                {"from": "a", "to": "expensive"},
                {"from": "b", "to": "expensive"},
            ],
        })
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 1
        assert results[0]["kind"] == "expensive_node_multi_trigger"
        assert results[0]["node_id"] == "expensive"

    def test_expensive_direct_branch_labels_to_same_target_no_error(self):
        """Expensive node behind one branch's distinct labels is exclusive."""
        wf = Workflow.from_dict({
            "name": "expensive_direct_branch_target",
            "start": "start",
            "nodes": [
                {"id": "start", "type": "python", "code": "outputs['verdict'] = 'true_positive'"},
                {"id": "check_verdict", "type": "branch", "select_key": "verdict"},
                {
                    "id": "generate_response_decision",
                    "type": "python",
                    "code": "result = llm.ask('decide response')\noutputs['decision'] = result",
                },
            ],
            "edges": [
                {"from": "start", "to": "check_verdict"},
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "true_positive",
                },
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "false_positive",
                },
                {
                    "from": "check_verdict",
                    "to": "generate_response_decision",
                    "label": "needs_confirmation",
                },
            ],
        })
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 0

    def test_non_expensive_node_no_error(self):
        """Non-expensive node with multiple edges -> no error from this check."""
        wf = Workflow.from_dict({
            "name": "cheap_ok",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {"id": "c", "type": "python", "code": "outputs['z'] = 3"},
            ],
            "edges": [
                {"from": "a", "to": "c"},
                {"from": "b", "to": "c"},
            ],
        })
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 0

    @pytest.mark.parametrize("call", [
        "tool.run('write', filePath='out.md', content='hi')",
        "tool.run_safe('write', filePath='out.md', content='hi')",
    ])
    def test_write_tool_detected_as_expensive(self, call):
        """Nodes calling write through tool.run/run_safe are detected as expensive."""
        wf = Workflow.from_dict({
            "name": "write_bad",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {
                    "id": "writer",
                    "type": "python",
                    "code": call,
                },
            ],
            "edges": [
                {"from": "a", "to": "writer"},
                {"from": "b", "to": "writer"},
            ],
        })
        results = lint_expensive_node_multi_trigger(wf)
        assert len(results) == 1
        assert results[0]["node_id"] == "writer"


class TestLintWorkflowUnified:
    """Tests for lint_workflow() unified entry point."""

    def test_lint_workflow_combines_all_checks(self):
        """lint_workflow() should return results from all check functions."""
        wf = Workflow.from_dict({
            "name": "combined",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = 2"},
                {"id": "c", "type": "python", "code": "pass"},
            ],
            "edges": [
                {"from": "a", "to": "c"},
                {"from": "b", "to": "c"},
            ],
        })
        results = lint_workflow(wf)
        kinds = {r["kind"] for r in results}
        assert "multi_incoming_no_join" in kinds

    def test_lint_workflow_clean(self):
        """A well-formed workflow should produce no lint results."""
        wf = Workflow.from_dict({
            "name": "clean",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
            ],
            "edges": [{"from": "a", "to": "b"}],
        })
        results = lint_workflow(wf)
        assert len(results) == 0


# ===================================================================
# 方案 2 Layer 3: Engine dedup
# ===================================================================


class TestEngineDedup:
    """Tests for engine input-hash dedup."""

    def test_dedup_different_inputs_both_execute(self):
        """When a node receives different inputs from two sources, both execute (no dedup)."""
        wf = Workflow.from_dict({
            "name": "dedup_test",
            "start": "a",
            "nodes": [
                # a fans out to b and c (python node sends to all outgoing edges)
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                {"id": "b", "type": "python", "code": "outputs['x'] = 2"},
                {"id": "d", "type": "python", "code": "outputs['result'] = inputs.get('x', 0)"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "d"},
                {"from": "b", "to": "d"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            stop_on_error=False,
        )
        result = engine.run(initial_inputs={})

        # a -> d (inputs: {x: 1}), b -> d (inputs: {x: 2})
        # Different inputs -> both should execute
        d_steps = [s for s in result.history if s.node_id == "d"]
        assert len(d_steps) == 2
        results = {s.outputs.get("result") for s in d_steps}
        assert results == {1, 2}

    def test_dedup_skips_truly_identical_inputs(self):
        """Identical inputs to the same node -> second execution is skipped."""
        wf = Workflow.from_dict({
            "name": "dedup_identical",
            "start": "a",
            "nodes": [
                {"id": "a", "type": "python", "code": "outputs['x'] = 1"},
                # Two edges from a to b (rare but possible)
                {"id": "b", "type": "python", "code": "outputs['y'] = inputs.get('x')"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "b"},
            ],
        })
        engine = WorkflowEngine(
            wf,
            runtime=PythonExecRuntime(),
            stop_on_error=False,
        )
        result = engine.run(initial_inputs={})

        # a executes once. b enqueued twice with identical inputs (x=1).
        # Dedup should skip the second execution of b.
        b_steps = [s for s in result.history if s.node_id == "b"]
        assert len(b_steps) == 1
        assert b_steps[0].outputs.get("y") == 1


# ===================================================================
# 方案 3: Example workflow.json validation
# ===================================================================


class TestExampleWorkflow:
    """Validate the fixed example workflow.json."""

    def test_example_workflow_loads(self):
        """Example workflow.json should be valid and loadable."""
        import os
        wf_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".smartclaw",
            "workflow",
            "search_summary",
            "workflow.json",
        )
        if not os.path.exists(wf_path):
            pytest.skip("Example workflow.json not found")

        with open(wf_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        wf = Workflow.from_dict(data)
        assert wf.start == "search_web"
        assert len(wf.nodes) == 6

    def test_example_workflow_no_lint_errors(self):
        """Example workflow should pass all lint checks (no errors)."""
        import os
        wf_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".smartclaw",
            "workflow",
            "search_summary",
            "workflow.json",
        )
        if not os.path.exists(wf_path):
            pytest.skip("Example workflow.json not found")

        with open(wf_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        wf = Workflow.from_dict(data)
        results = lint_workflow(wf)
        errors = [r for r in results if r.get("severity") == "error"]
        assert len(errors) == 0, f"Lint errors found: {errors}"

    def test_no_exec_json_files(self):
        """workflow-exec.json and workflow-exec-b.json should not exist."""
        import os
        base = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".smartclaw",
            "workflow",
            "search_summary",
        )
        assert not os.path.exists(os.path.join(base, "workflow-exec.json"))
        assert not os.path.exists(os.path.join(base, "workflow-exec-b.json"))
