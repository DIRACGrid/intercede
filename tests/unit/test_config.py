"""Backend configuration: one flat model, checked against the backend it names."""

from __future__ import annotations

import tomllib
from typing import Any

import pytest
from pydantic import ValidationError

from intercede import BackendConfig
from intercede._backends.config import (
    ARCSettings,
    BatchSettings,
    HTCondorCESettings,
    LocalTransportSettings,
    SSHTransportSettings,
    resolve,
)

SSH: dict[str, Any] = {
    "kind": "batch",
    "transport": "ssh",
    "scheduler": "slurm",
    "host": "batch.example.org",
    "user": "dirac",
    "known_hosts": "batch.example.org ssh-ed25519 AAAAC3Nz...",
}


def rejected(**data: Any) -> str:
    """Return the error message for configuration that must be refused."""
    with pytest.raises(ValidationError) as exc_info:
        BackendConfig.model_validate(data)
    return str(exc_info.value)


class TestResolve:
    """The flat model becomes the settings of exactly one backend."""

    def test_arc(self):
        settings = resolve(
            BackendConfig(kind="arc", endpoint="https://arc.example.org:443")
        )
        assert isinstance(settings, ARCSettings)
        assert settings.auth == {"token"}

    def test_htcondor_ce_with_proxy(self):
        config = BackendConfig(
            kind="htcondor-ce", endpoint="ce:9619", auth=frozenset({"token", "proxy"})
        )
        settings = resolve(config)
        assert isinstance(settings, HTCondorCESettings)
        assert settings.auth == {"token", "proxy"}

    def test_batch_over_ssh(self):
        settings = resolve(BackendConfig.model_validate(SSH))
        assert isinstance(settings, BatchSettings)
        assert settings.scheduler == "slurm"
        assert isinstance(settings.transport, SSHTransportSettings)
        assert settings.transport.host == "batch.example.org"
        assert settings.transport.port == 22
        assert settings.transport.auth == "key"

    def test_batch_on_this_host(self):
        settings = resolve(
            BackendConfig(kind="batch", transport="local", scheduler="direct")
        )
        assert isinstance(settings, BatchSettings)
        assert isinstance(settings.transport, LocalTransportSettings)

    def test_ssh_auth_mode(self):
        settings = resolve(BackendConfig.model_validate({**SSH, "auth": ["agent"]}))
        assert isinstance(settings, BatchSettings)
        assert isinstance(settings.transport, SSHTransportSettings)
        assert settings.transport.auth == "agent"

    def test_limits(self):
        settings = resolve(
            BackendConfig(kind="arc", endpoint="https://a:443", max_output_files=500)
        )
        assert settings.limits.max_output_files == 500
        assert settings.limits.transfer_timeout_s == 3600


def test_loads_from_toml():
    data = tomllib.loads(
        """
        kind = "batch"
        transport = "ssh"
        scheduler = "slurm"
        host = "batch.example.org"
        user = "dirac"
        known_hosts = "batch.example.org ssh-ed25519 AAAAC3Nz..."
        queue = "short"
        """
    )
    assert BackendConfig.model_validate(data).queue == "short"


def test_round_trips_through_json():
    config = BackendConfig.model_validate(SSH)
    assert BackendConfig.model_validate_json(config.model_dump_json()) == config


class TestRejected:
    def test_unknown_kind(self):
        assert "kind" in rejected(kind="lsf")

    def test_unknown_scheduler(self):
        assert "scheduler" in rejected(**{**SSH, "scheduler": "pbs"})

    def test_typo_is_not_silently_ignored(self):
        assert "endpont" in rejected(kind="arc", endpoint="https://a:443", endpont="x")

    @pytest.mark.parametrize(
        ("data", "field"),
        [
            ({"kind": "arc", "endpoint": "https://a:443", "host": "h"}, "host"),
            (
                {"kind": "arc", "endpoint": "https://a:443", "scheduler": "slurm"},
                "scheduler",
            ),
            ({**SSH, "endpoint": "https://a:443"}, "endpoint"),
            (
                {
                    "kind": "batch",
                    "transport": "local",
                    "scheduler": "slurm",
                    "host": "h",
                },
                "host",
            ),
        ],
    )
    def test_fields_that_do_not_apply_are_refused(
        self, data: dict[str, Any], field: str
    ):
        assert f"{field}: does not apply to" in rejected(**data)

    @pytest.mark.parametrize(
        ("data", "field"),
        [
            ({"kind": "arc"}, "endpoint"),
            ({"kind": "batch", "scheduler": "slurm"}, "transport"),
            ({"kind": "batch", "transport": "local"}, "scheduler"),
            ({**SSH, "user": None}, "user"),
            ({**SSH, "known_hosts": None}, "known_hosts"),
        ],
    )
    def test_missing_fields_are_named(self, data: dict[str, Any], field: str):
        assert f"{field}: required for" in rejected(**data)

    def test_ssh_requires_host_key_verification(self):
        assert "known_hosts" in rejected(**{**SSH, "known_hosts": "  "})

    @pytest.mark.parametrize("auth", [["token"], ["key", "password"]])
    def test_ssh_authenticates_one_supported_way(self, auth: list[str]):
        assert "auth" in rejected(**{**SSH, "auth": auth})

    def test_htcondor_ce_needs_a_token(self):
        assert "token" in rejected(
            kind="htcondor-ce", endpoint="ce:9619", auth=["proxy"]
        )

    def test_ce_auth_is_token_or_proxy(self):
        assert "auth" in rejected(kind="arc", endpoint="https://a:443", auth=["key"])

    def test_limits_must_be_positive(self):
        assert "max_output_files" in rejected(
            kind="arc", endpoint="https://a:443", max_output_files=0
        )

    def test_container_launcher_must_name_the_image(self):
        base = {"kind": "batch", "transport": "local", "scheduler": "slurm"}
        assert "{image}" in rejected(**base, container_launcher=["apptainer", "exec"])
        BackendConfig.model_validate(
            {**base, "container_launcher": ["apptainer", "exec", "{image}"]}
        )

    @pytest.mark.parametrize("field", ["password", "private_key", "token", "proxy"])
    def test_there_is_nowhere_to_put_a_secret(self, field: str):
        # Secrets come from the credential provider, never from configuration (IC-ADR-003).
        assert field in rejected(**{**SSH, field: "x"})


def test_configuration_is_immutable():
    config = BackendConfig(kind="arc", endpoint="https://a:443")
    with pytest.raises(ValidationError):
        config.endpoint = "https://b:443"  # type: ignore[misc]
