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
        assert build_default_handle(routing={"schedd": "a"}) != build_default_handle(
            routing={"schedd": "b"}
        )
        assert (
            len(
                {
                    build_default_handle(routing={"schedd": "a"}),
                    build_default_handle(routing={"schedd": "b"}),
                }
            )
            == 2
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
        sub = Submission(
            handles={1: build_default_handle("a")}, failures={0: "refused"}
        )
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


class TestJobHandleKey:
    def test_key_joins_backend_resource_and_id(self):
        assert build_default_handle("7").key == "fake:r:7"

    def test_str_is_the_key(self):
        handle = build_default_handle("7")
        assert str(handle) == handle.key

    def test_separators_inside_parts_are_escaped(self):
        # Without escaping, ("a:b", "c") and ("a", "b:c") would share a key.
        first = build_default_handle("c", resource="a:b")
        second = build_default_handle("b:c", resource="a")
        assert first.key != second.key
        assert first.key.count(":") == 2

    def test_equal_handles_hash_equal(self):
        assert hash(build_default_handle("1")) == hash(build_default_handle("1"))

    @pytest.mark.parametrize("field", ["backend", "resource", "id"])
    def test_every_identity_field_changes_the_key(self, field: str):
        assert (
            build_default_handle().key != build_default_handle(**{field: "other"}).key
        )

    def test_defaults(self):
        handle = build_default_handle()
        assert handle.version == 1
        assert handle.routing == {}


class TestOpOutcomeUnparametrised:
    def test_success_without_a_type_needs_no_value(self):
        assert OpOutcome(ok=True).ok

    def test_failure_without_a_type_still_needs_a_reason(self):
        with pytest.raises(ValidationError):
            OpOutcome(ok=False)


class TestDefaults:
    def test_submission_spec(self):
        spec = SubmissionSpec(executable="x")
        assert spec.outputs is None
        assert spec.container is None
        assert (spec.stdout, spec.stderr, spec.tag) == (None, None, None)
        assert tuple(spec.arguments) == ()
        assert spec.resources == Resources()

    def test_resources(self):
        resources = Resources()
        assert resources.cpus == 1
        assert (resources.memory_mb, resources.wall_time_s, resources.queue) == (
            None,
            None,
            None,
        )

    def test_job_output(self):
        output = JobOutput()
        assert (output.stdout, output.stderr, output.log) == (None, None, None)
        assert output.files == {}

    def test_empty_submission_is_valid(self):
        submission = Submission(handles={})
        assert submission.failures == {}


@pytest.mark.parametrize("value", [1, 2**31])
def test_positive_resources_are_accepted(value: int):
    assert Resources(cpus=value, memory_mb=value, wall_time_s=value).cpus == value


def test_job_status_values_are_stable():
    """Stored statuses are compared by value: renaming one is a breaking change."""
    from intercede import JobStatus

    assert {status.value for status in JobStatus} == {
        "Submitted",
        "Waiting",
        "Running",
        "Done",
        "Failed",
        "Aborted",
        "Unknown",
    }


@pytest.mark.parametrize("field", ["stdout", "stderr"])
@pytest.mark.parametrize("name", ["job.out", "logs/job.out"])
def test_stdout_and_stderr_names_are_accepted(field: str, name: str):
    assert getattr(SubmissionSpec(executable="x", **{field: name}), field) == name
