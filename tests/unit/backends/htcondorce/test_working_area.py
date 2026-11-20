"""The HTCondor-CE backend: the working area, and keeping nothing between calls."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from intercede import ConfigurationError
from intercede._backends.monolith.htcondorce import HTCondorCEBackend

from ._fake_condor import SPEC, FakeCondor, make


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
