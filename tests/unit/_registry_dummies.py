"""Stand-ins registered by test_registry.py.

Imported only through the registry, which is how the tests show that lookup is
lazy. Not collected by pytest: the file name does not start with ``test_``.
"""

from __future__ import annotations

from typing import Any


class _Lifecycle:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exc_info: object) -> None: ...


class DummyTransport(_Lifecycle):
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: Any) -> DummyTransport:
        return cls(settings)

    async def run(self, argv: Any) -> Any: ...

    async def put(self, local: Any, remote: Any) -> None: ...

    async def get(self, remote: Any, local: Any) -> None: ...


class DummyScheduler:
    stages_own_files = False

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: Any) -> DummyScheduler:
        return cls(settings)

    def submit_command(self, spec: Any, copies: Any, workdir: Any) -> Any: ...

    def parse_submit(self, raw: Any) -> Any: ...

    def status_command(self, ids: Any) -> Any: ...

    def parse_status(self, raw: Any, ids: Any) -> Any: ...

    def kill_command(self, ids: Any) -> Any: ...


class DummyBatchBackend(_Lifecycle):
    def __init__(self, settings: Any, transport: Any, scheduler: Any) -> None:
        self.settings = settings
        self.transport = transport
        self.scheduler = scheduler

    @classmethod
    def from_settings(
        cls, settings: Any, *, transport: Any, scheduler: Any
    ) -> DummyBatchBackend:
        return cls(settings, transport, scheduler)

    async def submit(self, spec: Any, copies: Any = 1) -> Any: ...

    async def get_status(self, handles: Any) -> Any: ...


class DummyBackend(_Lifecycle):
    """A single-class backend, standing in for the monolithic and provider families."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: Any) -> DummyBackend:
        return cls(settings)

    async def submit(self, spec: Any, copies: Any = 1) -> Any: ...

    async def get_status(self, handles: Any) -> Any: ...


class BuildsTheWrongThing:
    @classmethod
    def from_settings(cls, *args: Any, **kwargs: Any) -> object:
        return object()
