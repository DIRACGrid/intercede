"""The HTCondor-CE backend: shape and construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from intercede import InterCEdeError, JobHandle, JobID, registry
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede.auth import BearerToken, StaticCredentials
from intercede.protocols import Diagnosable

from ._fake_condor import ENDPOINT, SPEC, TOKEN, FakeCondor, make, settings


class TestShape:
    def test_no_live_diagnostics(self, condor: FakeCondor, workdir: Path):
        assert not isinstance(make(condor, workdir), Diagnosable)

    def test_construction_does_no_io(self, condor: FakeCondor, workdir: Path):
        make(condor, workdir)
        assert not workdir.exists()
        assert condor.calls == []

    def test_built_by_the_registry(self):
        built = registry.get_backend(
            {"kind": "htcondor-ce", "endpoint": ENDPOINT}, StaticCredentials()
        )
        assert isinstance(built, HTCondorCEBackend)
        assert built.resource == ENDPOINT

    @pytest.mark.parametrize(
        ("endpoint", "pool"),
        [
            ("ce.example.org:9619", "ce.example.org:9619"),
            ("ce.example.org", "ce.example.org:9619"),
            ("ce:1234", "ce:1234"),
        ],
    )
    async def test_endpoint_becomes_pool_and_schedd_name(
        self, condor: FakeCondor, workdir: Path, endpoint: str, pool: str
    ):
        backend = HTCondorCEBackend(
            settings(endpoint=endpoint),
            StaticCredentials([BearerToken(value=TOKEN)]),
            runner=condor,
            workdir=workdir,
        )
        async with backend:
            await backend.get_status(
                [JobHandle(backend="htcondor-ce", resource=endpoint, id=JobID("1.0"))]
            )
        argv = condor.calls[0].argv
        assert argv[argv.index("-pool") + 1] == pool
        assert argv[argv.index("-name") + 1] == pool.split(":")[0]

    async def test_schedd_overrides_the_name_not_the_pool(
        self, condor: FakeCondor, workdir: Path
    ):
        async with make(condor, workdir, schedd="ce01@ce.example.org") as backend:
            await backend.get_status(
                [JobHandle(backend="htcondor-ce", resource=ENDPOINT, id=JobID("1.0"))]
            )
        argv = condor.calls[0].argv
        assert argv[argv.index("-pool") + 1] == ENDPOINT
        assert argv[argv.index("-name") + 1] == "ce01@ce.example.org"

    async def test_closed_backend_refuses(self, condor: FakeCondor, workdir: Path):
        backend = make(condor, workdir)
        async with backend:
            pass
        await backend.__aexit__(None, None, None)  # closing twice is harmless
        with pytest.raises(InterCEdeError, match="closed"):
            await backend.submit(SPEC)
