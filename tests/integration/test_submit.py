"""Shared suite: Submitter, on every stack."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from intercede.models import JobHandle, JobStatus, SubmissionSpec
from intercede.protocols import JobBackend
from tests.integration.jobs import release, script

pytestmark = pytest.mark.contract


async def test_submit_returns_one_handle_per_copy(
    backend: JobBackend, client_config: dict[str, Any], tmp_path: Path
):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "true")), copies=2
    )
    handles = list(submission.handles.values())
    try:
        assert submission.failures == {}
        assert sorted(submission.handles) == [0, 1]
        assert len(set(handles)) == 2
        for handle in handles:
            assert handle.backend == client_config["backend"]["kind"]
            assert handle.resource == backend.resource
    finally:
        await release(backend, handles)


async def test_a_stored_handle_finds_the_job(backend: JobBackend, tmp_path: Path):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "true"))
    )
    handles = list(submission.handles.values())
    try:
        stored = JobHandle.model_validate_json(handles[0].model_dump_json())
        statuses = await backend.get_status([stored])
        assert statuses[stored] != JobStatus.UNKNOWN
    finally:
        await release(backend, handles)
