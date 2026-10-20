"""The registry: closed tables, lazy imports, and a protocol check on everything it builds."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from intercede import (
    BackendConfig,
    ConfigurationError,
    JobBackend,
    RegistryError,
    SubmissionSpec,
    registry,
)
from intercede._backends.config import (
    ARCSettings,
    CloudSettings,
    FakeSettings,
    HTCondorCESettings,
)
from intercede._backends.fake import FakeBackend
from intercede.auth import CredentialSet, StaticCredentials
from intercede.registry import _build, _load

DUMMIES = "tests.unit._registry_dummies"
CREDENTIALS = StaticCredentials()

SSH_SLURM: dict[str, Any] = {
    "kind": "batch",
    "transport": "ssh",
    "scheduler": "slurm",
    "host": "batch.example.org",
    "user": "dirac",
    "known_hosts": "batch.example.org ssh-ed25519 AAAAC3Nz...",
}
LOCAL_SLURM: dict[str, Any] = {
    "kind": "batch",
    "transport": "local",
    "scheduler": "slurm",
}


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    """Each test sees the tables as it left them, not classes cached by another test."""
    _load.cache_clear()
    yield
    _load.cache_clear()


@pytest.fixture
def composed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register stand-ins for the composed backend, which has no real parts yet."""
    monkeypatch.setitem(registry._BACKENDS, "batch", f"{DUMMIES}:DummyBatchBackend")
    monkeypatch.setitem(registry._TRANSPORTS, "ssh", f"{DUMMIES}:DummyTransport")
    monkeypatch.setitem(registry._TRANSPORTS, "local", f"{DUMMIES}:DummyTransport")
    monkeypatch.setitem(registry._SCHEDULERS, "slurm", f"{DUMMIES}:DummyScheduler")


class TestResolution:
    async def test_builds_the_fake_unopened(self):
        built = _build(FakeSettings(resource="fake://test"), CREDENTIALS)
        assert isinstance(built, FakeBackend)
        async with built as backend:
            assert backend.resource == "fake://test"

    def test_composes_a_transport_and_a_scheduler(self, composed: None):
        built: Any = registry.backend(SSH_SLURM, CREDENTIALS)
        assert type(built).__name__ == "DummyBatchBackend"
        assert type(built.transport).__name__ == "DummyTransport"
        assert built.transport.settings.host == "batch.example.org"
        assert type(built.scheduler).__name__ == "DummyScheduler"
        assert built.settings.kind == "batch"

    def test_one_backend_class_serves_every_combination(self, composed: None):
        over_ssh = registry.backend(SSH_SLURM, CREDENTIALS)
        on_this_host = registry.backend(LOCAL_SLURM, CREDENTIALS)
        assert type(over_ssh) is type(on_this_host)

    def test_accepts_a_config_object(self, composed: None):
        assert isinstance(
            registry.backend(BackendConfig.model_validate(LOCAL_SLURM), CREDENTIALS),
            JobBackend,
        )


class TestRefused:
    def test_invalid_configuration(self):
        with pytest.raises(ConfigurationError, match="kind"):
            registry.backend({"kind": "lsf"}, CREDENTIALS)

    @pytest.mark.parametrize(
        "config",
        [
            {"kind": "arc", "endpoint": "https://arc.example.org:443"},
            {
                "kind": "cloud",
                "endpoint": "https://cloud.example.org:5000",
                "driver": "openstack",
                "vm_image": "name:alma9",
                "vm_flavor": "name:m1.large",
            },
        ],
    )
    def test_valid_but_not_shipped_yet(self, config: dict[str, Any]):
        with pytest.raises(
            RegistryError, match=f"backend '{config['kind']}' is not available"
        ):
            registry.backend(config, CREDENTIALS)

    def test_composed_part_not_shipped_yet(self):
        with pytest.raises(RegistryError, match="transport 'ssh' is not available"):
            registry.backend(SSH_SLURM, CREDENTIALS)

    @pytest.mark.parametrize(
        ("target", "message"),
        [
            ("tests.unit.does_not_exist:Transport", "cannot be imported"),
            (f"{DUMMIES}:Missing", "cannot be imported"),
            (f"{DUMMIES}:NoFromSettings", "no from_settings"),
            (f"{DUMMIES}:BuildsTheWrongThing", "which is not a Transport"),
        ],
    )
    def test_broken_entry_fails_at_resolution(
        self, composed: None, monkeypatch: pytest.MonkeyPatch, target: str, message: str
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", target)
        with pytest.raises(RegistryError, match=message):
            registry.backend(SSH_SLURM, CREDENTIALS)

    def test_backend_that_breaks_the_contract(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(
            registry._BACKENDS, "fake", f"{DUMMIES}:BuildsTheWrongThing"
        )
        with pytest.raises(RegistryError, match="which is not a JobBackend"):
            _build(FakeSettings(), CREDENTIALS)


class TestAvailable:
    def test_lists_only_what_a_configuration_can_ask_for(self):
        listing = registry.available()
        assert set(listing) == {"backend", "transport", "scheduler"}
        assert "fake" not in listing["backend"]  # internal, not a configurable kind

    def test_grows_with_the_tables(self, composed: None):
        listing = registry.available()
        assert listing["backend"] == ("batch",)
        assert listing["transport"] == ("local", "ssh")
        assert listing["scheduler"] == ("slurm",)


def test_importing_intercede_imports_no_backend():
    code = (
        "import sys, intercede, intercede.registry;"
        "loaded = [m for m in sys.modules if m.startswith(('intercede._backends.fake', 'tests.'))];"
        "assert not loaded, loaded"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603  (constant input)


def test_only_the_requested_entry_is_imported(
    composed: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delitem(sys.modules, DUMMIES, raising=False)
    registry.available()
    assert DUMMIES not in sys.modules  # listing imports nothing
    registry.backend(LOCAL_SLURM, CREDENTIALS)
    assert DUMMIES in sys.modules


# --------------------------------------------------------------------------
# Every logical path through the registry
# --------------------------------------------------------------------------


class TestInput:
    @pytest.mark.parametrize("config", [None, "arc", 42, ["kind", "arc"]])
    def test_non_mapping_is_a_configuration_error(self, config: Any):
        with pytest.raises(ConfigurationError):
            registry.backend(config, CREDENTIALS)

    def test_configuration_error_keeps_the_validation_details(self):
        with pytest.raises(ConfigurationError) as exc_info:
            registry.backend({"kind": "arc"}, CREDENTIALS)
        assert "endpoint: required for kind='arc'" in str(exc_info.value)
        assert exc_info.value.__cause__ is not None

    def test_errors_share_the_library_root(self):
        from intercede import InterCEdeError

        assert issubclass(ConfigurationError, InterCEdeError)
        assert issubclass(RegistryError, InterCEdeError)

    def test_mapping_and_config_object_build_the_same(self, composed: None):
        from_mapping: Any = registry.backend(LOCAL_SLURM, CREDENTIALS)
        from_object: Any = registry.backend(
            BackendConfig.model_validate(LOCAL_SLURM), CREDENTIALS
        )
        assert type(from_mapping) is type(from_object)
        assert from_mapping.settings == from_object.settings


class TestComposition:
    def test_transport_gets_only_its_own_settings(self, composed: None):
        built: Any = registry.backend(SSH_SLURM, CREDENTIALS)
        assert built.transport.settings.kind == "ssh"
        assert not hasattr(built.transport.settings, "scheduler")

    def test_scheduler_gets_the_batch_settings(self, composed: None):
        built: Any = registry.backend({**LOCAL_SLURM, "queue": "short"}, CREDENTIALS)
        assert built.scheduler.settings.kind == "batch"
        assert built.scheduler.settings.queue == "short"

    def test_each_backend_gets_fresh_parts(self, composed: None):
        first: Any = registry.backend(LOCAL_SLURM, CREDENTIALS)
        second: Any = registry.backend(LOCAL_SLURM, CREDENTIALS)
        assert first is not second
        assert first.transport is not second.transport

    def test_missing_scheduler_is_reported(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delitem(registry._SCHEDULERS, "slurm")
        with pytest.raises(RegistryError, match="scheduler 'slurm' is not available"):
            registry.backend(LOCAL_SLURM, CREDENTIALS)

    def test_missing_batch_backend_is_reported(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delitem(registry._BACKENDS, "batch")
        with pytest.raises(RegistryError, match="backend 'batch' is not available"):
            registry.backend(LOCAL_SLURM, CREDENTIALS)

    def test_scheduler_that_breaks_its_protocol(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(
            registry._SCHEDULERS, "slurm", f"{DUMMIES}:BuildsTheWrongThing"
        )
        with pytest.raises(RegistryError, match="which is not a Scheduler"):
            registry.backend(LOCAL_SLURM, CREDENTIALS)

    def test_batch_backend_that_breaks_its_protocol(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(
            registry._BACKENDS, "batch", f"{DUMMIES}:BuildsTheWrongThing"
        )
        with pytest.raises(
            RegistryError,
            match="backend 'batch' built a object, which is not a JobBackend",
        ):
            registry.backend(LOCAL_SLURM, CREDENTIALS)

    def test_transport_is_checked_before_the_scheduler_is_loaded(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(
            registry._TRANSPORTS, "local", f"{DUMMIES}:BuildsTheWrongThing"
        )
        monkeypatch.delitem(registry._SCHEDULERS, "slurm")
        with pytest.raises(RegistryError, match="transport 'local'"):
            registry.backend(LOCAL_SLURM, CREDENTIALS)


class TestBrokenEntries:
    @pytest.mark.parametrize(
        "target",
        [
            "tests.unit._registry_dummies",  # no ":Class" part
            "tests.unit._registry_dummies:",  # empty class name
            ":DummyTransport",  # empty module name
            "builtins:not_a_thing",
        ],
    )
    def test_malformed_target(
        self, composed: None, monkeypatch: pytest.MonkeyPatch, target: str
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", target)
        with pytest.raises(RegistryError, match="cannot be imported"):
            registry.backend(SSH_SLURM, CREDENTIALS)

    def test_class_without_from_settings(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "builtins:object")
        with pytest.raises(RegistryError, match="has no from_settings"):
            registry.backend(SSH_SLURM, CREDENTIALS)

    def test_import_failure_is_chained(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "tests.unit.does_not_exist:X")
        with pytest.raises(RegistryError) as exc_info:
            registry.backend(SSH_SLURM, CREDENTIALS)
        assert isinstance(exc_info.value.__cause__, ImportError)

    def test_error_names_the_entry(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "tests.unit.does_not_exist:X")
        with pytest.raises(RegistryError, match="'tests.unit.does_not_exist:X'"):
            registry.backend(SSH_SLURM, CREDENTIALS)


class TestLoad:
    def test_imports_each_entry_once(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[str] = []
        real = registry.import_module

        def counting(name: str) -> Any:
            calls.append(name)
            return real(name)

        monkeypatch.setattr(registry, "import_module", counting)
        for _ in range(3):
            registry.backend(LOCAL_SLURM, CREDENTIALS)
        assert (
            calls.count(DUMMIES) == 3
        )  # transport, scheduler, batch: each loaded once

    def test_returns_the_registered_class(self, composed: None):
        from tests.unit._registry_dummies import DummyTransport

        assert _load("transport", "ssh") is DummyTransport

    def test_unknown_group_entry_lists_none_yet(self):
        with pytest.raises(RegistryError, match=r"\(available: none yet\)"):
            _load("transport", "ssh")

    def test_unavailable_entry_lists_what_is(self, composed: None):
        with pytest.raises(RegistryError, match=r"\(available: local, ssh\)"):
            _load("transport", "carrier-pigeon")

    def test_failures_are_not_cached(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "tests.unit.does_not_exist:X")
        with pytest.raises(RegistryError):
            _load("transport", "ssh")
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", f"{DUMMIES}:DummyTransport")
        assert _load("transport", "ssh").__name__ == "DummyTransport"


class TestAvailableDetails:
    def test_names_are_sorted(self, monkeypatch: pytest.MonkeyPatch):
        for name in ("ssh", "local"):
            monkeypatch.setitem(registry._TRANSPORTS, name, f"{DUMMIES}:DummyTransport")
        assert registry.available()["transport"] == ("local", "ssh")

    def test_entries_no_configuration_can_ask_for_are_hidden(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._SCHEDULERS, "lsf", f"{DUMMIES}:DummyScheduler")
        assert "lsf" not in registry.available()["scheduler"]

    def test_imports_nothing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "tests.unit.does_not_exist:X")
        assert registry.available()["transport"] == ("ssh",)  # listed, never imported

    def test_shipped_tables_today(self):
        assert registry.available() == {"backend": (), "transport": (), "scheduler": ()}


class TestChoices:
    def test_literal(self):
        from typing import Literal

        assert registry._choices(Literal["a", "b"]) == {"a", "b"}

    def test_optional_literal(self):
        from typing import Literal, Optional

        assert registry._choices(Optional[Literal["a"]]) == {"a"}  # noqa: UP045

    def test_union_of_literals(self):
        from typing import Literal

        assert registry._choices(Literal["a"] | Literal["b"] | None) == {"a", "b"}

    def test_non_literal(self):
        assert registry._choices(str) == set()
        assert registry._choices(int | None) == set()


async def test_backend_from_the_registry_is_usable(composed: None):
    async with registry.backend(LOCAL_SLURM, CREDENTIALS) as backend:
        assert isinstance(backend, JobBackend)


async def test_fake_through_the_registry_runs_a_job():
    from intercede import SubmissionSpec

    async with _build(FakeSettings(), CREDENTIALS) as backend:
        submission = await backend.submit(SubmissionSpec(executable="/bin/true"), 2)
        statuses = await backend.get_status(list(submission.handles.values()))
    assert len(statuses) == 2


# --------------------------------------------------------------------------
# Every family resolves to its own class, and calls reach that class
# --------------------------------------------------------------------------

SINGLE_CLASS = {
    "arc": (
        {"kind": "arc", "endpoint": "https://arc.example.org:443"},
        ARCSettings,
    ),
    "htcondor-ce": (
        {"kind": "htcondor-ce", "endpoint": "ce.example.org:9619"},
        HTCondorCESettings,
    ),
    "cloud": (
        {
            "kind": "cloud",
            "endpoint": "https://cloud.example.org:5000",
            "driver": "openstack",
            "vm_image": "name:alma9",
            "vm_flavor": "name:m1.large",
        },
        CloudSettings,
    ),
}


@pytest.mark.parametrize("kind", sorted(SINGLE_CLASS))
class TestSingleClassFamilies:
    """Monolithic ("arc", "htcondor-ce") and provider ("cloud") backends."""

    @pytest.fixture(autouse=True)
    def registered(self, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
        monkeypatch.setitem(registry._BACKENDS, kind, f"{DUMMIES}:DummyBackend")

    def test_built_from_its_own_settings(self, kind: str):
        config, settings_type = SINGLE_CLASS[kind]
        built: Any = registry.backend(config, CREDENTIALS)
        assert type(built).__name__ == "DummyBackend"
        assert isinstance(built.settings, settings_type)
        assert built.settings.kind == kind

    def test_needs_no_transport_or_scheduler(self, kind: str):
        # Only the backend table is filled; the other two stay empty.
        assert registry._TRANSPORTS == {}
        assert registry._SCHEDULERS == {}
        assert registry.backend(SINGLE_CLASS[kind][0], CREDENTIALS) is not None

    def test_is_listed_once_registered(self, kind: str):
        assert kind in registry.available()["backend"]

    async def test_calls_reach_the_registered_class(self, kind: str):
        built: Any = registry.backend(SINGLE_CLASS[kind][0], CREDENTIALS)
        backend: JobBackend = built  # what the consumer holds: only the protocol
        async with backend as ce:
            result = await ce.submit(SubmissionSpec(executable="x"))
            await ce.get_status([])
        assert result == f"submitted by {kind}"
        assert built.calls == ["submit", "get_status"]


async def test_composed_backend_calls_reach_the_registered_class(composed: None):
    built: Any = registry.backend(LOCAL_SLURM, CREDENTIALS)
    backend: JobBackend = built
    async with backend as ce:
        await ce.get_status([])
    assert type(built).__name__ == "DummyBatchBackend"


async def test_each_kind_reaches_its_own_class(monkeypatch: pytest.MonkeyPatch):
    """Two kinds registered to two classes: each configuration gets its own."""
    monkeypatch.setitem(registry._BACKENDS, "cloud", f"{DUMMIES}:DummyBackend")
    cloud: Any = registry.backend(SINGLE_CLASS["cloud"][0], CREDENTIALS)
    fake = _build(FakeSettings(), CREDENTIALS)
    assert type(cloud).__name__ == "DummyBackend"
    assert isinstance(fake, FakeBackend)
    async with cloud as ce:
        await ce.submit(SubmissionSpec(executable="x"))
    async with fake as ce:
        await ce.submit(SubmissionSpec(executable="x"))
    assert cloud.calls == ["submit"]  # the fake's submit never touched the dummy


# --------------------------------------------------------------------------
# The credential provider
# --------------------------------------------------------------------------


class TestCredentials:
    @pytest.mark.parametrize(
        "credentials",
        [None, "token", object(), CredentialSet()],
        ids=["none", "str", "object", "set"],
    )
    def test_must_be_a_provider(self, composed: None, credentials: Any):
        with pytest.raises(ConfigurationError, match="must be a CredentialProvider"):
            registry.backend(LOCAL_SLURM, credentials)

    def test_configuration_is_checked_first(self):
        # A broken configuration is reported even with a bad provider.
        with pytest.raises(ConfigurationError, match="kind"):
            registry.backend({"kind": "lsf"}, None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("kind", sorted(SINGLE_CLASS))
    def test_single_class_backend_receives_the_provider(
        self, monkeypatch: pytest.MonkeyPatch, kind: str
    ):
        monkeypatch.setitem(registry._BACKENDS, kind, f"{DUMMIES}:DummyBackend")
        provider = StaticCredentials()
        built: Any = registry.backend(SINGLE_CLASS[kind][0], provider)
        assert built.credentials is provider

    def test_transport_receives_the_provider(self, composed: None):
        provider = StaticCredentials()
        built: Any = registry.backend(SSH_SLURM, provider)
        assert built.transport.credentials is provider

    def test_the_same_provider_is_shared(self, composed: None):
        provider = StaticCredentials()
        first: Any = registry.backend(SSH_SLURM, provider)
        second: Any = registry.backend(LOCAL_SLURM, provider)
        assert first.transport.credentials is second.transport.credentials is provider

    async def test_building_never_asks_the_provider(self, composed: None):
        class Watching(StaticCredentials):
            asked = 0

            async def get(self, requirements: Any) -> CredentialSet:
                Watching.asked += 1
                return await super().get(requirements)

        registry.backend(SSH_SLURM, Watching())
        _build(FakeSettings(), Watching())
        assert Watching.asked == 0  # construction does no I/O

    def test_the_fake_ignores_its_provider(self):
        assert isinstance(_build(FakeSettings(), StaticCredentials()), FakeBackend)
