"""The fake's test hooks, and the rules the conformance suite cannot reach without them.

``test_conformance.py`` already runs the contract against the fake; what is here
needs a job to finish, fail or be refused on demand.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from intercede import (
    ContainerSpec,
    CopyVars,
    FileRef,
    JobHandle,
    JobStatus,
    OutputMember,
    OutputSpec,
    ResourceUnavailableError,
    SpecificationRejectedError,
    SubmissionSpec,
)
from intercede._backends.fake import FakeBackend

SPEC = SubmissionSpec(executable="/bin/true")

Operation = Callable[[FakeBackend, list[JobHandle], Path], Awaitable[Any]]
PER_JOB: dict[str, Operation] = {
    "get_output": lambda b, h, d: b.get_output(h, d),
    "kill": lambda b, h, d: b.kill(h),
    "purge": lambda b, h, d: b.purge(h),
    "get_diagnostics": lambda b, h, d: b.get_diagnostics(h),
}


class TestHooks:
    async def test_refused_copies_keep_their_indexes(self):
        async with FakeBackend(refuse_copies={0, 2}) as backend:
            submission = await backend.submit(SPEC, 4)
        assert sorted(submission.handles) == [1, 3]
        assert sorted(submission.failures) == [0, 2]

    @pytest.mark.parametrize("operation", sorted(PER_JOB))
    async def test_an_injected_failure_is_per_job(self, operation: str, tmp_path: Path):
        async with FakeBackend(fail_jobs={"0"}) as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle)
            outcome = (await PER_JOB[operation](backend, [handle], tmp_path))[handle]
            assert (await backend.get_status([handle]))[handle] == JobStatus.DONE
        assert not outcome.ok and outcome.reason == "injected failure"

    async def test_an_unavailable_resource_fails_the_whole_operation(self):
        async with FakeBackend() as backend:
            backend.unavailable = True
            with pytest.raises(ResourceUnavailableError):
                await backend.submit(SPEC)

    @pytest.mark.parametrize(
        ("options", "spec"),
        [
            (
                {"resource_staging": False},
                SubmissionSpec(
                    executable="x",
                    inputs=[FileRef(name="in", source="https://example.org/in")],
                ),
            ),
            (
                {"resource_staging": False},
                SubmissionSpec(
                    executable="x",
                    outputs=OutputSpec(
                        members=[
                            OutputMember(pattern="kept"),
                            OutputMember(pattern="sent", destination="https://e.org/o"),
                        ]
                    ),
                ),
            ),
            (
                {"containers": False},
                SubmissionSpec(executable="x", container=ContainerSpec(image="a")),
            ),
        ],
        ids=["url-input", "url-output", "container"],
    )
    async def test_what_a_resource_cannot_do_is_refused_before_anything_runs(
        self, options: dict[str, bool], spec: SubmissionSpec
    ):
        async with FakeBackend(**options) as backend:
            with pytest.raises(SpecificationRejectedError):
                await backend.submit(spec, 3)
            assert backend._jobs == {}


class TestSubmit:
    async def test_a_copy_value_wins_over_the_spec(self):
        spec = SubmissionSpec(
            executable="x", environment={"SITE": "a", "STAMP": "spec"}
        )
        copies = [
            CopyVars(environment={"STAMP": str(i)}, secrets={"s": b"%d" % i})
            for i in range(2)
        ]
        async with FakeBackend() as backend:
            submission = await backend.submit(spec, copies)
            jobs = [backend._jobs[submission.handles[i].id] for i in range(2)]
        assert [job.environment for job in jobs] == [
            {"SITE": "a", "STAMP": "0"},
            {"SITE": "a", "STAMP": "1"},
        ]
        assert [job.secrets for job in jobs] == [{"s": b"0"}, {"s": b"1"}]

    async def test_job_ids_are_unique_across_submissions(self):
        async with FakeBackend() as backend:
            first = await backend.submit(SPEC, 2)
            second = await backend.submit(SPEC, 2)
        ids = {h.id for s in (first, second) for h in s.handles.values()}
        assert len(ids) == 4


class TestKill:
    async def test_aborts_an_active_job(self):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, status=JobStatus.RUNNING)
            assert (await backend.kill([handle]))[handle].ok
            assert (await backend.get_status([handle]))[handle] == JobStatus.ABORTED

    async def test_leaves_a_finished_job_alone(self):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, status=JobStatus.FAILED)
            outcome = (await backend.kill([handle]))[handle]
            assert outcome.ok and outcome.reason == "already finished"
            assert (await backend.get_status([handle]))[handle] == JobStatus.FAILED


class TestOutput:
    async def test_collected_again_and_again(self, tmp_path: Path):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, stdout="hello", files={"deep/out.txt": b"42"})
            for _ in range(2):
                outcome = (await backend.get_output([handle], tmp_path))[handle]
                assert outcome.ok and outcome.value is not None
                assert outcome.value.stdout is not None
                assert outcome.value.stdout.read_text() == "hello"
                assert outcome.value.files["deep/out.txt"].read_bytes() == b"42"

    async def test_refused_while_active(self, tmp_path: Path):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            outcome = (await backend.get_output([handle], tmp_path))[handle]
        assert not outcome.ok and outcome.reason == "job has not finished"

    @pytest.mark.parametrize("name", ["../../escaped", "/abs/escaped"])
    async def test_an_escaping_member_writes_nothing_at_all(
        self, tmp_path: Path, name: str
    ):
        dest = tmp_path / "dest"
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, stdout="x", files={"ok.txt": b"x", name: b"x"})
            outcome = (await backend.get_output([handle], dest))[handle]
        assert not outcome.ok
        assert not dest.exists()  # not even the safe members or stdout


async def test_a_purged_job_is_gone_for_every_operation(tmp_path: Path):
    async with FakeBackend() as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        backend.finish(handle)
        assert (await backend.purge([handle]))[handle].ok
        assert (await backend.get_status([handle]))[handle] == JobStatus.UNKNOWN
        for operation in PER_JOB.values():
            assert not (await operation(backend, [handle], tmp_path))[handle].ok


async def test_diagnostics_read_a_running_job_by_name():
    async with FakeBackend() as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        backend.finish(handle, status=JobStatus.RUNNING, stdout="progress")
        default = (await backend.get_diagnostics([handle]))[handle]
        named = (await backend.get_diagnostics([handle], filename="job.log"))[handle]
    assert default.value is not None and default.value.filename == "stdout"
    assert default.value.content == "progress"
    assert named.value is not None and named.value.filename == "job.log"
