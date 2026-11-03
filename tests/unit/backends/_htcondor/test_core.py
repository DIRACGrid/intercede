"""The shared HTCondor knowledge: submit descriptions, commands and the state mapping.

Expected values were checked against HTCondor 25 (condor_submit -dry-run and a
live schedd): what is asserted here is what HTCondor actually does with them.
"""

from __future__ import annotations

from typing import Any

import pytest

from intercede import JobStatus
from intercede._backends._htcondor import commands, status, submit
from intercede._backends._htcondor.submit import CopyPlan, SubmitPlan, render

# --------------------------------------------------------------------------
# Quoting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "quoted"),
    [
        (["plain"], "\"'plain'\""),
        (["two words"], "\"'two words'\""),
        (["it's"], "\"'it''s'\""),
        (['say "hi"'], '"\'say ""hi""\'"'),
        (["cost $(HOME) $5"], "\"'cost $(DOLLAR)(HOME) $(DOLLAR)5'\""),
        ([""], "\"''\""),
        (["a", "b"], "\"'a' 'b'\""),
    ],
    ids=[
        "plain",
        "space",
        "single-quote",
        "double-quote",
        "dollar",
        "empty",
        "several",
    ],
)
def test_quote_arguments(arguments: list[str], quoted: str):
    assert submit.quote_arguments(arguments) == quoted


def test_quote_environment_sorts_and_quotes():
    quoted = submit.quote_environment({"STAMP": "a b", "X": "$(Y)", "A": "it's"})
    assert quoted == "\"A='it''s' STAMP='a b' X='$(DOLLAR)(Y)'\""


@pytest.mark.parametrize("name", ["", "A=B", "WITH SPACE", "DASH-ED", "$X"])
def test_environment_names_must_be_identifiers(name: str):
    with pytest.raises(ValueError, match="not a valid environment variable name"):
        submit.quote_environment({name: "x"})


@pytest.mark.parametrize("value", ["line\nbreak", "carriage\rreturn"])
def test_line_breaks_are_refused(value: str):
    with pytest.raises(ValueError, match="line break"):
        submit.quote_arguments([value])
    with pytest.raises(ValueError, match="line break"):
        submit.quote_environment({"X": value})


# --------------------------------------------------------------------------
# Submit descriptions
# --------------------------------------------------------------------------


def plan(**changes: Any) -> SubmitPlan:
    values: dict[str, Any] = {
        "executable": "/work/payload.sh",
        "transfer_executable": True,
        "arguments": ["a"],
        "copies": [
            CopyPlan("/work/0", {"STAMP": "s0"}, ["secrets/"]),
            CopyPlan("/work/1", {"STAMP": "s1"}),
        ],
    }
    return SubmitPlan(**{**values, **changes})


def lines(text: str) -> list[str]:
    return text.splitlines()


class TestRender:
    def test_one_process_per_copy(self):
        assert lines(render(plan()))[-1] == "queue 2"

    def test_per_copy_values_are_indexed_by_proc_id(self):
        text = render(plan())
        assert "ic_dir_0 = /work/0" in lines(text)
        assert "ic_dir_1 = /work/1" in lines(text)
        assert "ic_env_0 = \"STAMP='s0'\"" in lines(text)
        assert "ic_in_0 = secrets/" in lines(text)
        assert "ic_in_1 = " in lines(text)
        assert "initialdir = $(ic_dir_$(ProcId))" in lines(text)
        assert "environment = $(ic_env_$(ProcId))" in lines(text)

    def test_inputs_shared_and_per_copy(self):
        text = render(plan(inputs=["/work/inputs/", "https://example.org/in.dat"]))
        assert (
            "transfer_input_files = /work/inputs/, https://example.org/in.dat, $(ic_in_$(ProcId))"
            in lines(text)
        )

    @pytest.mark.parametrize(
        ("outputs", "expected"),
        [
            (
                [],
                'transfer_output_files = ""',
            ),  # nothing but stdout, stderr and the log
            (["result.txt", "dir/x"], "transfer_output_files = result.txt, dir/x"),
            (None, None),  # every file the payload creates
        ],
        ids=["none", "listed", "all"],
    )
    def test_outputs(self, outputs: list[str] | None, expected: str | None):
        text = render(plan(outputs=outputs))
        found = [
            line for line in lines(text) if line.startswith("transfer_output_files")
        ]
        assert found == ([expected] if expected else [])

    def test_output_remaps_upload_to_urls(self):
        text = render(
            plan(
                outputs=["a", "b"],
                output_remaps={"b": "https://s/b", "a": "https://s/a"},
            )
        )
        assert 'transfer_output_remaps = "a = https://s/a; b = https://s/b"' in lines(
            text
        )

    def test_resources_for_a_schedd_and_for_a_ce_router(self):
        text = render(plan(cpus=8, memory_mb=4000, wall_time_s=7200, queue="long"))
        for line in [
            "request_cpus = 8",
            "+xcount = 8",
            "request_memory = 4000",
            "+maxMemory = 4000",
            "+BatchRuntime = 7200",
            '+queue = "long"',
        ]:
            assert line in lines(text)

    def test_proxy(self):
        assert "x509userproxy = /creds/proxy" in lines(
            render(plan(proxy_file="/creds/proxy"))
        )
        assert not any(
            line.startswith("x509userproxy") for line in lines(render(plan()))
        )

    def test_finished_jobs_stay_until_purged(self):
        # The one lifetime rule interCEde sets: output can be collected
        # again until the consumer releases the job.
        assert "leave_in_queue = (JobStatus == 4)" in lines(render(plan()))

    @pytest.mark.parametrize(
        "command",
        [
            "on_exit_hold",
            "on_exit_remove",
            "periodic_hold",
            "periodic_release",
            "periodic_remove",
            "max_retries",
            "requirements",
        ],
    )
    def test_sets_no_policy_on_the_job(self, command: str):
        # Holding, releasing, removing or retrying jobs is the consumer's
        # policy, never interCEde's.
        assert not any(line.startswith(command) for line in lines(render(plan())))

    def test_macros_in_paths_are_escaped(self):
        assert "ic_dir_0 = /work/$(DOLLAR)0" in lines(
            render(plan(copies=[CopyPlan("/work/$0")]))
        )

    @pytest.mark.parametrize("field", ["inputs", "outputs"])
    def test_file_lists_cannot_hold_a_comma(self, field: str):
        with pytest.raises(ValueError, match="comma"):
            render(plan(**{field: ["a,b"]}))

    def test_paths_cannot_hold_a_line_break(self):
        with pytest.raises(ValueError, match="line break"):
            render(plan(copies=[CopyPlan("/work/a\nqueue 100")]))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


class TestCommands:
    def test_submit_to_the_local_schedd_spools(self):
        assert commands.submit("/w/job.sub") == [
            "condor_submit",
            "-terse",
            "-spool",
            "/w/job.sub",
        ]

    @pytest.mark.parametrize(
        ("count", "sizes"),
        [(100, [100]), (101, [100, 1]), (250, [100, 100, 50])],
    )
    def test_chunks(self, count: int, sizes: list[int]):
        ids = [status.JobID(f"1.{i}") for i in range(count)]
        chunked = commands.chunks(ids)
        assert [len(chunk) for chunk in chunked] == sizes
        assert [i for chunk in chunked for i in chunk] == ids


class TestParseSubmit:
    @pytest.mark.parametrize(
        ("raw", "ids"),
        [
            ("1.0 - 1.1\n", ["1.0", "1.1"]),
            ("  42.0 - 42.0  ", ["42.0"]),
            ("\n7.3 - 7.5\n\n", ["7.3", "7.4", "7.5"]),
        ],
    )
    def test_terse_output(self, raw: str, ids: list[str]):
        assert commands.parse_submit(raw) == ids

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "Submitting job(s).\n1 job(s) submitted to cluster 1.",
            "1.0 - 2.0",
            "1.3 - 1.1",
            "1.0 - 1.1\n2.0 - 2.1",
            "x",
        ],
        ids=["empty", "verbose", "two-clusters", "backwards", "two-lines", "garbage"],
    )
    def test_anything_else_is_refused(self, raw: str):
        with pytest.raises(ValueError, match="unexpected condor_submit output"):
            commands.parse_submit(raw)


def test_parse_remove():
    raw = "Job 1.0 marked for removal\nJob 1.2 marked for removal\nCouldn't find/remove all jobs matching 1.1\n"
    assert commands.parse_remove(raw) == {"1.0", "1.2"}
    assert commands.parse_remove("") == set()


# --------------------------------------------------------------------------
# State mapping
# --------------------------------------------------------------------------


def ad(**fields: Any) -> dict[str, Any]:
    return {"ClusterId": 3, "ProcId": 1, **fields}


class TestJobStatus:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (1, JobStatus.WAITING),
            (2, JobStatus.RUNNING),
            (4, JobStatus.DONE),
            (6, JobStatus.RUNNING),
            (7, JobStatus.WAITING),
        ],
        ids=["idle", "running", "completed", "transferring-output", "suspended"],
    )
    def test_plain_states(self, code: int, expected: JobStatus):
        assert status.job_status(ad(JobStatus=code)) == expected

    @pytest.mark.parametrize(
        "code",
        [8, None],
        ids=["unexpected", "missing"],
    )
    def test_unknown_codes(self, code: Any):
        assert status.job_status(ad(JobStatus=code)) == JobStatus.UNKNOWN

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"ExitCode": 0, "ExitBySignal": False}, JobStatus.DONE),
            ({"ExitCode": 3, "ExitBySignal": False}, JobStatus.FAILED),
            ({"ExitBySignal": True, "ExitSignal": 9}, JobStatus.FAILED),
            ({}, JobStatus.DONE),
        ],
        ids=["exit-0", "exit-3", "signal", "no-exit-info"],
    )
    def test_finished_payload_result_comes_from_its_exit(
        self, fields: dict[str, Any], expected: JobStatus
    ):
        # Read from what HTCondor records; no hold is put on the job to tell.
        assert status.job_status(ad(JobStatus=4, **fields)) == expected

    def test_spooling_is_waiting(self):
        assert (
            status.job_status(ad(JobStatus=5, HoldReasonCode=16)) == JobStatus.WAITING
        )

    @pytest.mark.parametrize(
        "fields",
        [{"HoldReasonCode": 1}, {"HoldReasonCode": 3, "HoldReasonSubCode": 55}, {}],
        ids=["by-user", "by-site-policy", "no-reason"],
    )
    def test_any_other_hold_is_aborted(self, fields: dict[str, Any]):
        assert status.job_status(ad(JobStatus=5, **fields)) == JobStatus.ABORTED

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"LastJobStatus": 4, "ExitCode": 0}, JobStatus.DONE),
            ({"LastJobStatus": 4}, JobStatus.DONE),
            ({"LastJobStatus": 4, "ExitCode": 2}, JobStatus.FAILED),
            ({"LastJobStatus": 4, "ExitBySignal": True}, JobStatus.FAILED),
            ({"LastJobStatus": 5, "ExitCode": 3}, JobStatus.ABORTED),
            ({"LastJobStatus": 2}, JobStatus.ABORTED),
            ({"LastJobStatus": 1}, JobStatus.ABORTED),
            ({}, JobStatus.ABORTED),
        ],
        ids=[
            "purged-done",
            "purged-done-no-code",
            "purged-failed",
            "purged-signal",
            "removed-while-held",
            "killed-running",
            "killed-idle",
            "bare",
        ],
    )
    def test_removed_jobs_keep_their_result(
        self, fields: dict[str, Any], expected: JobStatus
    ):
        assert status.job_status(ad(JobStatus=3, **fields)) == expected


class TestParseAds:
    def test_condor_q_prints_nothing_for_no_match(self):
        assert status.parse_ads("") == []
        assert status.parse_ads("\n  \n") == []

    def test_condor_history_prints_an_empty_list(self):
        assert status.parse_ads("[\n]\n") == []

    @pytest.mark.parametrize("raw", ["{}", "[1, 2]", '"text"', "not json"])
    def test_anything_else_is_refused(self, raw: str):
        with pytest.raises(ValueError):
            status.parse_ads(raw)

    def test_every_status_query_asks_for_what_the_mapping_reads(self):
        assert {
            "ClusterId",
            "ProcId",
            "JobStatus",
            "LastJobStatus",
            "ExitCode",
            "ExitBySignal",
            "HoldReasonCode",
        } <= set(status.STATE_ATTRIBUTES)
