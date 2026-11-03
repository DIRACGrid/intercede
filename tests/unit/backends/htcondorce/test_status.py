"""The HTCondor-CE backend: status."""

from __future__ import annotations

import pytest

from intercede import JobStatus
from intercede._backends.monolith.htcondorce import HTCondorCEBackend

from ._fake_condor import FakeCondor, handle


class TestStatus:
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

    async def test_large_polls_are_chunked(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        await ce.get_status([handle(f"1.{i}") for i in range(250)])
        assert condor.tools() == ["condor_q"] * 3 + ["condor_history"] * 3
