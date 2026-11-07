"""The minimal Flow execution runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar

from .models import (
    Flow,
    FlowContext,
    FlowExecution,
    FlowExecutionError,
    FlowRunRecord,
    FlowStatus,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def run_flow(
    flow: Flow[InputT, OutputT],
    input: InputT,
    *,
    context: FlowContext | None = None,
) -> FlowExecution[OutputT]:
    """Execute one Flow and return its output plus a portable run record.

    Failures are re-raised as FlowExecutionError. The original exception is
    preserved as the cause, and the error carries the failed run record.
    Retry, scheduling, concurrency, and persistence policies belong to the
    calling runtime or orchestrator.
    """

    if not isinstance(flow, Flow):
        raise TypeError("flow must be a Flow instance")

    invocation = context if context is not None else FlowContext()
    started_at = datetime.now(UTC)

    try:
        output = flow.execute(input, invocation)
    except Exception as exc:
        finished_at = datetime.now(UTC)
        record = FlowRunRecord(
            flow_name=flow.name,
            flow_version=flow.version,
            run_id=invocation.run_id,
            attempt=invocation.attempt,
            parent_run_id=invocation.parent_run_id,
            status=FlowStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
        )
        raise FlowExecutionError(record) from exc

    finished_at = datetime.now(UTC)
    record = FlowRunRecord(
        flow_name=flow.name,
        flow_version=flow.version,
        run_id=invocation.run_id,
        attempt=invocation.attempt,
        parent_run_id=invocation.parent_run_id,
        status=FlowStatus.SUCCEEDED,
        started_at=started_at,
        finished_at=finished_at,
    )
    return FlowExecution(output=output, record=record)
