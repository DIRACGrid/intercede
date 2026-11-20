"""Shared suite: Purgeable, on every stack whose backend has it."""

from __future__ import annotations

from pathlib import Path

import pytest

from intercede.models import JobStatus, SubmissionSpec
from intercede.protocols import JobBackend, OutputRetriever, Purgeable
from tests.integration.jobs import ACTIVE, script, wait_for

pytestmark = pytest.mark.contract


async def test_purge_releases_a_finished_job(
    backend: JobBackend, purgeable: Purgeable, tmp_path: Path
):
    submission = await backend.submit(
        SubmissionSpec(executable=script(tmp_path, "true"))
    )
    handles = list(submission.handles.values())
    await wait_for(backend, handles, frozenset({JobStatus.DONE}))

    outcomes = await purgeable.purge(handles)

    assert outcomes[handles[0]].ok, outcomes[handles[0]].reason
    statuses = await backend.get_status(handles)
    assert statuses[handles[0]] not in ACTIVE
    if isinstance(backend, OutputRetriever):
        output = await backend.get_output(handles, tmp_path / "out")
        assert not output[handles[0]].ok
