from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

from nooshare_flow import (
    Flow,
    FlowContext,
    FlowExecutionError,
    FlowStatus,
    run_flow,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "nooshare_flow"


class FlowRuntimeTests(unittest.TestCase):
    def test_direct_execution_returns_output_and_record(self) -> None:
        received_context: list[FlowContext] = []
        flow = Flow(
            name="text.normalize",
            version="2",
            execute=lambda value, context: (
                received_context.append(context) or value.strip().lower()
            ),
        )
        context = FlowContext(run_id="direct-001", attempt=2)

        execution = run_flow(flow, "  Hello World  ", context=context)

        self.assertEqual(execution.output, "hello world")
        self.assertEqual(received_context, [context])
        self.assertEqual(execution.record.flow_name, "text.normalize")
        self.assertEqual(execution.record.flow_version, "2")
        self.assertEqual(execution.record.run_id, "direct-001")
        self.assertEqual(execution.record.attempt, 2)
        self.assertEqual(execution.record.status, FlowStatus.SUCCEEDED)
        self.assertIsNotNone(execution.record.started_at.tzinfo)
        self.assertIsNotNone(execution.record.finished_at.tzinfo)
        self.assertGreaterEqual(execution.record.duration_seconds, 0)

    def test_context_is_created_when_caller_does_not_supply_one(self) -> None:
        seen: list[FlowContext] = []
        flow = Flow(
            name="context.inspect",
            execute=lambda value, context: seen.append(context) or value,
        )

        execution = run_flow(flow, 7)

        self.assertEqual(execution.output, 7)
        self.assertEqual(seen[0].run_id, execution.record.run_id)
        self.assertEqual(seen[0].attempt, 1)

    def test_failure_preserves_original_exception_and_run_record(self) -> None:
        def fail(_: str, __: FlowContext) -> str:
            raise ValueError("invalid input")

        flow = Flow(name="text.validate", execute=fail)
        context = FlowContext(run_id="failed-001")

        with self.assertRaises(FlowExecutionError) as caught:
            run_flow(flow, "bad", context=context)

        error = caught.exception
        self.assertIsInstance(error.__cause__, ValueError)
        self.assertEqual(str(error.__cause__), "invalid input")
        self.assertEqual(error.record.run_id, "failed-001")
        self.assertEqual(error.record.flow_name, "text.validate")
        self.assertEqual(error.record.status, FlowStatus.FAILED)

    def test_flows_can_be_composed_with_child_run_identity(self) -> None:
        child_records = []

        tokenize = Flow(
            name="text.tokenize",
            execute=lambda value, context: value.split(),
        )

        def summarize(value: str, context: FlowContext) -> int:
            child = run_flow(
                tokenize,
                value,
                context=context.child(metadata={"stage": "tokenize"}),
            )
            child_records.append(child.record)
            return len(child.output)

        summarize_flow = Flow(name="text.summarize", execute=summarize)
        parent = run_flow(
            summarize_flow,
            "one two three",
            context=FlowContext(run_id="parent-001"),
        )

        self.assertEqual(parent.output, 3)
        self.assertEqual(len(child_records), 1)
        self.assertNotEqual(child_records[0].run_id, parent.record.run_id)
        self.assertEqual(child_records[0].parent_run_id, parent.record.run_id)

    def test_context_metadata_is_copied_and_read_only(self) -> None:
        original = {"source": "cli"}
        context = FlowContext(metadata=original)
        original["source"] = "mutated"

        self.assertEqual(context.metadata["source"], "cli")
        with self.assertRaises(TypeError):
            context.metadata["source"] = "changed"  # type: ignore[index]

    def test_child_context_inherits_and_overrides_metadata(self) -> None:
        parent = FlowContext(run_id="parent", metadata={"source": "agent", "scope": "all"})
        child = parent.child(metadata={"scope": "selected"})

        self.assertEqual(child.parent_run_id, "parent")
        self.assertEqual(child.metadata["source"], "agent")
        self.assertEqual(child.metadata["scope"], "selected")
        self.assertNotEqual(child.run_id, parent.run_id)

    def test_contract_validation(self) -> None:
        with self.assertRaises(ValueError):
            Flow(name=" ", execute=lambda value, context: value)
        with self.assertRaises(ValueError):
            Flow(name="valid", version=" ", execute=lambda value, context: value)
        with self.assertRaises(TypeError):
            Flow(name="invalid.execute", execute=None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            FlowContext(attempt=0)
        with self.assertRaises(ValueError):
            FlowContext(attempt=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            FlowContext(run_id=123)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            Flow(name=None, execute=lambda value, context: value)  # type: ignore[arg-type]

    def test_runtime_has_no_orchestrator_or_third_party_imports(self) -> None:
        imported_roots: set[str] = set()
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])

        allowed = set(sys.stdlib_module_names) | {"nooshare_flow"}
        self.assertEqual(imported_roots - allowed, set())
        self.assertNotIn("dagster", imported_roots)
        self.assertNotIn("prefect", imported_roots)


if __name__ == "__main__":
    unittest.main()
