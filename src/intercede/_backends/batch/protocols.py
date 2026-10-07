"""Internal collaborator protocols of the composed backend (IC-ADR-001 §4).

Shapes only: transports and schedulers are implemented in later issues.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict

from intercede.models import CopyVars, JobID, JobStatus, SubmissionSpec

Command = Sequence[str]  # an argv: only argv and staged files ever cross the transport
# One resource, so its own IDs are unique: no need for handles here.
SchedulerStatusMap = Mapping[JobID, JobStatus]


class CommandResult(BaseModel):
    """The outcome of running one command on the resource."""

    model_config = ConfigDict(frozen=True)

    returncode: int
    stdout: str
    stderr: str


@runtime_checkable
class Transport(Protocol):
    """How InterCEde reaches a resource: local shell, SSH, ...

    Transports hold connections, so they share the backend lifecycle: cheap
    construction with no I/O, and an idempotent close.
    """

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def run(self, argv: Command) -> CommandResult: ...

    async def put(self, local: Path, remote: str) -> None: ...

    async def get(self, remote: str, local: Path) -> None: ...


@runtime_checkable
class Scheduler(Protocol):
    """How work is queued: builds commands locally and parses their output."""

    stages_own_files: bool  # does the scheduler move the sandbox itself?

    def submit_command(
        self, spec: SubmissionSpec, copies: Sequence[CopyVars], workdir: str
    ) -> Command:
        """Build the command that queues every copy; ``workdir`` is where the sandbox was staged."""
        ...

    def parse_submit(self, raw: str) -> Sequence[JobID]:
        """Read the IDs from the submit command's output, one per copy, in copy order."""
        ...

    def status_command(self, ids: Sequence[JobID]) -> Command: ...

    def parse_status(self, raw: str, ids: Sequence[JobID]) -> SchedulerStatusMap:
        """Return a status for every id asked about; ids missing from ``raw`` are ``UNKNOWN``."""
        ...

    def kill_command(self, ids: Sequence[JobID]) -> Command: ...
