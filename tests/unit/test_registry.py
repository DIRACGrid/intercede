"""The registry: closed tables, lazy imports, and a protocol check on everything it builds."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from intercede import (
    BatchSSHConfig,
    CloudConfig,
    ConfigurationError,
    JobBackend,
    RegistryError,
    registry,
)
from intercede._backends.config import FakeConfig
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
        async with _build(FakeConfig(resource="fake://test"), CREDENTIALS) as backend:
            assert isinstance(backend, JobBackend)
            assert backend.resource == "fake://test"

    @pytest.mark.parametrize(
        "config",
        [CLOUD, CloudConfig(**CLOUD)],
        ids=["mapping", "object"],
    )
    def test_a_single_class_backend_gets_its_settings_and_provider(
        self, monkeypatch: pytest.MonkeyPatch, config: Any
    ):
        monkeypatch.setitem(registry._BACKENDS, "cloud", f"{DUMMIES}:DummyBackend")
        provider = StaticCredentials()
        built: Any = registry.get_backend(config, provider)
        assert type(built).__name__ == "DummyBackend"
        assert isinstance(built.settings, CloudConfig)
        assert built.credentials is provider

    def test_a_batch_backend_is_composed_of_its_parts(self, composed: None):
        provider = StaticCredentials()
        built: Any = registry.get_backend({**SSH_SLURM, "queue": "short"}, provider)
        assert type(built).__name__ == "DummyBatchBackend"
        # Transport and scheduler each get the whole batch configuration; the
        # transport, which authenticates, also gets the provider.
        assert isinstance(built.transport.settings, BatchSSHConfig)
        assert built.transport.settings.host == "batch.example.org"
        assert built.transport.credentials is provider
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

        registry.get_backend(SSH_SLURM, Watching())
        registry.get_backend(CLOUD, Watching())
        assert Watching.asked == 0  # construction does no I/O


class TestRefused:
    def test_invalid_configuration_keeps_the_details(self):
        with pytest.raises(ConfigurationError) as exc_info:
            registry.get_backend({"kind": "arc"}, CREDENTIALS)
        assert "endpoint: required for kind='arc'" in str(exc_info.value)

    @pytest.mark.parametrize(
        "credentials", [None, CredentialSet()], ids=["none", "set-not-provider"]
    )
    def test_credentials_must_be_a_provider(self, composed: None, credentials: Any):
        with pytest.raises(ConfigurationError, match="must be a CredentialProvider"):
            registry.get_backend(LOCAL_SLURM, credentials)

    @pytest.mark.parametrize(
        "config",
        [{"kind": "arc", "endpoint": "https://arc.example.org:443"}, CLOUD],
        ids=["arc", "cloud"],
    )
    def test_valid_but_not_shipped_yet(self, config: dict[str, Any]):
        with pytest.raises(
            RegistryError, match=f"backend '{config['kind']}' is not available"
        ):
            registry.get_backend(config, CREDENTIALS)

    def test_composed_part_not_shipped_yet(self):
        with pytest.raises(RegistryError, match="transport 'ssh' is not available"):
            registry.get_backend(SSH_SLURM, CREDENTIALS)

    def test_an_entry_that_cannot_be_imported(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._TRANSPORTS, "ssh", "tests.unit.does_not_exist:X")
        with pytest.raises(RegistryError, match="cannot be imported") as exc_info:
            registry.get_backend(SSH_SLURM, CREDENTIALS)
        assert isinstance(exc_info.value.__cause__, ImportError)

    @pytest.mark.parametrize(
        ("table", "name", "protocol"),
        [
            ("_TRANSPORTS", "local", "Transport"),
            ("_SCHEDULERS", "slurm", "Scheduler"),
            ("_BACKENDS", "batch", "JobBackend"),
        ],
    )
    def test_what_breaks_its_protocol_is_refused(
        self,
        composed: None,
        monkeypatch: pytest.MonkeyPatch,
        table: str,
        name: str,
        protocol: str,
    ):
        monkeypatch.setitem(
            getattr(registry, table), name, f"{DUMMIES}:BuildsTheWrongThing"
        )
        with pytest.raises(RegistryError, match=f"which is not a {protocol}"):
            registry.get_backend(LOCAL_SLURM, CREDENTIALS)


class TestAvailable:
    def test_shipped_today(self):
        assert registry.available() == {"backend": (), "transport": (), "scheduler": ()}

    def test_lists_only_what_a_configuration_can_ask_for(
        self, composed: None, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(registry._SCHEDULERS, "lsf", f"{DUMMIES}:DummyScheduler")
        listing = registry.available()
        assert listing["transport"] == ("local", "ssh")
        assert "lsf" not in listing["scheduler"]
        assert "fake" not in listing["backend"]


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
        registry.get_backend(LOCAL_SLURM, CREDENTIALS)
        assert DUMMIES in sys.modules
