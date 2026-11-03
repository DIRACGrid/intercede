"""The shared HTCondor knowledge: submit descriptions, commands and the state mapping.

Expected values were checked against HTCondor 25 (condor_submit -dry-run and a
live schedd): what is asserted here is what HTCondor actually does with them.
"""

from __future__ import annotations

from typing import Any

import pytest

from intercede._backends._htcondor import submit
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
