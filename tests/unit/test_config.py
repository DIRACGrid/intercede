"""Backend configuration: one flat model, checked against the backend it names."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from intercede import BackendConfig
from intercede._backends.config import (
    ARCSettings,
    BatchSettings,
    CloudSettings,
    HTCondorCESettings,
    LocalTransportSettings,
    SSHTransportSettings,
    resolve,
)

ARC: dict[str, Any] = {"kind": "arc", "endpoint": "https://arc.example.org:443"}
CE: dict[str, Any] = {"kind": "htcondor-ce", "endpoint": "ce.example.org:9619"}
CLOUD: dict[str, Any] = {
    "kind": "cloud",
    "endpoint": "https://cloud.example.org:5000",
    "driver": "openstack",
    "vm_image": "name:alma9-pilot",
    "vm_flavor": "name:m1.large",
}
LOCAL: dict[str, Any] = {"kind": "batch", "transport": "local", "scheduler": "slurm"}
SSH: dict[str, Any] = {
    "kind": "batch",
    "transport": "ssh",
    "scheduler": "slurm",
    "host": "batch.example.org",
    "user": "dirac",
    "known_hosts": "batch.example.org ssh-ed25519 AAAAC3Nz...",
}


def settings_of(data: dict[str, Any]) -> Any:
    return resolve(BackendConfig.model_validate(data))


def rejected(**data: Any) -> str:
    """Return the error message for configuration that must be refused."""
    with pytest.raises(ValidationError) as exc_info:
        BackendConfig.model_validate(data)
    return str(exc_info.value)


# --------------------------------------------------------------------------
# From the flat model to the settings of one backend
# --------------------------------------------------------------------------


class TestResolve:
    def test_arc(self):
        settings = settings_of(ARC)
        assert isinstance(settings, ARCSettings)
        assert settings.auth == {"token"}
        assert settings.limits.transfer_timeout_s == 3600

    def test_htcondor_ce(self):
        settings = settings_of({**CE, "auth": ["token", "proxy"]})
        assert isinstance(settings, HTCondorCESettings)
        assert settings.auth == {"token", "proxy"}

    def test_cloud(self):
        settings = settings_of({**CLOUD, "driver_options": {"ex_tenant_name": "d"}})
        assert isinstance(settings, CloudSettings)
        assert settings.driver_options == {"ex_tenant_name": "d"}

    def test_batch_over_ssh(self):
        settings = settings_of({**SSH, "auth": ["agent"], "queue": "short"})
        assert isinstance(settings, BatchSettings)
        assert settings.queue == "short"
        assert isinstance(settings.transport, SSHTransportSettings)
        assert (settings.transport.host, settings.transport.port) == (
            "batch.example.org",
            22,
        )
        assert settings.transport.auth == "agent"

    def test_batch_on_this_host(self):
        settings = settings_of(LOCAL)
        assert isinstance(settings, BatchSettings)
        assert isinstance(settings.transport, LocalTransportSettings)

    def test_setting_one_limit_keeps_the_other_defaults(self):
        limits = settings_of({**ARC, "transfer_timeout_s": 5}).limits
        assert (limits.transfer_timeout_s, limits.max_output_files) == (5, 10_000)


# --------------------------------------------------------------------------
# Every field against every kind: a field that does not apply is refused,
# and one that does reaches the settings.
# --------------------------------------------------------------------------

BASES: dict[str, dict[str, Any]] = {
    "arc": ARC,
    "htcondor-ce": CE,
    "cloud": CLOUD,
    "batch-local": LOCAL,
    "batch-ssh": SSH,
}
_SHARED = {
    "queue",
    "container_launcher",
    "max_output_bytes",
    "max_output_files",
    "transfer_timeout_s",
}
_SSH_FIELDS = {"host", "port", "user", "known_hosts", "jump_host", "connect_timeout_s"}
APPLIES: dict[str, set[str]] = {
    "arc": {"endpoint", "auth"} | _SHARED,
    "htcondor-ce": {"endpoint", "auth"} | _SHARED,
    "cloud": {
        "endpoint",
        "driver",
        "driver_options",
        "vm_image",
        "vm_flavor",
        "vm_networks",
        "vm_ssh_key",
        "vm_max_lifetime_s",
    },
    "batch-local": {"transport", "scheduler"} | _SHARED,
    "batch-ssh": {"transport", "scheduler", "auth"} | _SSH_FIELDS | _SHARED,
}
VALUES: dict[str, Any] = {
    "transport": "local",
    "scheduler": "htcondor",
    "endpoint": "https://other.example.org:443",
    "host": "other.example.org",
    "port": 2222,
    "user": "pilot",
    "known_hosts": "other.example.org ssh-ed25519 AAAAC3Nz...",
    "jump_host": "gateway.example.org",
    "connect_timeout_s": 5,
    "driver": "ec2",
    "driver_options": {"ex_tenant_name": "dirac"},
    "vm_image": "name:other",
    "vm_flavor": "name:m1.small",
    "vm_networks": ["public", "private"],
    "vm_ssh_key": "pilot-keypair",
    "vm_max_lifetime_s": 3600,
    "auth": ["token"],
    "queue": "long",
    "container_launcher": ["apptainer", "exec", "{image}"],
    "max_output_bytes": 1024,
    "max_output_files": 7,
    "transfer_timeout_s": 60,
}


def test_matrix_covers_every_field():
    """Adding a field to BackendConfig without adding it here fails this test."""
    assert set(VALUES) == set(BackendConfig.model_fields) - {"kind"}


@pytest.mark.parametrize(
    ("shape", "field"),
    [(s, f) for s in BASES for f in sorted(VALUES) if f not in APPLIES[s]],
)
def test_a_field_is_refused_where_it_does_not_apply(shape: str, field: str):
    message = rejected(**{**BASES[shape], field: VALUES[field]})
    assert f"{field}: does not apply to kind=" in message


@pytest.mark.parametrize(
    ("shape", "field"),
    [(s, f) for s in BASES for f in sorted(APPLIES[s]) if f not in BASES[s]],
)
def test_a_field_reaches_the_settings_where_it_applies(shape: str, field: str):
    value = VALUES[field]
    if shape == "batch-ssh" and field in ("auth", "transport"):
        value = {"auth": ["password"], "transport": "ssh"}[field]
    settings = settings_of({**BASES[shape], field: value})
    if field in ("max_output_bytes", "max_output_files", "transfer_timeout_s"):
        found = getattr(settings.limits, field)
    elif shape == "batch-ssh" and field in _SSH_FIELDS:
        found = getattr(settings.transport, field)
    elif shape == "batch-ssh" and field == "auth":
        found = [settings.transport.auth]
    else:
        found = getattr(settings, field)
    if field == "auth" and shape != "batch-ssh":
        assert found == set(value)
    elif isinstance(value, list) and field != "auth":
        assert list(found) == value
    else:
        assert found == value


@pytest.mark.parametrize(
    ("shape", "field"),
    [
        ("arc", "endpoint"),
        ("htcondor-ce", "endpoint"),
        ("cloud", "driver"),
        ("cloud", "vm_image"),
        ("batch-local", "transport"),
        ("batch-local", "scheduler"),
        ("batch-ssh", "host"),
        ("batch-ssh", "known_hosts"),
    ],
)
def test_a_missing_field_is_named(shape: str, field: str):
    data = {name: value for name, value in BASES[shape].items() if name != field}
    assert f"{field}: required for" in rejected(**data)


# --------------------------------------------------------------------------
# Per-kind rules
# --------------------------------------------------------------------------


class TestRules:
    def test_unknown_kind(self):
        assert "kind" in rejected(kind="lsf")

    def test_a_typo_is_not_silently_ignored(self):
        assert "endpont" in rejected(**ARC, endpont="x")

    def test_arc_needs_some_credential(self):
        assert "auth must name at least one" in rejected(**ARC, auth=[])

    @pytest.mark.parametrize("auth", [[], ["proxy"]])
    def test_htcondor_ce_needs_a_token(self, auth: list[str]):
        assert "must include 'token'" in rejected(**CE, auth=auth)

    @pytest.mark.parametrize("auth", [["key"], ["x509"]])
    def test_a_ce_authenticates_with_token_or_proxy(self, auth: list[str]):
        assert "auth" in rejected(**ARC, auth=auth)

    @pytest.mark.parametrize("auth", [["token"], ["key", "password"]])
    def test_ssh_authenticates_one_supported_way(self, auth: list[str]):
        assert "auth" in rejected(**SSH, auth=auth)

    def test_ssh_requires_host_key_verification(self):
        assert "known_hosts" in rejected(**{**SSH, "known_hosts": "\n\t "})

    def test_container_launcher_must_name_the_image(self):
        assert "{image}" in rejected(**LOCAL, container_launcher=["apptainer", "exec"])
        assert settings_of({**LOCAL, "container_launcher": ["run", "--img={image}"]})

    @pytest.mark.parametrize("option", ["SECRET", "api_key", "os_password", "token"])
    def test_driver_options_hold_no_secrets(self, option: str):
        assert "credential provider" in rejected(**CLOUD, driver_options={option: "x"})

    def test_every_problem_is_reported_at_once_without_pydantic_noise(self):
        message = rejected(kind="batch", transport="ssh", scheduler="slurm")
        for field in ("host", "user", "known_hosts"):
            assert f"{field}: required" in message
        assert "Value error, Value error" not in message
        assert "tagged-union" not in message
