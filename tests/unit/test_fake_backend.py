"""Behaviour of the fake that needs its test hooks: completion, partial failure, refusal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from intercede import (
    ContainerSpec,
    CopyVars,
    FileRef,
    InterCEdeError,
    JobHandle,
    JobStatus,
    OutputMember,
    OutputSpec,
    ResourceUnavailableError,
    SpecificationRejectedError,
    SubmissionSpec,
)
from intercede._backends.config import FakeSettings
from intercede._backends.fake import FakeBackend

SPEC = SubmissionSpec(executable="/bin/true")


async def test_partial_submission_keeps_copy_indexes():
    async with FakeBackend(refuse_copies={0, 2}) as backend:
        copies = [CopyVars(environment={"STAMP": str(i)}) for i in range(4)]
        sub = await backend.submit(SPEC, copies)
    assert sorted(sub.handles) == [1, 3]
    assert sorted(sub.failures) == [0, 2]


async def test_injected_failure_is_reported_per_job():
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
        handle = (await backend.submit(SPEC)).handles[0]
        backend.finish(handle, stdout="hello", files={"result/out.txt": b"42"})
        for _ in range(2):
            outcome = (await backend.get_output([handle], tmp_path))[handle]
            assert outcome.ok and outcome.value is not None
            assert outcome.value.stdout is not None
            assert outcome.value.stdout.read_text() == "hello"
            assert outcome.value.files["result/out.txt"].read_bytes() == b"42"
            assert outcome.value.files["result/out.txt"].is_relative_to(
                tmp_path.resolve()
            )


async def test_output_of_a_running_job_fails_per_job(tmp_path: Path):
    async with FakeBackend() as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        assert not (await backend.get_output([handle], tmp_path))[handle].ok


@pytest.mark.parametrize("name", ["../../escaped", "/abs/escaped"])
async def test_output_members_cannot_escape_dest(tmp_path: Path, name: str):
    dest = tmp_path / "dest"
    async with FakeBackend() as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        backend.finish(handle, files={name: b"x"})
        outcome = (await backend.get_output([handle], dest))[handle]
    assert not outcome.ok
    assert not any(p.name == "escaped" for p in tmp_path.rglob("*"))


async def test_kill_stops_active_jobs():
    async with FakeBackend() as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        await backend.kill([handle])
        assert (await backend.get_status([handle]))[handle] == JobStatus.ABORTED


async def test_diagnostics_are_repeatable():
    async with FakeBackend() as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        backend.finish(handle, status=JobStatus.RUNNING, stdout="progress")
        for _ in range(2):
            outcome = (await backend.get_diagnostics([handle]))[handle]
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


# --------------------------------------------------------------------------
# Every operation against every state a job can be in
# --------------------------------------------------------------------------

Operation = Callable[[FakeBackend, list[JobHandle], Path], Awaitable[Any]]

OPERATIONS: dict[str, Operation] = {
    "submit": lambda b, h, d: b.submit(SPEC),
    "get_status": lambda b, h, d: b.get_status(h),
    "get_output": lambda b, h, d: b.get_output(h, d),
    "kill": lambda b, h, d: b.kill(h),
    "purge": lambda b, h, d: b.purge(h),
    "get_diagnostics": lambda b, h, d: b.get_diagnostics(h),
}
PER_JOB = ["get_output", "kill", "purge", "get_diagnostics"]


def foreign(handle: JobHandle, **change: str) -> JobHandle:
    return handle.model_copy(update=change)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_every_operation_refuses_when_closed(operation: str, tmp_path: Path):
    backend = FakeBackend()
    async with backend:
        handles = list((await backend.submit(SPEC)).handles.values())
    with pytest.raises(InterCEdeError, match="closed"):
        await OPERATIONS[operation](backend, handles, tmp_path)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
async def test_every_operation_raises_when_unavailable(operation: str, tmp_path: Path):
    async with FakeBackend() as backend:
        handles = list((await backend.submit(SPEC)).handles.values())
        backend.unavailable = True
        with pytest.raises(ResourceUnavailableError):
            await OPERATIONS[operation](backend, handles, tmp_path)


@pytest.mark.parametrize("operation", PER_JOB)
@pytest.mark.parametrize(
    ("make_handle", "reason"),
    [
        (lambda h: foreign(h, id="999"), "unknown job"),
        (lambda h: foreign(h, resource="fake://other"), "unknown job"),
        (lambda h: foreign(h, backend="arc"), "unknown job"),
    ],
    ids=["unknown-id", "other-resource", "other-backend"],
)
async def test_every_per_job_operation_fails_for_jobs_it_does_not_hold(
    operation: str,
    make_handle: Callable[[JobHandle], JobHandle],
    reason: str,
    tmp_path: Path,
):
    async with FakeBackend() as backend:
        handle = make_handle((await backend.submit(SPEC)).handles[0])
        outcome = (await OPERATIONS[operation](backend, [handle], tmp_path))[handle]
    assert not outcome.ok
    assert outcome.reason == reason


@pytest.mark.parametrize("operation", PER_JOB)
async def test_every_per_job_operation_reports_injected_failures(
    operation: str, tmp_path: Path
):
    async with FakeBackend(fail_jobs={"0"}) as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        backend.finish(handle)
        outcome = (await OPERATIONS[operation](backend, [handle], tmp_path))[handle]
    assert not outcome.ok
    assert outcome.reason == "injected failure"


@pytest.mark.parametrize("operation", PER_JOB)
async def test_every_per_job_operation_answers_for_an_empty_batch(
    operation: str, tmp_path: Path
):
    async with FakeBackend() as backend:
        assert await OPERATIONS[operation](backend, [], tmp_path) == {}


async def test_status_of_an_empty_batch():
    async with FakeBackend() as backend:
        assert await backend.get_status([]) == {}


async def test_injected_failure_does_not_hide_status():
    async with FakeBackend(fail_jobs={"0"}) as backend:
        handle = (await backend.submit(SPEC)).handles[0]
        assert (await backend.get_status([handle]))[handle] == JobStatus.SUBMITTED


class TestLifecycle:
    async def test_reopening_after_close(self):
        backend = FakeBackend()
        async with backend:
            pass
        async with backend:
            assert (await backend.submit(SPEC)).handles

    async def test_from_settings(self):
        backend = FakeBackend.from_settings(FakeSettings(resource="fake://elsewhere"))
        async with backend:
            handle = (await backend.submit(SPEC)).handles[0]
        assert handle.resource == "fake://elsewhere"
        assert handle.backend == "fake"


class TestSubmit:
    async def test_count(self):
        async with FakeBackend() as backend:
            submission = await backend.submit(SPEC, 3)
        assert sorted(submission.handles) == [0, 1, 2]

    async def test_zero_copies(self):
        async with FakeBackend() as backend:
            submission = await backend.submit(SPEC, 0)
        assert submission.handles == {}
        assert submission.failures == {}

    async def test_every_copy_refused(self):
        async with FakeBackend(refuse_copies={0, 1}) as backend:
            submission = await backend.submit(SPEC, 2)
        assert submission.handles == {}
        assert sorted(submission.failures) == [0, 1]

    async def test_job_ids_are_unique_across_submissions(self):
        async with FakeBackend() as backend:
            first = await backend.submit(SPEC, 2)
            second = await backend.submit(SPEC, 2)
        ids = [h.id for s in (first, second) for h in s.handles.values()]
        assert len(set(ids)) == 4

    async def test_local_inputs_are_read_at_submit(self, tmp_path: Path):
        source = tmp_path / "input.txt"
        source.write_bytes(b"before")
        spec = SubmissionSpec(
            executable="x",
            inputs=[
                FileRef(name="in", source=str(source)),
                FileRef(name="remote", source="https://example.org/data"),
            ],
        )
        async with FakeBackend() as backend:
            handle = (await backend.submit(spec)).handles[0]
            source.write_bytes(b"after")  # the caller changes its file afterwards
            stored = backend._jobs[handle.id].inputs
        assert stored == {"in": b"before"}  # read once, URL left to the resource

    async def test_missing_local_input_fails_the_submission(self, tmp_path: Path):
        spec = SubmissionSpec(
            executable="x",
            inputs=[FileRef(name="in", source=str(tmp_path / "missing"))],
        )
        async with FakeBackend() as backend:
            with pytest.raises(FileNotFoundError):
                await backend.submit(spec)

    async def test_each_copy_keeps_its_own_secret_and_environment(self):
        spec = SubmissionSpec(
            executable="x", environment={"SITE": "a", "STAMP": "spec"}
        )
        copies = [
            CopyVars(
                environment={"STAMP": str(i)}, secrets={"pilot.secret": b"s%d" % i}
            )
            for i in range(2)
        ]
        async with FakeBackend() as backend:
            submission = await backend.submit(spec, copies)
            jobs = [backend._jobs[submission.handles[i].id] for i in range(2)]
        assert [job.secrets for job in jobs] == [
            {"pilot.secret": b"s0"},
            {"pilot.secret": b"s1"},
        ]
        assert [job.environment for job in jobs] == [
            {"SITE": "a", "STAMP": "0"},  # the copy's value wins over the spec's
            {"SITE": "a", "STAMP": "1"},
        ]


class TestRefusal:
    @pytest.mark.parametrize(
        "spec",
        [
            SubmissionSpec(executable="x"),  # no outputs at all: nothing to upload
            SubmissionSpec(executable="x", outputs=OutputSpec()),
            SubmissionSpec(
                executable="x",
                outputs=OutputSpec(members=[OutputMember(pattern="kept")]),
            ),
            SubmissionSpec(
                executable="x", inputs=[FileRef(name="in", source="/dev/null")]
            ),
        ],
        ids=["no-outputs", "empty-outputs", "kept-output", "local-input"],
    )
    async def test_spec_without_resource_staging_is_accepted(
        self, spec: SubmissionSpec
    ):
        async with FakeBackend(resource_staging=False) as backend:
            assert (await backend.submit(spec)).handles

    async def test_one_url_output_among_kept_ones_is_refused(self):
        spec = SubmissionSpec(
            executable="x",
            outputs=OutputSpec(
                members=[
                    OutputMember(pattern="kept"),
                    OutputMember(pattern="sent", destination="https://e.org/o"),
                ]
            ),
        )
        async with FakeBackend(resource_staging=False) as backend:
            with pytest.raises(SpecificationRejectedError, match="upload"):
                await backend.submit(spec)

    async def test_refusal_submits_nothing(self):
        spec = SubmissionSpec(
            executable="x", container=ContainerSpec(image="docker://a")
        )
        async with FakeBackend(containers=False) as backend:
            with pytest.raises(SpecificationRejectedError):
                await backend.submit(spec, 3)
            assert backend._jobs == {}

    async def test_container_is_accepted_when_supported(self):
        spec = SubmissionSpec(
            executable="x", container=ContainerSpec(image="docker://a")
        )
        async with FakeBackend() as backend:
            assert (await backend.submit(spec)).handles

    async def test_resource_staging_is_accepted_when_supported(self):
        spec = SubmissionSpec(
            executable="x",
            inputs=[FileRef(name="in", source="https://example.org/in")],
            outputs=OutputSpec(
                members=[OutputMember(pattern="out", destination="https://e.org/o")]
            ),
        )
        async with FakeBackend() as backend:
            assert (await backend.submit(spec)).handles


class TestKill:
    @pytest.mark.parametrize(
        "status", [JobStatus.SUBMITTED, JobStatus.WAITING, JobStatus.RUNNING]
    )
    async def test_aborts_every_active_state(self, status: JobStatus):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, status=status)
            outcome = (await backend.kill([handle]))[handle]
            assert outcome.ok and outcome.reason is None
            assert (await backend.get_status([handle]))[handle] == JobStatus.ABORTED

    @pytest.mark.parametrize(
        "status", [JobStatus.DONE, JobStatus.FAILED, JobStatus.ABORTED]
    )
    async def test_leaves_a_finished_job_alone(self, status: JobStatus):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, status=status)
            outcome = (await backend.kill([handle]))[handle]
            assert outcome.ok
            assert outcome.reason == "already finished"
            assert (await backend.get_status([handle]))[handle] == status

    async def test_killing_twice(self):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            await backend.kill([handle])
            assert (await backend.kill([handle]))[handle].reason == "already finished"


class TestPurge:
    async def test_purged_job_is_gone_for_every_operation(self, tmp_path: Path):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle)
            assert (await backend.purge([handle]))[handle].ok
            assert (await backend.get_status([handle]))[handle] == JobStatus.UNKNOWN
            for operation in PER_JOB:
                outcome = (await OPERATIONS[operation](backend, [handle], tmp_path))[
                    handle
                ]
                assert not outcome.ok

    async def test_purge_keeps_other_jobs(self):
        async with FakeBackend() as backend:
            submission = await backend.submit(SPEC, 2)
            await backend.purge([submission.handles[0]])
            status = await backend.get_status([submission.handles[1]])
        assert status[submission.handles[1]] == JobStatus.SUBMITTED


class TestOutput:
    @pytest.mark.parametrize(
        "status", [JobStatus.SUBMITTED, JobStatus.WAITING, JobStatus.RUNNING]
    )
    async def test_refused_while_active(self, status: JobStatus, tmp_path: Path):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, status=status)
            outcome = (await backend.get_output([handle], tmp_path))[handle]
        assert not outcome.ok
        assert outcome.reason == "job has not finished"

    @pytest.mark.parametrize(
        "status", [JobStatus.DONE, JobStatus.FAILED, JobStatus.ABORTED]
    )
    async def test_collected_once_finished(self, status: JobStatus, tmp_path: Path):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, status=status, stdout="log")
            outcome = (await backend.get_output([handle], tmp_path))[handle]
        assert outcome.ok and outcome.value is not None
        assert outcome.value.stdout is not None
        assert outcome.value.stdout.read_text() == "log"

    async def test_manifest_lists_every_member(self, tmp_path: Path):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(handle, files={"a.txt": b"a", "deep/er/b.bin": b"\x00\x01"})
            outcome = (await backend.get_output([handle], tmp_path))[handle]
        assert outcome.value is not None
        assert set(outcome.value.files) == {"a.txt", "deep/er/b.bin"}
        assert outcome.value.files["deep/er/b.bin"].read_bytes() == b"\x00\x01"
        assert (
            outcome.value.stderr is not None and outcome.value.stderr.read_text() == ""
        )
        assert outcome.value.log is None

    async def test_jobs_land_in_separate_directories(self, tmp_path: Path):
        async with FakeBackend() as backend:
            submission = await backend.submit(SPEC, 2)
            handles = list(submission.handles.values())
            for handle in handles:
                backend.finish(handle, stdout=handle.id)
            outcomes = await backend.get_output(handles, tmp_path)
        paths = [outcomes[h].value.stdout for h in handles]  # type: ignore[union-attr]
        assert paths[0] != paths[1]
        assert [p.read_text() for p in paths] == [h.id for h in handles]  # type: ignore[union-attr]

    async def test_mixed_batch_reports_each_job(self, tmp_path: Path):
        async with FakeBackend() as backend:
            submission = await backend.submit(SPEC, 2)
            done, running = submission.handles[0], submission.handles[1]
            backend.finish(done)
            outcomes = await backend.get_output([done, running], tmp_path)
        assert outcomes[done].ok
        assert not outcomes[running].ok

    async def test_escape_writes_nothing_at_all(self, tmp_path: Path):
        dest = tmp_path / "dest"
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            backend.finish(
                handle, stdout="x", files={"ok.txt": b"x", "../../bad": b"x"}
            )
            outcome = (await backend.get_output([handle], dest))[handle]
        assert not outcome.ok
        assert "../../bad" in (outcome.reason or "")
        assert not dest.exists()  # not even the safe members or stdout


class TestDiagnostics:
    async def test_defaults_to_stdout(self):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            outcome = (await backend.get_diagnostics([handle]))[handle]
        assert outcome.value is not None and outcome.value.name == "stdout"

    async def test_uses_the_requested_name(self):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            outcome = (await backend.get_diagnostics([handle], name="job.log"))[handle]
        assert outcome.value is not None and outcome.value.name == "job.log"

    async def test_does_not_change_the_job(self):
        async with FakeBackend() as backend:
            handle = (await backend.submit(SPEC)).handles[0]
            await backend.get_diagnostics([handle])
            assert (await backend.get_status([handle]))[handle] == JobStatus.SUBMITTED
