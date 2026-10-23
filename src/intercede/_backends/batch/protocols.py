"""Internal collaborator protocols of the composed backend.

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

    async def run(self, argv: Command) -> CommandResult:
        """Run one command on the resource and wait for it to finish.

        Args:
            argv: The program and its arguments. Never passed through a shell.

        Returns:
            The exit code and everything the command printed. A non-zero exit
            code is returned, not raised: the scheduler decides what it means.

        Raises:
            ResourceUnavailableError: The resource could not be reached.
            AuthenticationError: The resource rejected the credentials.

        """
        ...

    async def put(self, local: Path, remote: str) -> None:
        """Copy a file or directory from this host to the resource.

        Args:
            local: The file or directory on this host.
            remote: Its destination path on the resource.

        Raises:
            ResourceUnavailableError: The copy failed.

        """
        ...

    async def get(self, remote: str, local: Path) -> None:
        """Copy a file or directory from the resource to this host.

        Args:
            remote: The file or directory on the resource.
            local: Its destination path on this host.

        Raises:
            ResourceUnavailableError: The copy failed.

        """
        ...


@runtime_checkable
class Scheduler(Protocol):
    """How work is queued: builds commands locally and parses their output.

    A scheduler never runs anything itself; the backend runs its commands
    through the transport. Its methods are pure, so they are tested without a
    resource.

    Attributes:
        stages_own_files: Whether the scheduler moves the job's files itself.
            When False, the backend copies them with the transport first.

    """

    stages_own_files: bool

    def submit_command(
        self, spec: SubmissionSpec, copies: Sequence[CopyVars], workdir: str
    ) -> Command:
        """Build the command that queues every copy of ``spec``.

        Args:
            spec: What every copy shares.
            copies: One entry per copy, in copy order.
            workdir: The directory on the resource where the files were staged.

        Returns:
            The command to run through the transport.

        """
        ...

    def parse_submit(self, raw: str) -> Sequence[JobID]:
        """Read the job IDs from the output of the submit command.

        Args:
            raw: What the submit command printed.

        Returns:
            One ID per copy, in copy order.

        Raises:
            ValueError: ``raw`` does not hold one ID per copy.

        """
        ...

    def status_command(self, ids: Sequence[JobID]) -> Command:
        """Build the command that reports the state of the given jobs.

        Args:
            ids: The jobs to ask about.

        Returns:
            The command to run through the transport.

        """
        ...

    def parse_status(self, raw: str, ids: Sequence[JobID]) -> SchedulerStatusMap:
        """Read the jobs' states from the output of the status command.

        Args:
            raw: What the status command printed.
            ids: The jobs that were asked about.

        Returns:
            A status for every ID in ``ids``; an ID missing from ``raw`` is
            ``UNKNOWN``.

        """
        ...

    def kill_command(self, ids: Sequence[JobID]) -> Command:
        """Build the command that cancels the given jobs.

        Args:
            ids: The jobs to cancel.

        Returns:
            The command to run through the transport.

        """
        ...
