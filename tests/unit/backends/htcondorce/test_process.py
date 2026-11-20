"""The HTCondor-CE backend: how every tool runs, how failures map to errors, and the process runner."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from intercede import (
    AuthenticationError,
    ConfigurationError,
    JobHandle,
    JobID,
    ResourceUnavailableError,
)
from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede._backends.monolith.htcondorce.process import run_process
from intercede.auth import BearerToken, StaticCredentials

from ._fake_condor import ENDPOINT, SPEC, TOKEN, FakeCondor, make, ok, settings


class TestEnvironment:
    async def test_every_tool_runs_with_debug_for_error_classification(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        await ce.submit(SPEC)
        assert all(call.argv[1] == "-debug" for call in condor.calls)

    async def test_authentication_is_required_and_token_based(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        await ce.submit(SPEC)
        env = condor.calls[0].env
        assert env["_CONDOR_SEC_CLIENT_AUTHENTICATION"] == "REQUIRED"
        assert env["_CONDOR_SEC_CLIENT_AUTHENTICATION_METHODS"] == "SCITOKENS,IDTOKENS"
        assert env["_CONDOR_TOOL_DEBUG"] == "D_SECURITY"

    async def test_token_is_a_private_file_only_during_the_call(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        await ce.submit(SPEC)
        call = condor.calls[0]
        token = call.env["_CONDOR_SCITOKENS_FILE"]
        assert call.files[token] == (0o600, TOKEN.encode())
        assert not Path(token).exists()
        assert not Path(call.env["_CONDOR_SEC_TOKEN_DIRECTORY"]).exists()
        assert not Path(call.env["_CONDOR_LOG"]).exists()

    async def test_no_config_file_unless_the_operator_set_one(
        self, ce: HTCondorCEBackend, condor: FakeCondor, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("CONDOR_CONFIG", raising=False)
        await ce.get_status(
            [JobHandle(backend="htcondor-ce", resource=ENDPOINT, id=JobID("1.0"))]
        )
        assert condor.calls[-1].env["CONDOR_CONFIG"] == "ONLY_ENV"
        monkeypatch.setenv("CONDOR_CONFIG", "/etc/condor/site.conf")
        await ce.get_status(
            [JobHandle(backend="htcondor-ce", resource=ENDPOINT, id=JobID("1.0"))]
        )
        assert condor.calls[-1].env["CONDOR_CONFIG"] == "/etc/condor/site.conf"

    async def test_ca_directory_from_x509_cert_dir(
        self, ce: HTCondorCEBackend, condor: FakeCondor, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("X509_CERT_DIR", "/etc/grid-security/certificates")
        await ce.submit(SPEC)
        assert (
            condor.calls[0].env["_CONDOR_AUTH_SSL_CLIENT_CADIR"]
            == "/etc/grid-security/certificates"
        )

    async def test_scopes_follow_the_operation(self, condor: FakeCondor, workdir: Path):
        asked: list[frozenset[str]] = []

        class Recording(StaticCredentials):
            async def get(self, requirements: Any) -> Any:
                asked.append(requirements.scopes)
                return await super().get(requirements)

        handle = JobHandle(backend="htcondor-ce", resource=ENDPOINT, id=JobID("1.0"))
        backend = HTCondorCEBackend(
            settings(),
            Recording([BearerToken(value=TOKEN)]),
            runner=condor,
            workdir=workdir,
        )
        async with backend:
            await backend.submit(SPEC)
            await backend.get_status([handle])
            await backend.kill([handle])
        assert asked[:2] == [frozenset({"compute.create"}), frozenset({"compute.read"})]


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
    async def test_authentication_failure(
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
            "10/21/26 13:52:19 connect errno = 111\nError: Couldn't contact the condor_collector on ce:9619.",
            "Can't find address for schedd ce.example.org",
        ],
        ids=["refused", "no-address"],
    )
    async def test_unreachable(
        self, ce: HTCondorCEBackend, condor: FakeCondor, stderr: str
    ):
        condor.fail["condor_q"] = CommandResult(returncode=1, stdout="", stderr=stderr)
        with pytest.raises(ResourceUnavailableError, match="could not be reached"):
            await ce.get_status(
                [JobHandle(backend="htcondor-ce", resource=ENDPOINT, id=JobID("1.0"))]
            )

    async def test_other_failures_name_the_tool_without_debug_lines(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        condor.fail["condor_submit"] = CommandResult(
            returncode=1,
            stdout="",
            stderr="10/21/26 13:52:19 SECMAN: noise\nERROR: on Line 3 of submit file\n",
        )
        with pytest.raises(
            ResourceUnavailableError,
            match="condor_submit failed: ERROR: on Line 3 of submit file$",
        ):
            await ce.submit(SPEC)

    @pytest.mark.parametrize("raw", ["Submitting job(s).", "1.0 - 1.0\n2.0 - 2.0"])
    async def test_unreadable_submit_output(
        self, ce: HTCondorCEBackend, condor: FakeCondor, raw: str
    ):
        condor.fail["condor_submit"] = ok(raw)
        with pytest.raises(
            ResourceUnavailableError, match="unexpected condor_submit output"
        ):
            await ce.submit(SPEC)

    async def test_job_count_mismatch(self, ce: HTCondorCEBackend, condor: FakeCondor):
        condor.fail["condor_submit"] = ok("5.0 - 5.0")
        with pytest.raises(
            ResourceUnavailableError, match="created 1 jobs, expected 2"
        ):
            await ce.submit(SPEC, 2)

    async def test_unreadable_query_output(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        condor.fail["condor_q"] = ok("not json")
        with pytest.raises(ResourceUnavailableError, match="unreadable output"):
            await ce.get_status(
                [JobHandle(backend="htcondor-ce", resource=ENDPOINT, id=JobID("1.0"))]
            )

    async def test_no_token_from_the_provider(self, condor: FakeCondor, workdir: Path):
        async with make(condor, workdir, credentials=[]) as backend:
            with pytest.raises(AuthenticationError, match="BearerToken"):
                await backend.submit(SPEC)
        assert condor.calls == []


class TestRunProcess:
    async def test_runs_a_command_with_only_the_given_environment(self):
        result = await run_process(
            ["/bin/sh", "-c", "echo $ONLY; echo err >&2; exit 3"],
            {"ONLY": "this", "PATH": os.environ["PATH"]},
            10,
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            3,
            "this\n",
            "err\n",
        )

    async def test_missing_tool_is_a_configuration_error(self):
        with pytest.raises(ConfigurationError, match="HTCondor client tools"):
            await run_process(["condor_definitely_not_installed"], dict(os.environ), 10)

    async def test_timeout_kills_the_tool(self):
        with pytest.raises(ResourceUnavailableError, match="did not finish"):
            await run_process(["/bin/sleep", "10"], dict(os.environ), 0.2)
