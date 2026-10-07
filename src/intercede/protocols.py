"""The capability protocols of the InterCEde contract (IC-ADR-001 §3).

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

        Pass a count for identical copies, or one ``CopyVars`` per copy when each
        copy needs its own identity or secret. ``Submission.handles[i]`` belongs
        to copy ``i``; refused copies are reported in ``Submission.failures``.

        A spec the backend cannot honour raises ``SpecificationRejectedError``
        and submits nothing. Local input files are read before this returns:
        the backend never depends on caller-owned paths afterwards.
        """
        ...


@runtime_checkable
class StatusReporter(Protocol):
    """Reports the status of jobs."""

    async def get_status(self, handles: Sequence[JobHandle]) -> StatusMap:
        """Return a status per handle. Jobs the backend does not know are ``UNKNOWN``, never dropped."""
        ...


@runtime_checkable
class JobBackend(Submitter, StatusReporter, Protocol):
    """A complete backend: what the registry returns.

    Backends are asynchronous context managers (IC-ADR-001 §3): construction
    does no I/O, and closing is idempotent.
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
# --------------------------------------------------------------------------


@runtime_checkable
class OutputRetriever(Protocol):
    """Collects the outputs a job left at the resource."""

    async def get_output(
        self, handles: Sequence[JobHandle], dest: Path
    ) -> Mapping[JobHandle, OpOutcome[JobOutput]]:
        """Write each job's kept outputs into ``dest`` and return a manifest per job.

        ``dest`` belongs to the consumer: InterCEde writes into it and never
        deletes it. Every member lands under ``dest``. Collection is repeatable.
        """
        ...


@runtime_checkable
class Killable(Protocol):
    """Cancels jobs."""

    async def kill(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        """Cancel the jobs; the outcome is reported per job."""
        ...


@runtime_checkable
class Purgeable(Protocol):
    """Releases a job's remaining state at the resource."""

    async def purge(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        """Release outputs and remote state without collecting them."""
        ...


@runtime_checkable
class Diagnosable(Protocol):
    """Reads files from jobs before they complete."""

    async def get_diagnostics(
        self, handles: Sequence[JobHandle], name: str | None = None
    ) -> Mapping[JobHandle, OpOutcome[Diagnostics]]:
        """Read a file from each job; repeatable and non-destructive."""
        ...
