"""Fixtures for the HTCondor-CE backend tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._fake_condor import FakeCondor, make


@pytest.fixture
def condor() -> FakeCondor:
    return FakeCondor()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path / "work"


@pytest.fixture
async def ce(condor: FakeCondor, workdir: Path):
    async with make(condor, workdir) as backend:
        yield backend
