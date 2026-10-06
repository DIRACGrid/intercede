"""Behaviour of the fake that needs its test hooks: completion, partial failure, refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from intercede import (
    ContainerSpec,
    CopyVars,
    FileRef,
    JobStatus,
    OutputMember,
    OutputSpec,
    ResourceUnavailableError,
    SpecificationRejectedError,
    SubmissionSpec,
)
from intercede._backends.fake import FakeBackend

SPEC = SubmissionSpec(executable="/bin/true")


async def test_construction_does_no_io():
    FakeBackend(resource="unreachable://")  # nothing to connect to, and nothing raised


async def test_partial_submission_keeps_copy_indexes():
    async with FakeBackend(refuse_copies={0, 2}) as backend:
        copies = [CopyVars(environment={"STAMP": str(i)}) for i in range(4)]
        sub = await backend.submit(SPEC, copies)
    assert sorted(sub.handles) == [1, 3]
    assert sorted(sub.failures) == [0, 2]


async def test_injected_failure_is_reported_per_job(tmp_path: Path):
    async with FakeBackend(fail_jobs={"1"}) as backend:
        sub = await backend.submit(SPEC, 2)
        good, bad = sub.handles[0], sub.handles[1]
        killed = await backend.kill([good, bad])
        assert killed[good].ok
        assert not killed[bad].ok


async def test_whole_operation_failure_raises():
    async with FakeBackend() as backend:
        backend.unavailable = True
        with pytest.raises(ResourceUnavailableError):
            await backend.submit(SPEC)


async def test_output_is_repeatable_and_lands_under_dest(tmp_path: Path):
    async with FakeBackend() as backend:
        h = (await backend.submit(SPEC)).handles[0]
        backend.finish(h, stdout="hello", files={"result/out.txt": b"42"})
        for _ in range(2):
            outcome = (await backend.get_output([h], tmp_path))[h]
            assert outcome.ok and outcome.value is not None
            assert outcome.value.stdout is not None
            assert outcome.value.stdout.read_text() == "hello"
            assert outcome.value.files["result/out.txt"].read_bytes() == b"42"
            assert outcome.value.files["result/out.txt"].is_relative_to(
                tmp_path.resolve()
            )


async def test_output_of_a_running_job_fails_per_job(tmp_path: Path):
    async with FakeBackend() as backend:
        h = (await backend.submit(SPEC)).handles[0]
        assert not (await backend.get_output([h], tmp_path))[h].ok


@pytest.mark.parametrize("name", ["../../escaped", "/abs/escaped"])
async def test_output_members_cannot_escape_dest(tmp_path: Path, name: str):
    dest = tmp_path / "dest"
    async with FakeBackend() as backend:
        h = (await backend.submit(SPEC)).handles[0]
        backend.finish(h, files={name: b"x"})
        outcome = (await backend.get_output([h], dest))[h]
    assert not outcome.ok
    assert not any(p.name == "escaped" for p in tmp_path.rglob("*"))


async def test_kill_stops_active_jobs():
    async with FakeBackend() as backend:
        h = (await backend.submit(SPEC)).handles[0]
        await backend.kill([h])
        assert (await backend.get_status([h]))[h] == JobStatus.ABORTED


async def test_diagnostics_are_repeatable():
    async with FakeBackend() as backend:
        h = (await backend.submit(SPEC)).handles[0]
        backend.finish(h, status=JobStatus.RUNNING, stdout="progress")
        for _ in range(2):
            outcome = (await backend.get_diagnostics([h]))[h]
            assert outcome.value is not None
            assert outcome.value.content == "progress"


@pytest.mark.parametrize(
    "spec",
    [
        SubmissionSpec(
            executable="x", inputs=[FileRef(name="in", source="https://example.org/in")]
        ),
        SubmissionSpec(
            executable="x",
            outputs=OutputSpec(
                members=[OutputMember(pattern="out", destination="https://e.org/o")]
            ),
        ),
    ],
)
async def test_spec_needing_resource_staging_is_rejected_at_submit(
    spec: SubmissionSpec,
):
    async with FakeBackend(resource_staging=False) as backend:
        with pytest.raises(SpecificationRejectedError):
            await backend.submit(spec)


async def test_spec_with_container_is_rejected_when_unsupported():
    async with FakeBackend(containers=False) as backend:
        with pytest.raises(SpecificationRejectedError):
            await backend.submit(
                SubmissionSpec(
                    executable="x", container=ContainerSpec(image="docker://alpine")
                )
            )
