"""The HTCondor-CE backend: what the shared suite cannot show on a healthy stack.

Normal behaviour is the shared integration suite's, on the htcondor stack. These
tests drive the backend through its public operations, against simulated tools,
for what a stack does not produce: states it never reaches, refusals, partial
failures, and answers a CE gives when something is wrong.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from intercede import (
    AuthenticationError,
    ConfigurationError,
    ContainerSpec,
    CopyVars,
    FileRef,
    JobStatus,
    OutputMember,
    OutputSpec,
    ResourceUnavailableError,
    SpecificationRejectedError,
)
from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede.auth import BearerToken, StaticCredentials

from ._fake_condor import SPEC, TOKEN, FakeCondor, handle, make, ok, settings


class TestStatus:
    @pytest.mark.parametrize(
        ("code", "fields", "expected"),
        [
            (1, {}, JobStatus.WAITING),
            (2, {}, JobStatus.RUNNING),
            (6, {}, JobStatus.RUNNING),  # transferring output
            (7, {}, JobStatus.WAITING),  # suspended
            (8, {}, JobStatus.UNKNOWN),
            (4, {"ExitCode": 0}, JobStatus.DONE),
            (4, {"ExitCode": 3}, JobStatus.FAILED),
            (4, {"ExitBySignal": True, "ExitSignal": 9}, JobStatus.FAILED),
            (5, {"HoldReasonCode": 16}, JobStatus.WAITING),  # inputs still spooling
            (5, {"HoldReasonCode": 3}, JobStatus.ABORTED),
            (3, {"LastJobStatus": 4, "ExitCode": 0}, JobStatus.DONE),  # purged
            (3, {"LastJobStatus": 4, "ExitCode": 2}, JobStatus.FAILED),
            (3, {"LastJobStatus": 2}, JobStatus.ABORTED),  # killed while running
        ],
        ids=[
            "idle",
            "running",
            "transferring",
            "suspended",
            "unexpected",
            "exit-0",
            "exit-3",
            "signal",
            "spooling",
            "held",
            "purged-done",
            "purged-failed",
            "killed",
        ],
    )
    async def test_the_states_the_ce_reports(
        self,
        ce: HTCondorCEBackend,
        condor: FakeCondor,
        code: int,
        fields: dict[str, Any],
        expected: JobStatus,
    ):
        job = (await ce.submit(SPEC)).handles[0]
        condor.finish(job.id, code, **fields)
        assert await ce.get_status([job]) == {job: expected}

    @pytest.mark.parametrize(
        "foreign",
        [
            {"resource": "other.example.org:9619"},
            {"backend": "arc"},
            {"id": "-constraint true"},
            {"id": "1"},
            {"id": "1.0 1.1"},
        ],
        ids=["other-ce", "other-backend", "option-injection", "no-proc", "two-ids"],
    )
    async def test_foreign_and_malformed_handles_never_reach_a_command(
        self, ce: HTCondorCEBackend, condor: FakeCondor, foreign: dict[str, str]
    ):
        bad = handle("1.0", **foreign)
        assert await ce.get_status([bad]) == {bad: JobStatus.UNKNOWN}
        assert condor.calls == []


class TestSubmit:
    @pytest.mark.parametrize(
        "fields",
        [
            {"inputs": [FileRef(name="renamed.dat", source="https://example.org/in")]},
            {"outputs": OutputSpec(members=[OutputMember(pattern="*.root")])},
            {
                "outputs": OutputSpec(
                    members=[OutputMember(pattern="*.root", destination="https://se/")],
                    include_unlisted=True,
                )
            },
            {"container": ContainerSpec(image="docker://alpine")},
        ],
        ids=["url-input-renamed", "pattern", "pattern-uploaded", "no-launcher"],
    )
    async def test_refused_before_any_call(
        self, ce: HTCondorCEBackend, condor: FakeCondor, fields: dict[str, Any]
    ):
        with pytest.raises(SpecificationRejectedError):
            await ce.submit(SPEC.model_copy(update=fields))
        assert "condor_submit" not in condor.tools()

    async def test_a_value_refused_in_a_later_cluster_submits_nothing(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        # Different variable names go in different clusters.
        copies = [CopyVars(environment={"A": "1"}), CopyVars(environment={"B": "a\nb"})]
        with pytest.raises(SpecificationRejectedError, match="line break"):
            await ce.submit(SPEC, copies)
        assert "condor_submit" not in condor.tools()

    async def test_a_later_cluster_failing_keeps_the_submitted_copies(
        self, condor: FakeCondor, workdir: Path
    ):
        copies = [CopyVars(environment={"A": "1"}), CopyVars(environment={"B": "2"})]
        submitted = 0

        async def second_submit_fails(
            argv: Sequence[str], env: Mapping[str, str], timeout: float
        ) -> CommandResult:
            nonlocal submitted
            if argv[0] == "condor_submit":
                submitted += 1
                if submitted == 2:
                    return CommandResult(
                        returncode=1, stdout="", stderr="ERROR: schedd full"
                    )
            return await condor(argv, env, timeout)

        backend = HTCondorCEBackend(
            settings(),
            StaticCredentials([BearerToken(value=TOKEN)]),
            runner=second_submit_fails,
            workdir=workdir,
        )
        async with backend:
            sub = await backend.submit(SPEC, copies)
        assert sorted(sub.handles) == [0]
        assert sub.failures == {1: "condor_submit failed: ERROR: schedd full"}

    async def test_secrets_and_the_token_exist_only_privately_during_the_call(
        self, ce: HTCondorCEBackend, condor: FakeCondor, workdir: Path
    ):
        copies = [
            CopyVars(secrets={"pilot.secret": b"TOPSECRET-0"}),
            CopyVars(secrets={"pilot.secret": b"TOPSECRET-1"}),
        ]
        await ce.submit(SPEC, copies)
        call = condor.calls[0]
        secrets = [entry for path, entry in call.files.items() if "pilot" in path]
        assert sorted(secrets) == [(0o600, b"TOPSECRET-0"), (0o600, b"TOPSECRET-1")]
        assert "TOPSECRET" not in condor.descriptions[0]
        token = call.env["_CONDOR_SCITOKENS_FILE"]
        assert call.files[token] == (0o600, TOKEN.encode())
        assert not Path(token).exists()
        assert not [p for p in workdir.rglob("*") if p.is_file()]

    async def test_a_link_in_place_of_the_working_area_is_refused(
        self, condor: FakeCondor, tmp_path: Path
    ):
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = tmp_path / "work"
        link.symlink_to(target)
        async with make(condor, link) as backend:
            with pytest.raises(ConfigurationError, match="not a directory owned"):
                await backend.submit(SPEC)

    async def test_resources_and_the_queue_reach_the_ce(
        self, condor: FakeCondor, workdir: Path
    ):
        resources = SPEC.resources.model_copy(
            update={"cpus": 4, "memory_mb": 8000, "wall_time_s": 3600}
        )
        async with make(condor, workdir, queue="default") as backend:
            await backend.submit(SPEC.model_copy(update={"resources": resources}))
            short = resources.model_copy(update={"queue": "short"})
            await backend.submit(SPEC.model_copy(update={"resources": short}))
        configured, named = condor.descriptions
        for line in ["+xcount = 4", "+maxMemory = 8000", "+BatchRuntime = 3600"]:
            assert line in configured
        assert '+queue = "default"' in configured
        assert '+queue = "short"' in named  # the spec's queue wins


class TestErrors:
    @pytest.mark.parametrize(
        "stderr",
        [
            "10/21/26 13:52:19 AUTHENTICATE:1003:Failed to authenticate with any method\n"
            "Error: Couldn't contact the condor_collector",
            "PERMISSION DENIED to submituser",
            # condor_submit with a rejected token (HTCondor 25): no AUTHENTICATE
            # summary, and an error that looks like an unreachable schedd.
            "10/26/26 11:37:15 AUTHENTICATE: method 2048 (IDTOKENS) failed.\n"
            "10/26/26 11:37:15 SECMAN: required authentication with collector "
            "127.0.0.1:9619 failed, so aborting command QUERY_SCHEDD_ADS.\n"
            "ERROR: Can't find address of schedd htcondor-ce@backend",
        ],
        ids=["token-rejected", "permission-denied", "submit-token-rejected"],
    )
    async def test_a_refused_token(
        self, ce: HTCondorCEBackend, condor: FakeCondor, stderr: str
    ):
        condor.fail["condor_submit"] = CommandResult(
            returncode=1, stdout="", stderr=stderr
        )
        with pytest.raises(AuthenticationError):
            await ce.submit(SPEC)

    @pytest.mark.parametrize(
        "stderr",
        [
            "10/21/26 13:52:19 connect errno = 111\n"
            "Error: Couldn't contact the condor_collector on ce:9619.",
            "Can't find address for schedd ce.example.org",
        ],
        ids=["refused", "no-address"],
    )
    async def test_a_ce_that_cannot_be_reached(
        self, ce: HTCondorCEBackend, condor: FakeCondor, stderr: str
    ):
        condor.fail["condor_q"] = CommandResult(returncode=1, stdout="", stderr=stderr)
        with pytest.raises(ResourceUnavailableError, match="could not be reached"):
            await ce.get_status([handle("1.0")])

    async def test_nothing_submitted_fails_the_operation(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        condor.fail["condor_submit"] = CommandResult(
            returncode=1, stdout="", stderr="ERROR: schedd full"
        )
        with pytest.raises(ResourceUnavailableError, match="schedd full"):
            await ce.submit(SPEC, 2)

    @pytest.mark.parametrize(
        ("tool", "raw", "copies"),
        [
            ("condor_submit", "Submitting job(s).", 1),
            ("condor_submit", "1.0 - 1.0\n2.0 - 2.0", 1),
            ("condor_submit", "5.0 - 5.0", 2),  # fewer jobs than copies
            ("condor_q", "not json", 1),
        ],
        ids=["submit-text", "submit-two-clusters", "submit-too-few", "query-text"],
    )
    async def test_an_answer_that_cannot_be_read(
        self,
        ce: HTCondorCEBackend,
        condor: FakeCondor,
        tool: str,
        raw: str,
        copies: int,
    ):
        condor.fail[tool] = ok(raw)
        with pytest.raises(ResourceUnavailableError):
            if tool == "condor_submit":
                await ce.submit(SPEC, copies)
            else:
                await ce.get_status([handle("1.0")])
