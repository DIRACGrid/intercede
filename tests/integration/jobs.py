"""Helpers the shared suite uses to run jobs on a stack: payloads, waiting, cleanup."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from intercede.models import JobHandle, JobStatus
from intercede.protocols import JobBackend, Purgeable

# A job passes through the backend's own cycles (a CE's router, a scheduler's
# matchmaking) before it runs; a few minutes covers them on a slow CI runner.
FINISH_TIMEOUT_S = 300
POLL_INTERVAL_S = 2

FINISHED = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.ABORTED})
ACTIVE = frozenset({JobStatus.SUBMITTED, JobStatus.WAITING, JobStatus.RUNNING})


def script(tmp_path: Path, body: str) -> str:
    """Write a job script on this host and return its path.

    A local executable is shipped with the job by every backend, so the payload
    does not depend on what is installed where it runs.
    """
    path = tmp_path / "job.sh"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return str(path)


async def wait_for(
    backend: JobBackend, handles: Sequence[JobHandle], states: frozenset[JobStatus]
) -> dict[JobHandle, JobStatus]:
    """Poll until every job is in one of ``states``; fail after the timeout."""
    deadline = time.monotonic() + FINISH_TIMEOUT_S
    while True:
        statuses = dict(await backend.get_status(handles))
        if all(status in states for status in statuses.values()):
            return statuses
        if time.monotonic() > deadline:
            pytest.fail(f"jobs did not reach {sorted(states)} in time: {statuses}")
        await asyncio.sleep(POLL_INTERVAL_S)


async def release(backend: JobBackend, handles: Sequence[JobHandle]) -> None:
    """Leave nothing behind on the stack, where the backend can release jobs."""
    if isinstance(backend, Purgeable) and handles:
        await backend.purge(handles)
