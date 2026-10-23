"""Which capabilities a backend has, as a consumer finds out with ``isinstance``."""

from __future__ import annotations

import pytest

import intercede
from intercede import (
    Diagnosable,
    JobBackend,
    Killable,
    OutputRetriever,
    Purgeable,
)
from intercede._backends.fake import FakeBackend


class Minimal:
    """A backend with only the essential capabilities, like a cloud provider."""

    async def submit(self, spec, copies=1): ...

    async def get_status(self, handles): ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info): ...


@pytest.mark.parametrize(
    "capability", [JobBackend, OutputRetriever, Killable, Purgeable, Diagnosable]
)
def test_the_fake_has_every_capability(capability: type):
    assert isinstance(FakeBackend(), capability)


def test_a_minimal_backend_has_only_the_essentials():
    assert isinstance(Minimal(), JobBackend)
    assert not isinstance(
        Minimal(), OutputRetriever | Killable | Purgeable | Diagnosable
    )


def test_a_backend_needs_a_lifecycle():
    class NoLifecycle:
        async def submit(self, spec, copies=1): ...

        async def get_status(self, handles): ...

    assert not isinstance(NoLifecycle(), JobBackend)


def test_public_api_is_exported():
    for name in intercede.__all__:
        assert hasattr(intercede, name), name
    assert "Transport" not in intercede.__all__  # internal to the batch family
