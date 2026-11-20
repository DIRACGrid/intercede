"""The HTCondor-CE backend: output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede.auth import BearerToken, StaticCredentials

from ._fake_condor import SPEC, TOKEN, FakeCondor, make, ok, settings


class TestOutput:
    async def test_collects_into_dest_with_a_manifest(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id)
        condor.outputs[h.id] = {"result.txt": b"42", "sub/dir/data.bin": b"\x00"}
        outcome = (await ce.get_output([h], tmp_path / "dest"))[h]
        assert outcome.ok and outcome.value is not None
        target = tmp_path / "dest" / "ce.example.org" / h.id
        assert outcome.value.stdout == target / "1.0.out"
        assert outcome.value.stdout.read_bytes() == b"stdout of 1.0"
        assert outcome.value.stderr == target / "1.0.err"
        assert outcome.value.log == target / "1.0.log"
        assert outcome.value.files == {
            "result.txt": target / "result.txt",
            "sub/dir/data.bin": target / "sub/dir/data.bin",
        }

    async def test_custom_stdout_name_is_recognised(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (
            await ce.submit(SPEC.model_copy(update={"stdout": "logs/pilot.out"}))
        ).handles[0]
        condor.finish(h.id)
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert outcome.value is not None and outcome.value.stdout is not None
        assert outcome.value.stdout.name == "pilot.out"
        assert "logs/pilot.out" not in outcome.value.files

    async def test_repeatable_and_replacing(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id)
        condor.outputs[h.id] = {"old.txt": b"1"}
        await ce.get_output([h], tmp_path)
        condor.outputs[h.id] = {"new.txt": b"2"}
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert outcome.value is not None and set(outcome.value.files) == {"new.txt"}
        assert not (tmp_path / "ce.example.org" / h.id / "old.txt").exists()

    async def test_collection_area_is_cleaned(
        self, ce: HTCondorCEBackend, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id)
        await ce.get_output([h], tmp_path / "dest")
        assert not [p for p in workdir.rglob("*") if p.is_file()]

    @pytest.mark.parametrize("code", [1, 2], ids=["waiting", "running"])
    async def test_active_jobs(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path, code: int
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, code=code)
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert not outcome.ok and outcome.reason == "job has not finished"
        assert "condor_transfer_data" not in condor.tools()

    async def test_failed_jobs_can_be_collected(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, ExitCode=3)  # completed, payload exited non-zero
        assert (await ce.get_output([h], tmp_path))[h].ok

    async def test_job_that_left_the_queue(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        await ce.purge([h])
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert (
            not outcome.ok
            and outcome.reason == "the job has left the queue; its output is gone"
        )

    @pytest.mark.parametrize(
        "iwd", ["/etc", "relative/dir", "{workdir}", "{workdir}/../escape"]
    )
    async def test_the_ce_cannot_choose_where_we_write(
        self,
        ce: HTCondorCEBackend,
        condor: FakeCondor,
        workdir: Path,
        tmp_path: Path,
        iwd: str,
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, SUBMIT_Iwd=iwd.format(workdir=workdir))
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert (
            not outcome.ok
            and outcome.reason
            == "the job's output directory is outside our working area"
        )
        assert "condor_transfer_data" not in condor.tools()

    async def test_transfer_failure_is_per_job(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id)
        condor.fail["condor_transfer_data"] = CommandResult(
            returncode=1, stdout="", stderr="ERROR: spool gone"
        )
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert (
            not outcome.ok
            and outcome.reason == "condor_transfer_data failed: ERROR: spool gone"
        )

    async def test_too_many_files(
        self, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        async with make(condor, workdir, max_output_files=3) as backend:
            h = (await backend.submit(SPEC)).handles[0]
            condor.finish(h.id)
            condor.outputs[h.id] = {"a": b"", "b": b""}  # plus stdout, stderr and log
            outcome = (await backend.get_output([h], tmp_path))[h]
        assert not outcome.ok and outcome.reason == "more than 3 output files"

    async def test_too_many_bytes(
        self, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        async with make(condor, workdir, max_output_bytes=10) as backend:
            h = (await backend.submit(SPEC)).handles[0]
            condor.finish(h.id)
            condor.outputs[h.id] = {"big": b"x" * 100}
            outcome = (await backend.get_output([h], tmp_path))[h]
        assert not outcome.ok and outcome.reason == "output larger than 10 bytes"

    async def test_links_are_refused(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id)
        real = condor._transfer

        def with_link(ids: list[str]) -> CommandResult:
            result = real(ids)
            (Path(condor.queue[h.id]["SUBMIT_Iwd"]) / "link").symlink_to("/etc/passwd")
            return result

        condor._transfer = with_link  # type: ignore[method-assign]
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert not outcome.ok and "link" in (outcome.reason or "")

    async def test_transfer_uses_the_configured_timeout(
        self, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        timeouts: dict[str, float] = {}

        async def recording(
            argv: Sequence[str], env: Mapping[str, str], timeout: float
        ) -> CommandResult:
            timeouts[argv[0]] = timeout
            return await condor(argv, env, timeout)

        backend = HTCondorCEBackend(
            settings(transfer_timeout_s=42),
            StaticCredentials([BearerToken(value=TOKEN)]),
            runner=recording,
            workdir=workdir,
        )
        async with backend:
            h = (await backend.submit(SPEC)).handles[0]
            condor.finish(h.id)
            await backend.get_output([h], tmp_path)
        assert timeouts["condor_transfer_data"] == 42
        assert timeouts["condor_submit"] == 120


class TestOutputEdgeCases:
    async def test_transfer_that_writes_nothing(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id)
        condor._transfer = lambda ids: ok("Fetching data files...\n")  # type: ignore[method-assign]
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert (
            not outcome.ok
            and outcome.reason == "condor_transfer_data returned no files"
        )

    async def test_log_outside_the_job_directory_is_not_the_log(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, UserLog="/var/log/elsewhere.log")
        outcome = (await ce.get_output([h], tmp_path))[h]
        assert outcome.ok and outcome.value is not None
        assert outcome.value.log is None
        assert (
            "elsewhere.log" in outcome.value.files
        )  # written into the job directory by name
