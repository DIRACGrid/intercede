"""The HTCondor-CE backend: kill and purge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend

from ._fake_condor import SPEC, FakeCondor, handle


class TestKill:
    async def test_a_job_the_ce_no_longer_knows(self, ce: HTCondorCEBackend):
        gone = handle("9.0")
        assert (await ce.kill([gone]))[gone].reason == "unknown job"
        assert (await ce.purge([gone]))[gone].reason == "unknown job"

    @pytest.mark.parametrize(
        "fields",
        [{"code": 4, "ExitCode": 0}, {"code": 4, "ExitCode": 3}],
        ids=["done", "failed"],
    )
    async def test_leaves_finished_jobs_and_their_output(
        self, ce: HTCondorCEBackend, condor: FakeCondor, fields: dict[str, Any]
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, **fields)
        condor.calls.clear()
        outcome = (await ce.kill([h]))[h]
        assert outcome.ok and outcome.reason == "already finished"
        assert "condor_rm" not in condor.tools()

    async def test_condor_rm_refusing_one_job(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        sub = await ce.submit(SPEC, 2)
        real = condor._remove

        def refuse_second(ids: list[str]) -> CommandResult:
            result = real(ids[:1])
            return CommandResult(
                returncode=1,
                stdout=result.stdout,
                stderr="Couldn't find/remove all jobs\n",
            )

        condor._remove = refuse_second  # type: ignore[method-assign]
        outcomes = await ce.kill(list(sub.handles.values()))
        assert outcomes[sub.handles[0]].ok
        assert not outcomes[sub.handles[1]].ok
        assert (
            outcomes[sub.handles[1]].reason
            == "condor_rm failed: Couldn't find/remove all jobs"
        )


class TestPurge:
    async def test_a_job_removed_but_still_queued_is_already_released(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        # condor_rm returns before the schedd drops the job from the queue.
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, code=3, LastJobStatus=4)
        condor.calls.clear()
        output = (await ce.get_output([h], tmp_path))[h]
        assert (
            not output.ok and output.reason == "the job was purged; its output is gone"
        )
        again = (await ce.purge([h]))[h]
        assert again.ok and again.reason == "already released"
        assert "condor_transfer_data" not in condor.tools()
        assert "condor_rm" not in condor.tools()

    async def test_twice_is_harmless(self, ce: HTCondorCEBackend):
        h = (await ce.submit(SPEC)).handles[0]
        await ce.purge([h])
        again = (await ce.purge([h]))[h]
        assert again.ok and again.reason == "already released"

    async def test_purges_active_jobs_too(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        h = (await ce.submit(SPEC)).handles[0]
        condor.finish(h.id, code=2)
        assert (await ce.purge([h]))[h].ok
        assert h.id not in condor.queue
