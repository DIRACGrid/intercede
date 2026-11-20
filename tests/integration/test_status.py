"""Shared suite: StatusReporter, on every stack."""

from __future__ import annotations

from pathlib import Path

import pytest

from intercede.models import JobID, JobStatus, SubmissionSpec
from intercede.protocols import JobBackend
from tests.integration.jobs import FINISHED, release, script, wait_for

pytestmark = pytest.mark.contract


async def test_a_job_that_succeeds_is_done(backend: JobBackend, tmp_path: Path):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "true"))
    )
    handles = list(submission.handles.values())
    try:
        assert await wait_for(backend, handles, FINISHED) == {
            handles[0]: JobStatus.DONE
        }
    finally:
        await release(backend, handles)


async def test_a_job_that_exits_non_zero_failed(backend: JobBackend, tmp_path: Path):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "exit 3"))
    )
    handles = list(submission.handles.values())
    try:
        assert await wait_for(backend, handles, FINISHED) == {
            handles[0]: JobStatus.FAILED
        }
    finally:
        await release(backend, handles)


async def test_an_unknown_job_is_unknown(backend: JobBackend, tmp_path: Path):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "true"))
    )
    handles = list(submission.handles.values())
    try:
        unknown = handles[0].model_copy(update={"id": JobID("999999.0")})
        statuses = await backend.get_status([handles[0], unknown])
        assert set(statuses) == {handles[0], unknown}
        assert statuses[unknown] == JobStatus.UNKNOWN
    finally:
        await release(backend, handles)
