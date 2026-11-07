"""Framework-independent Flow contracts and execution records."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Generic, TypeVar
from uuid import uuid4

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class FlowContext:
    """Execution identity and metadata supplied to one Flow invocation.

    Infrastructure clients and business configuration belong in the Flow input
    or in the callable's explicitly constructed dependencies. The context only
    carries execution-scoped information.
    """

    run_id: str = field(default_factory=lambda: uuid4().hex)
    attempt: int = 1
    parent_run_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if self.parent_run_id is not None and not self.parent_run_id.strip():
            raise ValueError("parent_run_id must be None or a non-empty string")

        copied_metadata = dict(self.metadata)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in copied_metadata.items()
        ):
            raise TypeError("FlowContext metadata keys and values must be strings")
        object.__setattr__(self, "metadata", MappingProxyType(copied_metadata))

    def child(self, *, metadata: Mapping[str, str] | None = None) -> FlowContext:
        """Create a new invocation context linked to this invocation."""

        child_metadata = dict(self.metadata)
        if metadata is not None:
            child_metadata.update(metadata)
        return FlowContext(parent_run_id=self.run_id, metadata=child_metadata)


@dataclass(frozen=True, slots=True)
class Flow(Generic[InputT, OutputT]):
    """A named, directly callable unit of business capability.

    A Flow contains business execution only. It does not declare schedules,
    retries, concurrency, persistence policy, or an orchestrator-specific graph.
    """

    name: str
    execute: Callable[[InputT, FlowContext], OutputT]
    version: str = "1"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Flow name must not be empty")
        if not self.version.strip():
            raise ValueError("Flow version must not be empty")
        if not callable(self.execute):
            raise TypeError("Flow execute must be callable")


class FlowStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FlowRunRecord:
    """Observable facts about one invocation, independent of its output."""

    flow_name: str
    flow_version: str
    run_id: str
    attempt: int
    parent_run_id: str | None
    status: FlowStatus
    started_at: datetime
    finished_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class FlowExecution(Generic[OutputT]):
    """A successful Flow output together with its execution record."""

    output: OutputT
    record: FlowRunRecord


class FlowExecutionError(RuntimeError):
    """Raised when a Flow fails, retaining its failed execution record."""

    def __init__(self, record: FlowRunRecord) -> None:
        self.record = record
        super().__init__(
            f"Flow {record.flow_name!r} failed "
            f"(run_id={record.run_id}, attempt={record.attempt})."
        )
