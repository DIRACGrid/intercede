"""The capability protocols of the InterCEde contract.

A backend qualifies by having the right methods; it never inherits from these
classes. Every protocol is runtime-checkable, so consumers can narrow on a
capability with ``isinstance``. Every operation is asynchronous, works on many
jobs at once, and returns one entry per handle it was given.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from intercede.models import (
    CopyVars,
    Diagnostics,
    JobHandle,
    JobOutput,
    OpOutcome,
    StatusMap,
    Submission,
    SubmissionSpec,
)

__all__ = [
    "Diagnosable",
    "JobBackend",
    "Killable",
    "OutputRetriever",
    "Purgeable",
    "StatusReporter",
    "Submitter",
]


# --------------------------------------------------------------------------
# Essential capabilities: every usable backend has these.
# --------------------------------------------------------------------------


@runtime_checkable
class Submitter(Protocol):
    """Submits one specification as one or more copies."""

    async def submit(
        self, spec: SubmissionSpec, copies: int | Sequence[CopyVars] = 1
    ) -> Submission:
        """Submit ``spec`` once per copy.

        Local input files are read before this returns: the backend never
        depends on caller-owned paths afterwards.

        Args:
            spec: What every copy shares: executable, inputs, outputs, resources.
            copies: A count for identical copies, or one ``CopyVars`` per copy
                when each copy needs its own environment or secrets.

        Returns:
            The handles of the submitted copies and the reasons for refused
            ones, both keyed by copy index: ``handles[i]`` belongs to copy ``i``.

        Raises:
            SpecificationRejectedError: The backend cannot honour ``spec``, or a
                copy has a secret named like one of the spec's inputs. Nothing
                is submitted.
            ResourceUnavailableError: The resource could not be reached, or
                nothing could be submitted.
            AuthenticationError: The resource rejected the credentials.

        """
        ...


@runtime_checkable
class StatusReporter(Protocol):
    """Reports the status of jobs."""

    async def get_status(self, handles: Sequence[JobHandle]) -> StatusMap:
        """Return the current status of each job.

        Args:
            handles: The jobs to ask about, as returned by ``submit``.

        Returns:
            One status per handle. A job the backend does not know is
            ``UNKNOWN``, never left out.

        Raises:
            ResourceUnavailableError: The resource could not be reached.
            AuthenticationError: The resource rejected the credentials.

        """
        ...


@runtime_checkable
class JobBackend(Submitter, StatusReporter, Protocol):
    """A complete backend: what the registry returns.

    Backends are asynchronous context managers: construction does no I/O,
    connections are opened on entry, and closing is idempotent.
    """

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


# --------------------------------------------------------------------------
# Optional capabilities: a backend implements only those it really has.
# Each operation reports per job; a failure of the whole operation raises.
# --------------------------------------------------------------------------


@runtime_checkable
class OutputRetriever(Protocol):
    """Collects the outputs a job left at the resource."""

    async def get_output(
        self, handles: Sequence[JobHandle], dest: Path
    ) -> Mapping[JobHandle, OpOutcome[JobOutput]]:
        """Write each finished job's kept outputs into ``dest``.

        Collection is repeatable until the job is purged.

        Args:
            handles: The jobs to collect.
            dest: A directory owned by the consumer. InterCEde writes every file
                under it and never deletes it.

        Returns:
            Per job, a manifest of the files written, or the reason the job's
            output could not be collected (for example, it has not finished).

        """
        ...


@runtime_checkable
class Killable(Protocol):
    """Cancels jobs."""

    async def kill(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        """Cancel the jobs that are still active.

        Args:
            handles: The jobs to cancel.

        Returns:
            Per job, whether it was cancelled. A job that already finished is
            left alone and reported as a success with a reason.

        """
        ...


@runtime_checkable
class Purgeable(Protocol):
    """Releases a job's remaining state at the resource."""

    async def purge(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        """Release the jobs' outputs and remote state without collecting them.

        Args:
            handles: The jobs to release.

        Returns:
            Per job, whether it was released. Afterwards its output can no
            longer be collected.

        """
        ...


@runtime_checkable
class Diagnosable(Protocol):
    """Reads files from jobs before they complete."""

    async def get_diagnostics(
        self, handles: Sequence[JobHandle], filename: str | None = None
    ) -> Mapping[JobHandle, OpOutcome[Diagnostics]]:
        """Read one file from each job while it runs; repeatable and non-destructive.

        Args:
            handles: The jobs to read from.
            filename: A file in the job's working directory, for example
                ``"job.log"``. ``None`` reads the job's standard output.

        Returns:
            Per job, the file's name and content, or the reason it could not be
            read.

        """
        ...
