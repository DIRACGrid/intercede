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
    registry,
)
from intercede._backends.config import CloudSettings, FakeSettings
from intercede.auth import CredentialSet, StaticCredentials
from intercede.registry import _build, _load

DUMMIES = "tests.unit._registry_dummies"
CREDENTIALS = StaticCredentials()

CLOUD: dict[str, Any] = {
    "kind": "cloud",
    "endpoint": "https://cloud.example.org:5000",
    "driver": "openstack",
    "vm_image": "name:alma9",
    "vm_flavor": "name:m1.large",
}
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


class TestBuilds:
    async def test_the_fake_for_the_test_suites(self):
        async with _build(FakeSettings(resource="fake://test"), CREDENTIALS) as backend:
            assert isinstance(backend, JobBackend)
            assert backend.resource == "fake://test"

    @pytest.mark.parametrize(
        "config",
        [CLOUD, BackendConfig.model_validate(CLOUD)],
        ids=["mapping", "object"],
    )
    def test_a_single_class_backend_gets_its_settings_and_provider(
        self, monkeypatch: pytest.MonkeyPatch, config: Any
    ):
        monkeypatch.setitem(registry._BACKENDS, "cloud", f"{DUMMIES}:DummyBackend")
        provider = StaticCredentials()
        built: Any = registry.backend(config, provider)
        assert isinstance(built.settings, CloudSettings)
        assert built.credentials is provider

    def test_a_batch_backend_is_composed_of_its_parts(self, composed: None):
        provider = StaticCredentials()
        built: Any = registry.backend({**SSH_SLURM, "queue": "short"}, provider)
        assert type(built).__name__ == "DummyBatchBackend"
        # The transport gets only its own settings, and the credentials.
        assert built.transport.settings.kind == "ssh"
        assert built.transport.settings.host == "batch.example.org"
        assert built.transport.credentials is provider
        # The scheduler gets the batch settings.
        assert built.scheduler.settings.queue == "short"

    async def test_building_asks_the_provider_nothing(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._BACKENDS, "cloud", f"{DUMMIES}:DummyBackend")

        class Watching(StaticCredentials):
            asked = 0

            async def get(self, requirements: Any) -> CredentialSet:
                Watching.asked += 1
                return await super().get(requirements)

        registry.backend(SSH_SLURM, Watching())
        registry.backend(CLOUD, Watching())
        assert Watching.asked == 0  # construction does no I/O


class TestRefused:
    def test_invalid_configuration_keeps_the_details(self):
        with pytest.raises(ConfigurationError) as exc_info:
            registry.backend({"kind": "arc"}, CREDENTIALS)
        assert "endpoint: required for kind='arc'" in str(exc_info.value)

    @pytest.mark.parametrize(
        "credentials", [None, CredentialSet()], ids=["none", "set-not-provider"]
    )
    def test_credentials_must_be_a_provider(self, composed: None, credentials: Any):
        with pytest.raises(ConfigurationError, match="must be a CredentialProvider"):
            registry.backend(LOCAL_SLURM, credentials)

    @pytest.mark.parametrize(
        "config",
        [{"kind": "arc", "endpoint": "https://arc.example.org:443"}, CLOUD],
        ids=["arc", "cloud"],
    )
    def test_valid_but_not_shipped_yet(self, config: dict[str, Any]):
        with pytest.raises(
            RegistryError,
            match=f"backend '{config['kind']}' is not available",
        ):
            registry.backend(config, CREDENTIALS)

    def test_composed_part_not_shipped_yet(self):
        with pytest.raises(RegistryError, match="transport 'ssh' is not available"):
            registry.backend(SSH_SLURM, CREDENTIALS)

    def test_an_entry_that_cannot_be_imported(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "tests.unit.does_not_exist:X")
        with pytest.raises(RegistryError, match="cannot be imported") as exc_info:
            registry.backend(SSH_SLURM, CREDENTIALS)
        assert isinstance(exc_info.value.__cause__, ImportError)

    @pytest.mark.parametrize(
        ("table", "name", "config", "protocol"),
        [
            ("_TRANSPORTS", "local", LOCAL_SLURM, "Transport"),
            ("_SCHEDULERS", "slurm", LOCAL_SLURM, "Scheduler"),
            ("_BACKENDS", "batch", LOCAL_SLURM, "JobBackend"),
        ],
    )
    def test_what_breaks_its_protocol_is_refused(
        self,
        composed: None,
        monkeypatch: pytest.MonkeyPatch,
        table: str,
        name: str,
        config: dict[str, Any],
        protocol: str,
    ):
        monkeypatch.setitem(
            getattr(registry, table), name, f"{DUMMIES}:BuildsTheWrongThing"
        )
        with pytest.raises(RegistryError, match=f"which is not a {protocol}"):
            registry.backend(config, CREDENTIALS)


class TestAvailable:
    def test_shipped_today(self):
        assert registry.available() == {"backend": (), "transport": (), "scheduler": ()}

    def test_lists_only_what_a_configuration_can_ask_for(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._SCHEDULERS, "lsf", f"{DUMMIES}:DummyScheduler")
        assert "lsf" not in registry.available()["scheduler"]
        assert "fake" not in registry.available()["backend"]


class TestLazyImports:
    def test_importing_intercede_imports_no_backend(self):
        code = (
            "import sys, intercede, intercede.registry;"
            "loaded = [m for m in sys.modules if m.startswith(('intercede._backends.fake', 'tests.'))];"
            "assert not loaded, loaded"
        )
        subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603  (constant input)

    def test_only_the_requested_entry_is_imported(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delitem(sys.modules, DUMMIES, raising=False)
        registry.available()
        assert DUMMIES not in sys.modules  # listing imports nothing
        registry.backend(LOCAL_SLURM, CREDENTIALS)
        assert DUMMIES in sys.modules
