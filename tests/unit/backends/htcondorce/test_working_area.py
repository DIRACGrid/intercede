"""The HTCondor-CE backend: the working area, and keeping nothing between calls."""

from __future__ import annotations

import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from intercede import ConfigurationError, JobStatus
from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede.auth import BearerToken, StaticCredentials

from ._fake_condor import SPEC, TOKEN, FakeCondor, make, settings


class TestWorkingArea:
    async def test_is_private(self, ce: HTCondorCEBackend, workdir: Path):
        await ce.submit(SPEC)
        assert stat.S_IMODE(workdir.stat().st_mode) == 0o700

    async def test_a_link_in_its_place_is_refused(
        self, condor: FakeCondor, tmp_path: Path
    ):
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = tmp_path / "work"
        link.symlink_to(target)
        async with make(condor, link) as backend:
            with pytest.raises(
                ConfigurationError, match="not a directory owned by this user"
            ):
                await backend.submit(SPEC)


class TestStateless:
    """The backend keeps nothing about jobs between calls."""

    async def test_no_job_is_remembered_after_collection(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        import gc

        sub = await ce.submit(SPEC, 3)
        for h in sub.handles.values():
            condor.finish(h.id)
        await ce.get_output(list(sub.handles.values()), tmp_path)
        gc.collect()
        assert len(ce._collecting) == 0

    async def test_one_job_collected_twice_at_once(
        self, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        import asyncio

        transfers: list[str] = []  # "start" / "end", to see whether they overlap

        async def slow_transfer(
            argv: Sequence[str], env: Mapping[str, str], timeout: float
        ) -> CommandResult:
            if argv[0] != "condor_transfer_data":
                return await condor(argv, env, timeout)
            transfers.append("start")
            await asyncio.sleep(0.05)  # the second collection arrives meanwhile
            result = await condor(argv, env, timeout)
            transfers.append("end")
            return result

        provider = StaticCredentials([BearerToken(value=TOKEN)])
        backend = HTCondorCEBackend(
            settings(), provider, runner=slow_transfer, workdir=workdir
        )
        async with backend:
            h = (await backend.submit(SPEC)).handles[0]
            condor.finish(h.id)
            condor.outputs[h.id] = {"result.txt": b"42"}
            first, second = await asyncio.gather(
                backend.get_output([h], tmp_path / "a"),
                backend.get_output([h], tmp_path / "b"),
            )
        # Both succeed, one after the other: never two transfers into one directory.
        assert transfers == ["start", "end", "start", "end"]
        for outcome in (first[h], second[h]):
            assert outcome.ok and outcome.value is not None
            assert outcome.value.files["result.txt"].read_bytes() == b"42"

    async def test_a_new_backend_knows_the_jobs_of_another(
        self, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        # Everything needed lives in the handle and at the CE, not in the backend.
        async with make(condor, workdir) as first:
            h = (await first.submit(SPEC)).handles[0]
        condor.finish(h.id, ExitCode=0)
        async with make(condor, workdir) as second:
            assert (await second.get_status([h]))[h] == JobStatus.DONE
            assert (await second.get_output([h], tmp_path))[h].ok
            assert (await second.purge([h]))[h].ok
