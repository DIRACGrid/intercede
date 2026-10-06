"""Unit conformance suite: what every backend must do, written against the contract only.

Tests here use nothing but the public API, so the same suite runs against real
backends in the integration stacks (IC-ADR-002). Behaviour that needs a test
hook (finishing a job, injecting failures) lives in ``test_fake_backend.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intercede import (
    CopyVars,
    FileRef,
    InterCEdeError,
    JobBackend,
    JobHandle,
    JobID,
    JobStatus,
    Killable,
    OutputRetriever,
    Purgeable,
    SubmissionSpec,
)

SPEC = SubmissionSpec(executable="/bin/true", tag="conformance")


def stranger(backend_handle: JobHandle) -> JobHandle:
    """Return a handle with the same job ID on another resource."""
    return backend_handle.model_copy(update={"resource": "elsewhere://"})


async def test_submit_returns_one_handle_per_copy(backend: JobBackend):
    sub = await backend.submit(SPEC, 3)
    assert sorted(sub.handles) == [0, 1, 2]
    assert not sub.failures
    assert len(set(sub.handles.values())) == 3


async def test_submit_with_copy_vars(backend: JobBackend):
    copies = [
        CopyVars(environment={"STAMP": str(i)}, secrets={"pilot.secret": b"s%d" % i})
        for i in range(2)
    ]
    sub = await backend.submit(SPEC, copies)
    assert sorted(sub.handles) == [0, 1]


async def test_handles_survive_serialisation(backend: JobBackend):
    sub = await backend.submit(SPEC)
    stored = JobHandle.model_validate_json(sub.handles[0].model_dump_json())
    status = await backend.get_status([stored])
    assert status[stored] != JobStatus.UNKNOWN


async def test_submit_does_not_retain_caller_paths(backend: JobBackend, tmp_path: Path):
    source = tmp_path / "input.txt"
    source.write_text("data")
    sub = await backend.submit(
        SubmissionSpec(
            executable="/bin/true", inputs=[FileRef(name="in", source=str(source))]
        )
    )
    source.unlink()  # the caller may delete its files as soon as submit returns
    status = await backend.get_status(list(sub.handles.values()))
    assert all(status != JobStatus.UNKNOWN for status in status.values())


async def test_status_answers_every_handle(backend: JobBackend):
    sub = await backend.submit(SPEC, 2)
    handles = list(sub.handles.values())
    unknown = handles[0].model_copy(update={"id": JobID("does-not-exist")})
    status = await backend.get_status([*handles, unknown])
    assert set(status) == {*handles, unknown}
    assert status[unknown] == JobStatus.UNKNOWN


async def test_status_does_not_confuse_jobs_across_resources(backend: JobBackend):
    sub = await backend.submit(SPEC)
    ours = sub.handles[0]
    theirs = stranger(ours)
    status = await backend.get_status([ours, theirs])
    assert status[ours] != JobStatus.UNKNOWN
    assert status[theirs] == JobStatus.UNKNOWN


async def test_kill_reports_per_job(backend: JobBackend):
    if not isinstance(backend, Killable):
        pytest.skip("backend cannot kill")
    sub = await backend.submit(SPEC)
    ours = sub.handles[0]
    theirs = stranger(ours)
    outcome = await backend.kill([ours, theirs])
    assert outcome[ours].ok
    assert not outcome[theirs].ok
    assert outcome[theirs].reason


async def test_purge_forgets_the_job(backend: JobBackend):
    if not isinstance(backend, Purgeable):
        pytest.skip("backend cannot purge")
    sub = await backend.submit(SPEC)
    handle = sub.handles[0]
    assert (await backend.purge([handle]))[handle].ok
    assert (await backend.get_status([handle]))[handle] == JobStatus.UNKNOWN


async def test_output_of_an_unknown_job_fails_per_job(
    backend: JobBackend, tmp_path: Path
):
    if not isinstance(backend, OutputRetriever):
        pytest.skip("backend cannot retrieve output")
    sub = await backend.submit(SPEC)
    theirs = stranger(sub.handles[0])
    outcome = await backend.get_output([theirs], tmp_path)
    assert not outcome[theirs].ok


async def test_close_is_idempotent(backend: JobBackend):
    await backend.__aexit__(None, None, None)
    await backend.__aexit__(None, None, None)
    with pytest.raises(InterCEdeError):
        await backend.submit(SPEC)
