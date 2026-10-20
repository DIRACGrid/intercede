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
    CloudSettings,
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


CLOUD: dict[str, Any] = {
    "kind": "cloud",
    "endpoint": "https://cloud.example.org:5000",
    "driver": "openstack",
    "vm_image": "name:alma9-pilot",
    "vm_flavor": "name:m1.large",
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


class TestCloud:
    """The provider family: a cloud boots one VM per job, with no queue to collect from."""

    def test_resolves(self):
        config = BackendConfig.model_validate(
            {
                **CLOUD,
                "driver_options": {"ex_tenant_name": "dirac"},
                "vm_networks": ["public"],
            }
        )
        settings = resolve(config)
        assert isinstance(settings, CloudSettings)
        assert settings.driver_options == {"ex_tenant_name": "dirac"}
        assert settings.vm_max_lifetime_s == 14 * 24 * 3600

    @pytest.mark.parametrize("field", ["driver", "vm_image", "vm_flavor", "endpoint"])
    def test_required_fields(self, field: str):
        assert f"{field}: required for kind='cloud'" in rejected(
            **{**CLOUD, field: None}
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("queue", "short"),
            ("max_output_files", 10),
            ("container_launcher", ["apptainer", "exec", "{image}"]),
            ("auth", ["token"]),
            ("host", "h"),
        ],
    )
    def test_fields_of_other_families_do_not_apply(self, field: str, value: Any):
        assert f"{field}: does not apply to kind='cloud'" in rejected(
            **{**CLOUD, field: value}
        )

    @pytest.mark.parametrize(
        "option", ["secret", "key", "api_key", "os_password", "auth_token"]
    )
    def test_driver_options_hold_no_secrets(self, option: str):
        assert "credential provider" in rejected(
            **{**CLOUD, "driver_options": {option: "x"}}
        )

    def test_vm_fields_do_not_apply_to_a_ce(self):
        assert "vm_image: does not apply" in rejected(
            kind="arc", endpoint="https://a:443", vm_image="name:x"
        )


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


# --------------------------------------------------------------------------
# Every field against every kind
# --------------------------------------------------------------------------

# The smallest valid configuration of each backend shape.
BASES: dict[str, dict[str, Any]] = {
    "arc": {"kind": "arc", "endpoint": "https://arc.example.org:443"},
    "htcondor-ce": {"kind": "htcondor-ce", "endpoint": "ce.example.org:9619"},
    "cloud": CLOUD,
    "batch-local": {"kind": "batch", "transport": "local", "scheduler": "slurm"},
    "batch-ssh": SSH,
}

# Which flat fields each shape accepts (besides "kind").
_SHARED = {
    "queue",
    "container_launcher",
    "max_output_bytes",
    "max_output_files",
    "transfer_timeout_s",
}
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
    "batch-ssh": {
        "transport",
        "scheduler",
        "host",
        "port",
        "user",
        "known_hosts",
        "jump_host",
        "connect_timeout_s",
        "auth",
    }
    | _SHARED,
}

# A valid value for every flat field.
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


def value_for(shape: str, field: str) -> Any:
    """Return a value of ``field`` that is valid for ``shape``."""
    if field == "auth" and shape == "batch-ssh":
        return ["password"]
    if field == "transport" and shape == "batch-ssh":
        return "ssh"
    return VALUES[field]


def test_matrix_covers_every_flat_field():
    """Adding a field to BackendConfig without adding it here fails this test."""
    assert set(VALUES) == set(BackendConfig.model_fields) - {"kind"}


def test_matrix_covers_every_kind():
    kinds = {base["kind"] for base in BASES.values()}
    assert kinds == {"arc", "htcondor-ce", "cloud", "batch"}


NOT_APPLYING = [
    (shape, field)
    for shape in BASES
    for field in sorted(VALUES)
    if field not in APPLIES[shape]
]
APPLYING = [
    (shape, field)
    for shape in BASES
    for field in sorted(APPLIES[shape])
    if field not in BASES[shape]
]


@pytest.mark.parametrize(("shape", "field"), NOT_APPLYING)
def test_every_field_is_refused_where_it_does_not_apply(shape: str, field: str):
    message = rejected(**{**BASES[shape], field: VALUES[field]})
    assert f"{field}: does not apply to kind=" in message


@pytest.mark.parametrize(("shape", "field"), APPLYING)
def test_every_optional_field_is_accepted_where_it_applies(shape: str, field: str):
    config = BackendConfig.model_validate(
        {**BASES[shape], field: value_for(shape, field)}
    )
    assert resolve(config).kind == BASES[shape]["kind"]


@pytest.mark.parametrize(("shape", "field"), APPLYING)
def test_every_optional_field_reaches_the_settings(shape: str, field: str):
    """A value the consumer sets is never lost on its way to the backend."""
    value = value_for(shape, field)
    settings = resolve(BackendConfig.model_validate({**BASES[shape], field: value}))
    if field in ("max_output_bytes", "max_output_files", "transfer_timeout_s"):
        found = getattr(settings.limits, field)  # type: ignore[union-attr]
    elif shape == "batch-ssh" and field in (
        "host",
        "port",
        "user",
        "known_hosts",
        "jump_host",
        "connect_timeout_s",
    ):
        found = getattr(settings.transport, field)  # type: ignore[union-attr]
    elif shape == "batch-ssh" and field == "auth":
        found = [settings.transport.auth]  # type: ignore[union-attr]
    else:
        found = getattr(settings, field)
    if isinstance(value, list) and field != "auth":
        assert list(found) == value
    elif field == "auth" and shape != "batch-ssh":
        assert found == set(value)
    else:
        assert found == value


REQUIRED = [
    ("arc", "endpoint"),
    ("htcondor-ce", "endpoint"),
    ("cloud", "endpoint"),
    ("cloud", "driver"),
    ("cloud", "vm_image"),
    ("cloud", "vm_flavor"),
    ("batch-local", "transport"),
    ("batch-local", "scheduler"),
    ("batch-ssh", "scheduler"),
    ("batch-ssh", "host"),
    ("batch-ssh", "user"),
    ("batch-ssh", "known_hosts"),
]


@pytest.mark.parametrize(("shape", "field"), REQUIRED)
def test_every_required_field_is_named_when_missing(shape: str, field: str):
    data = {name: value for name, value in BASES[shape].items() if name != field}
    assert f"{field}: required for" in rejected(**data)


@pytest.mark.parametrize("shape", sorted(BASES))
def test_every_shape_round_trips_through_json(shape: str):
    config = BackendConfig.model_validate(BASES[shape])
    assert BackendConfig.model_validate_json(config.model_dump_json()) == config


# --------------------------------------------------------------------------
# Defaults, values and messages
# --------------------------------------------------------------------------


class TestDefaults:
    def test_ce_authenticates_with_a_token(self):
        for shape in ("arc", "htcondor-ce"):
            settings = resolve(BackendConfig.model_validate(BASES[shape]))
            assert settings.auth == {"token"}  # type: ignore[union-attr]

    def test_ssh(self):
        settings = resolve(BackendConfig.model_validate(SSH))
        assert isinstance(settings, BatchSettings)
        transport = settings.transport
        assert isinstance(transport, SSHTransportSettings)
        assert (transport.port, transport.auth, transport.connect_timeout_s) == (
            22,
            "key",
            30,
        )
        assert transport.jump_host is None

    def test_limits(self):
        settings = resolve(BackendConfig.model_validate(BASES["arc"]))
        assert settings.limits.max_output_bytes == 10 * 2**30  # type: ignore[union-attr]
        assert settings.limits.max_output_files == 10_000  # type: ignore[union-attr]
        assert settings.limits.transfer_timeout_s == 3600  # type: ignore[union-attr]

    def test_setting_one_limit_keeps_the_other_defaults(self):
        config = BackendConfig.model_validate({**BASES["arc"], "transfer_timeout_s": 5})
        limits = resolve(config).limits  # type: ignore[union-attr]
        assert (limits.transfer_timeout_s, limits.max_output_files) == (5, 10_000)

    def test_cloud(self):
        settings = resolve(BackendConfig.model_validate(CLOUD))
        assert isinstance(settings, CloudSettings)
        assert settings.driver_options == {}
        assert tuple(settings.vm_networks) == ()
        assert settings.vm_ssh_key is None
        assert settings.vm_max_lifetime_s == 14 * 24 * 3600

    def test_shared_fields(self):
        settings = resolve(BackendConfig.model_validate(BASES["batch-local"]))
        assert settings.queue is None  # type: ignore[union-attr]
        assert settings.container_launcher is None  # type: ignore[union-attr]


class TestAuthValues:
    @pytest.mark.parametrize("auth", [["token"], ["proxy"], ["token", "proxy"]])
    def test_arc_accepts_any_non_empty_combination(self, auth: list[str]):
        settings = resolve(BackendConfig.model_validate({**BASES["arc"], "auth": auth}))
        assert settings.auth == set(auth)  # type: ignore[union-attr]

    def test_arc_needs_some_credential(self):
        assert "auth must name at least one" in rejected(**BASES["arc"], auth=[])

    @pytest.mark.parametrize("auth", [["token"], ["token", "proxy"]])
    def test_htcondor_ce_accepts_token_with_or_without_proxy(self, auth: list[str]):
        config = BackendConfig.model_validate({**BASES["htcondor-ce"], "auth": auth})
        assert resolve(config).auth == set(auth)  # type: ignore[union-attr]

    @pytest.mark.parametrize("auth", [[], ["proxy"]])
    def test_htcondor_ce_refuses_without_token(self, auth: list[str]):
        assert "must include 'token'" in rejected(**BASES["htcondor-ce"], auth=auth)

    @pytest.mark.parametrize("auth", [["key"], ["password"], ["agent"]])
    def test_ssh_accepts_each_mode(self, auth: list[str]):
        settings = resolve(BackendConfig.model_validate({**SSH, "auth": auth}))
        assert settings.transport.auth == auth[0]  # type: ignore[union-attr]

    @pytest.mark.parametrize("auth", [[], ["token"], ["proxy"], ["key", "agent"]])
    def test_ssh_refuses_anything_else(self, auth: list[str]):
        assert "auth" in rejected(**SSH, auth=auth)

    @pytest.mark.parametrize("shape", ["arc", "htcondor-ce"])
    @pytest.mark.parametrize("auth", [["key"], ["password"], ["agent"], ["x509"]])
    def test_ce_refuses_ssh_and_unknown_modes(self, shape: str, auth: list[str]):
        assert "auth" in rejected(**BASES[shape], auth=auth)


POSITIVE = [
    "port",
    "connect_timeout_s",
    "vm_max_lifetime_s",
    "max_output_bytes",
    "max_output_files",
    "transfer_timeout_s",
]


@pytest.mark.parametrize("field", POSITIVE)
@pytest.mark.parametrize("value", [0, -1])
def test_numbers_must_be_positive(field: str, value: int):
    assert field in rejected(**{**SSH, field: value})


@pytest.mark.parametrize("field", ["kind", "transport", "scheduler"])
def test_choices_are_case_sensitive(field: str):
    assert field in rejected(**{**SSH, field: SSH[field].upper()})


def test_every_problem_is_reported_at_once():
    message = rejected(kind="batch", transport="ssh", scheduler="slurm")
    assert "host: required" in message
    assert "user: required" in message
    assert "known_hosts: required" in message
    assert message.count("; ") >= 2


def test_message_has_no_pydantic_noise():
    message = rejected(**BASES["arc"], auth=[])
    assert "Value error, Value error" not in message
    assert "tagged-union" not in message


@pytest.mark.parametrize(
    "option", ["ex_tenant_name", "ex_force_auth_url", "region", "ex_keyname"]
)
def test_ordinary_driver_options_are_accepted(option: str):
    config = BackendConfig.model_validate({**CLOUD, "driver_options": {option: "x"}})
    assert resolve(config).driver_options == {option: "x"}  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "option", ["SECRET", "Password", "access_key", "private_key", "passphrase"]
)
def test_secret_driver_options_are_refused_in_any_case(option: str):
    assert "credential provider" in rejected(**CLOUD, driver_options={option: "x"})


def test_container_launcher_placeholder_may_be_inside_an_argument():
    config = BackendConfig.model_validate(
        {**BASES["batch-local"], "container_launcher": ["run", "--image={image}"]}
    )
    assert resolve(config).container_launcher == ["run", "--image={image}"]  # type: ignore[union-attr]


def test_known_hosts_of_only_whitespace_is_refused():
    assert "known_hosts" in rejected(**{**SSH, "known_hosts": "\n\t "})
