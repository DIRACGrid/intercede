"""Shared suite: Submitter, on every stack."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from intercede.exceptions import SpecificationRejectedError
from intercede.models import CopyVars, FileRef, JobHandle, JobStatus, SubmissionSpec
from intercede.protocols import JobBackend
from tests.integration.jobs import FINISHED, release, script, wait_for

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


async def test_local_files_may_be_deleted_once_submit_returns(
    backend: JobBackend, tmp_path: Path
):
    """A backend reads local files before submit returns, never later."""
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "data.txt").write_text("kept")
    spec = SubmissionSpec(
        executable=script(caller, '[ "$(cat data.txt)" = kept ]'),
        inputs=[FileRef(name="data.txt", source=str(caller / "data.txt"))],
    )
    submission = await backend.submit(spec)
    shutil.rmtree(caller)
    handles = list(submission.handles.values())
    try:
        statuses = await wait_for(backend, handles, FINISHED)
        assert statuses[handles[0]] == JobStatus.DONE
    finally:
        await release(backend, handles)


# What every backend delivers as written, whatever it passes them through (a
# shell, XML, a submit description).
AS_WRITTEN = [
    "two words",
    "it's",
    'say "hi"',
    "",
    "ünïcode",
    "back\\slash",
    "a&b;c|d",
    "<in>",
    "*",
]

# What a backend may refuse instead, when the way it starts a job would change
# it (ARC's job script expands "$" and backticks, and drops line breaks).
EXACT_OR_REFUSED = {
    "dollar-argument": {"arguments": ["$HOME"]},
    "backtick-argument": {"arguments": ["`id`"]},
    "line-break-argument": {"arguments": ["a\nb"]},
    "dollar-variable": {"environment": {"V0": "$HOME"}},
    "line-break-variable": {"environment": {"V0": "a\nb"}},
}


def arrival(
    tmp_path: Path, arguments: list[str], environment: dict[str, str] | None = None
) -> SubmissionSpec:
    """A job that succeeds only if its arguments and variables arrive as written.

    It checks them itself, so a backend that cannot collect output can run it.
    """
    environment = environment or {}
    names = sorted(environment)
    expected = tmp_path / "expected.txt"
    expected.write_text(
        "".join(
            f"[{value}]" for value in [*arguments, *(environment[n] for n in names)]
        )
    )
    received = " ".join(['"$@"', *(f'"${{{name}-}}"' for name in names)])
    return SubmissionSpec(
        executable=script(
            tmp_path, f"""[ "$(printf '[%s]' {received})" = "$(cat expected.txt)" ]"""
        ),
        arguments=arguments,
        environment=environment,
        inputs=[FileRef(name="expected.txt", source=str(expected))],
    )


async def finished(
    backend: JobBackend, spec: SubmissionSpec, copies: int | list[CopyVars] = 1
) -> list[JobStatus]:
    """Submit, wait for every copy to finish, and return their statuses in copy order."""
    submission = await backend.submit(spec, copies)
    handles = [submission.handles[i] for i in sorted(submission.handles)]
    try:
        assert submission.failures == {}
        statuses = await wait_for(backend, handles, FINISHED)
    finally:
        await release(backend, handles)
    return [statuses[handle] for handle in handles]


async def test_arguments_and_variables_arrive_as_written(
    backend: JobBackend, tmp_path: Path
):
    spec = arrival(
        tmp_path,
        arguments=AS_WRITTEN,
        environment={f"V{i}": value for i, value in enumerate(AS_WRITTEN)},
    )
    assert await finished(backend, spec) == [JobStatus.DONE]


@pytest.mark.parametrize(
    "fields", EXACT_OR_REFUSED.values(), ids=list(EXACT_OR_REFUSED)
)
async def test_what_would_not_arrive_as_written_is_refused(
    backend: JobBackend, tmp_path: Path, fields: dict[str, Any]
):
    spec = arrival(tmp_path, **{"arguments": [], **fields})
    try:
        statuses = await finished(backend, spec)
    except SpecificationRejectedError:
        return  # refused at submit, as it should be if it cannot arrive as written
    assert statuses == [JobStatus.DONE]


async def test_each_copy_has_its_own_environment_over_the_shared_one(
    backend: JobBackend, tmp_path: Path
):
    """A copy's variable wins over the shared one, and stays out of the other copies."""
    spec = SubmissionSpec(
        executable=script(
            tmp_path, '[ "$SHARED $WHO ${ONLY_FIRST-unset}" = "$EXPECTED" ]'
        ),
        environment={"SHARED": "both", "WHO": "nobody"},
    )
    copies = [
        CopyVars(
            environment={"WHO": "ann", "ONLY_FIRST": "yes", "EXPECTED": "both ann yes"}
        ),
        CopyVars(environment={"WHO": "bob", "EXPECTED": "both bob unset"}),
        CopyVars(environment={"EXPECTED": "both nobody unset"}),
    ]
    assert await finished(backend, spec, copies) == [JobStatus.DONE] * 3
