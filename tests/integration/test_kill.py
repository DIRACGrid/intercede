"""Shared suite: Killable, on every stack whose backend has it."""

from __future__ import annotations

from pathlib import Path

import pytest

from intercede.models import JobStatus, SubmissionSpec
from intercede.protocols import JobBackend, Killable
from tests.integration.jobs import release, script, wait_for

pytestmark = pytest.mark.contract


async def test_kill_stops_a_running_job(
    backend: JobBackend, killable: Killable, tmp_path: Path
):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "sleep 600"))
    )
    handles = list(submission.handles.values())
    try:
        await wait_for(backend, handles, frozenset({JobStatus.RUNNING}))

        outcomes = await killable.kill(handles)

        assert outcomes[handles[0]].ok, outcomes[handles[0]].reason
        await wait_for(backend, handles, frozenset({JobStatus.ABORTED}))
    finally:
        await release(backend, handles)
