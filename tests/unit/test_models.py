"""Validation rules of the contract data types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intercede import (
    CopyVars,
    FileRef,
    JobHandle,
    JobOutput,
    JobStatus,
    OpOutcome,
    OutputMember,
    Submission,
    SubmissionSpec,
)


def handle(job_id: str = "1", **fields: object) -> JobHandle:
    return JobHandle.model_validate(
        {"backend": "fake", "resource": "r", "id": job_id, **fields}
    )


class TestJobHandle:
    def test_a_stored_handle_round_trips(self):
        stored = handle(routing={"schedd": "a"})
        assert JobHandle.model_validate_json(stored.model_dump_json()) == stored

    def test_a_newer_format_fails_loudly(self):
        with pytest.raises(ValidationError):
            handle(version=2)
        with pytest.raises(ValidationError):
            handle(extra="x")

    def test_the_same_id_on_two_resources_is_two_jobs(self):
        assert len({handle(resource="a"), handle(resource="b")}) == 2

    def test_key_separators_inside_parts_are_escaped(self):
        # Without escaping, ("a:b", "c") and ("a", "b:c") would share a key.
        first = handle("c", resource="a:b")
        second = handle("b:c", resource="a")
        assert first.key != second.key
        assert str(first) == first.key == "fake:a%3Ab:c"


@pytest.mark.parametrize("name", ["", ".", "/etc/passwd", "../up", "a/../../up"])
def test_names_must_stay_in_the_working_directory(name: str):
    with pytest.raises(ValidationError):
        FileRef(name=name, source="local/input")
    with pytest.raises(ValidationError):
        OutputMember(pattern=name)
    with pytest.raises(ValidationError):
        CopyVars(secrets={name: b"x"})
    with pytest.raises(ValidationError):
        SubmissionSpec(executable="x", stdout=name)


@pytest.mark.parametrize("name", ["input.txt", "sub/dir/file", "*.root"])
def test_relative_names_are_accepted(name: str):
    FileRef(name=name, source="local/input")
    OutputMember(pattern=name)


def test_secret_content_never_shows():
    copy = CopyVars(secrets={"pilot.secret": b"TOPSECRET"})
    for shown in (repr(copy), str(copy), copy.model_dump_json()):
        assert "TOPSECRET" not in shown


def test_an_input_name_is_given_once():
    # Two sources for one file in the working directory: which one would win?
    twice = [FileRef(name="in", source="a"), FileRef(name="in", source="b")]
    with pytest.raises(ValidationError, match="more than once"):
        SubmissionSpec(executable="x", inputs=twice)


def test_secrets_that_clash_with_inputs_are_named():
    spec = SubmissionSpec(executable="x", inputs=[FileRef(name="in", source="a")])
    assert spec.clashes_with(CopyVars(secrets={"in": b"s", "other": b"s"})) == {"in"}
    assert spec.clashes_with(CopyVars()) == frozenset()


def test_a_copy_is_either_submitted_or_refused():
    with pytest.raises(ValidationError):
        Submission(handles={0: handle()}, failures={0: "refused"})


class TestOpOutcome:
    def test_success_carries_a_value_unless_there_is_none(self):
        assert OpOutcome[None](ok=True, reason="already finished").ok
        with pytest.raises(ValidationError):
            OpOutcome[JobOutput](ok=True)

    def test_failure_has_a_reason_and_no_value(self):
        with pytest.raises(ValidationError):
            OpOutcome[None](ok=False)
        with pytest.raises(ValidationError):
            OpOutcome[JobOutput](ok=False, reason="x", value=JobOutput())


def test_job_status_values_are_stable():
    """Stored statuses are compared by value: renaming one is a breaking change."""
    assert {status.value for status in JobStatus} == {
        "Submitted",
        "Waiting",
        "Running",
        "Done",
        "Failed",
        "Aborted",
        "Unknown",
    }
