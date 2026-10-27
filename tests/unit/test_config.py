"""Backend configuration, through the public API: one model per kind of backend."""

from __future__ import annotations

from typing import Any

import pytest

from intercede import (
    ARCConfig,
    BatchLocalConfig,
    BatchSSHConfig,
    CloudConfig,
    ConfigurationError,
    HTCondorCEConfig,
    validate_config,
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


def rejected(**data: Any) -> str:
    """Return the message for a configuration that must be refused."""
    with pytest.raises(ConfigurationError) as exc_info:
        validate_config(data)
    return str(exc_info.value)


@pytest.mark.parametrize(
    ("data", "model"),
    [
        (ARC, ARCConfig),
        (CE, HTCondorCEConfig),
        (CLOUD, CloudConfig),
        (LOCAL, BatchLocalConfig),
        (SSH, BatchSSHConfig),
    ],
    ids=["arc", "htcondor-ce", "cloud", "batch-local", "batch-ssh"],
)
def test_kind_and_transport_choose_the_model(data: dict[str, Any], model: type):
    assert type(validate_config(data)) is model


def test_defaults_are_filled_in():
    ssh = validate_config(SSH)
    assert isinstance(ssh, BatchSSHConfig)
    assert (ssh.port, ssh.auth, ssh.connect_timeout_s) == (22, "key", 30)
    assert ssh.transfer_timeout_s == 3600
    ce = validate_config(CE)
    assert isinstance(ce, HTCondorCEConfig) and ce.auth == {"token"}


def test_a_model_passes_through_unchanged():
    model = HTCondorCEConfig(kind="htcondor-ce", endpoint="ce:9619", queue="short")
    assert validate_config(model) == model


class TestRefused:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (
                {"kind": "lsf"},
                "kind: must be one of 'arc', 'batch', 'cloud', 'htcondor-ce'",
            ),
            (
                {**LOCAL, "transport": "pigeon"},
                "transport: must be one of 'local', 'ssh'",
            ),
            ({"kind": "batch", "scheduler": "slurm"}, "transport: must be one of"),
        ],
        ids=["kind", "transport", "no-transport"],
    )
    def test_an_unknown_choice(self, data: dict[str, Any], message: str):
        assert message in rejected(**data)

    @pytest.mark.parametrize(
        ("data", "field", "where"),
        [
            ({**ARC, "host": "h"}, "host", "kind='arc'"),
            ({**CLOUD, "queue": "short"}, "queue", "kind='cloud'"),
            (
                {**LOCAL, "known_hosts": "h k"},
                "known_hosts",
                "kind='batch' with transport='local'",
            ),
            (
                {**SSH, "endpoint": "https://a"},
                "endpoint",
                "kind='batch' with transport='ssh'",
            ),
        ],
    )
    def test_a_field_of_another_kind(
        self, data: dict[str, Any], field: str, where: str
    ):
        assert f"{field}: not for {where} (expected: " in rejected(**data)

    @pytest.mark.parametrize("data", [None, "arc", ["kind", "arc"]])
    def test_something_that_is_not_a_configuration(self, data: Any):
        # For example a config file read as text, or a missing table.
        with pytest.raises(ConfigurationError, match="kind: must be one of"):
            validate_config(data)

    def test_every_missing_field_is_named_at_once(self):
        message = rejected(kind="batch", transport="ssh", scheduler="slurm")
        for field in ("host", "user", "known_hosts"):
            assert f"{field}: required for kind='batch' with transport='ssh'" in message

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({**ARC, "auth": []}, "auth must name at least one"),
            ({**CE, "auth": ["proxy"]}, "must include 'token'"),
            ({**ARC, "auth": ["key"]}, "auth"),
            ({**SSH, "auth": "token"}, "auth"),
            (
                {**SSH, "known_hosts": "\n\t "},
                "known_hosts must hold the expected host keys",
            ),
            (
                {**LOCAL, "container_launcher": ["apptainer", "exec"]},
                "'{image}' placeholder",
            ),
            ({**CLOUD, "driver_options": {"OS_PASSWORD": "x"}}, "credential provider"),
            ({**LOCAL, "max_output_files": 0}, "max_output_files"),
        ],
        ids=[
            "arc-no-auth",
            "htcondor-ce-no-token",
            "ce-ssh-auth",
            "ssh-ce-auth",
            "no-host-keys",
            "launcher-no-image",
            "secret-driver-option",
            "zero-limit",
        ],
    )
    def test_a_rule_of_the_kind(self, data: dict[str, Any], message: str):
        assert message in rejected(**data)


class TestNeverRepeatsAValue:
    """A value given by mistake may be a secret, so no error message repeats one."""

    def test_an_unknown_field(self):
        message = rejected(**SSH, password="toto4")  # noqa: S106  (fake)
        assert "toto4" not in message
        assert "password: not for kind='batch' with transport='ssh'" in message

    def test_a_wrong_value(self):
        assert "toto4" not in rejected(**SSH, port="toto4")

    def test_building_a_model_directly(self):
        with pytest.raises(ValueError) as exc_info:
            BatchSSHConfig(**SSH, password="toto4")  # type: ignore[call-arg]  # noqa: S106
        assert "toto4" not in str(exc_info.value)


def test_a_copied_model_is_checked_again():
    # model_copy does not validate: a field of the old transport must not slip through.
    local = validate_config(SSH).model_copy(update={"transport": "local"})
    with pytest.raises(ConfigurationError) as exc_info:
        validate_config(local)
    assert (
        "host, user, known_hosts: not for kind='batch' with transport='local'"
        in str(exc_info.value)
    )
