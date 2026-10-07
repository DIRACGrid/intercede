"""Validation rules of the contract data types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intercede import (
    CopyVars,
    Diagnostics,
    FileRef,
    JobHandle,
    JobID,
    JobOutput,
    OpOutcome,
    OutputMember,
    Resources,
    Submission,
    SubmissionSpec,
)


def build_default_handle(job_id: str = "1", **kwargs: object) -> JobHandle:
    return JobHandle.model_validate(
        {"backend": "fake", "resource": "r", "id": job_id, **kwargs}
    )


class TestJobHandle:
    def test_round_trips_through_json(self):
        handle = build_default_handle(routing={"schedd": "a"})
        assert JobHandle.model_validate_json(handle.model_dump_json()) == handle

    def test_is_hashable_with_routing(self):
        handle = build_default_handle(routing={"schedd": "a"})
        assert {handle: 1}[build_default_handle(routing={"schedd": "a"})] == 1

    def test_same_id_on_different_resources_are_different_keys(self):
        handle_a = build_default_handle(resource="cluster-a")
        handle_b = build_default_handle(resource="cluster-b")
        assert len({handle_a: 1, handle_b: 2}) == 2

    def test_routing_is_part_of_identity(self):
        assert build_default_handle(routing={"schedd": "a"}) != build_default_handle(routing={"schedd": "b"})
        assert (
            len({build_default_handle(routing={"schedd": "a"}), build_default_handle(routing={"schedd": "b"})}) == 2
        )

    def test_rejects_unknown_fields_and_versions(self):
        with pytest.raises(ValidationError):
            build_default_handle(extra="x")
        with pytest.raises(ValidationError):
            build_default_handle(version=2)

    def test_is_immutable(self):
        with pytest.raises(ValidationError):
            build_default_handle().id = JobID("2")  # type: ignore[misc]


class TestCopyVars:
    def test_secret_content_never_appears_in_repr(self):
        copy = CopyVars(secrets={"pilot.secret": b"TOPSECRET"})
        assert "TOPSECRET" not in repr(copy)
        assert "TOPSECRET" not in str(copy)
        assert "TOPSECRET" not in copy.model_dump_json()

    def test_secret_content_is_readable_on_purpose(self):
        copy = CopyVars(secrets={"pilot.secret": b"TOPSECRET"})
        assert copy.secrets["pilot.secret"].get_secret_value() == b"TOPSECRET"


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


@pytest.mark.parametrize("field", ["cpus", "memory_mb", "wall_time_s"])
def test_resources_must_be_positive(field: str):
    with pytest.raises(ValidationError):
        Resources.model_validate({field: 0})


class TestSubmission:
    def test_handles_are_keyed_by_copy_index(self):
        sub = Submission(handles={1: build_default_handle("a")}, failures={0: "refused"})
        assert sub.handles[1].id == "a"

    def test_a_copy_cannot_be_both_submitted_and_refused(self):
        with pytest.raises(ValidationError):
            Submission(handles={0: build_default_handle()}, failures={0: "refused"})


class TestOpOutcome:
    def test_success_carries_a_value(self):
        assert OpOutcome[Diagnostics](
            ok=True, value=Diagnostics(name="n", content="c")
        ).ok

    def test_success_without_a_value_is_only_valid_for_none(self):
        assert OpOutcome[None](ok=True).ok
        with pytest.raises(ValidationError):
            OpOutcome[JobOutput](ok=True)

    def test_success_may_give_a_reason(self):
        assert (
            OpOutcome[None](ok=True, reason="already finished").reason
            == "already finished"
        )

    def test_failure_needs_a_reason(self):
        with pytest.raises(ValidationError):
            OpOutcome[None](ok=False)

    def test_failure_cannot_carry_a_value(self):
        with pytest.raises(ValidationError):
            OpOutcome[JobOutput](ok=False, reason="x", value=JobOutput())
