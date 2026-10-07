"""Fixtures for the unit suite.

``backend`` is parametrised over every backend the conformance suite runs
against. Only the in-memory fake exists for now; real backends join the list
in the integration suite (IC-ADR-002).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from intercede import JobBackend
from intercede._backends.fake import FakeBackend

BACKENDS: dict[str, Callable[[], JobBackend]] = {
    "fake": FakeBackend,
}


@pytest.fixture(params=sorted(BACKENDS))
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[JobBackend]:
    name = request.param

    backend_only_marker = request.node.get_closest_marker("backend_only")
    if (
        backend_only_marker is not None 
        and name not in backend_only_marker.args
    ):
        pytest.skip(f"only runs on {', '.join(backend_only_marker.args)}")

    xfail_marker = request.node.get_closest_marker("xfail_backend")
    if (
        xfail_marker is not None 
        and name in xfail_marker.args
    ):
        request.applymarker(
            pytest.mark.xfail(
                strict=True, 
                reason=f"known failure on {name}"
            )
        )

    async with BACKENDS[name]() as instance:
        yield instance
