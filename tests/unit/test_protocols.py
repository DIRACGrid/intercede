"""``isinstance`` narrowing against the runtime-checkable protocols."""

from __future__ import annotations

import pytest

import intercede
from intercede import (
    Diagnosable,
    JobBackend,
    Killable,
    OutputRetriever,
    Purgeable,
    StatusReporter,
    Submitter,
)
from intercede._backends.batch.protocols import Scheduler, Transport
from intercede._backends.fake import FakeBackend

ALL = (
    Submitter,
    StatusReporter,
    JobBackend,
    OutputRetriever,
    Killable,
    Purgeable,
    Diagnosable,
)


class SubmitOnly:
    async def submit(self, spec, copies=1): ...


class StatusOnly:
    async def get_status(self, handles): ...


class Minimal(SubmitOnly, StatusOnly):
    """A backend with only the essential capabilities, like a cloud provider."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info): ...


class FakeTransport:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info): ...

    async def run(self, argv): ...

    async def put(self, local, remote): ...

    async def get(self, remote, local): ...


class FakeScheduler:
    stages_own_files = False

    def submit_command(self, spec, copies, workdir): ...

    def parse_submit(self, raw): ...

    def status_command(self, ids): ...

    def parse_status(self, raw, ids): ...

    def kill_command(self, ids): ...


@pytest.mark.parametrize("protocol", ALL)
def test_fake_backend_has_every_capability(protocol: type):
    assert isinstance(FakeBackend(), protocol)


def test_minimal_backend_has_only_the_essentials():
    backend = Minimal()
    assert isinstance(backend, JobBackend)
    assert not isinstance(backend, OutputRetriever | Killable | Purgeable | Diagnosable)


def test_narrow_protocols_stand_alone():
    assert isinstance(SubmitOnly(), Submitter)
    assert not isinstance(SubmitOnly(), StatusReporter | JobBackend)
    assert isinstance(StatusOnly(), StatusReporter)
    assert not isinstance(StatusOnly(), Submitter | JobBackend)


def test_a_backend_without_lifecycle_is_not_a_job_backend():
    class NoLifecycle(SubmitOnly, StatusOnly): ...

    assert not isinstance(NoLifecycle(), JobBackend)


def test_collaborator_protocols():
    assert isinstance(FakeTransport(), Transport)
    assert isinstance(FakeScheduler(), Scheduler)
    assert not isinstance(object(), Transport | Scheduler)


def test_public_api_is_exported():
    for name in intercede.__all__:
        assert hasattr(intercede, name), name
    assert "Transport" not in intercede.__all__
    assert "Scheduler" not in intercede.__all__
