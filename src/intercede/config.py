"""Backend configuration: how a consumer says which backend it wants.

There is one model per kind of backend, and ``BackendConfig`` is any of them,
chosen by ``kind`` (and, for ``batch``, by ``transport``). Each model has
exactly the fields of its backend, so a field that does not apply is refused
when the configuration is read, not ignored or discovered at the first submit.

A consumer can build a model directly, or pass the same data as a mapping, such
as a parsed TOML table, to ``validate_config`` or ``registry.get_backend``::

    kind = "batch"
    transport = "ssh"
    scheduler = "slurm"
    host = "batch.example.org"
    user = "dirac"
    known_hosts = "batch.example.org ssh-ed25519 AAAA..."

Configuration describes the resource and is never secret: keys, passwords,
tokens and proxies come from the credential provider at run time. Error
messages therefore never repeat the values they were given. The kinds are a
closed set: they list what interCEde ships.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    PositiveInt,
    Tag,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from intercede.exceptions import ConfigurationError

__all__ = [
    "ARCConfig",
    "BackendConfig",
    "BatchLocalConfig",
    "BatchSSHConfig",
    "CloudConfig",
    "HTCondorCEConfig",
    "validate_config",
]


class _Config(BaseModel):
    """Base of every configuration model: immutable, strict, and silent about values."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        # A value given by mistake may be a secret: never repeat it in an error.
        hide_input_in_errors=True,
        use_attribute_docstrings=True,
    )


class _QueueConfig(_Config):
    """What every backend that queues jobs on a resource shares."""

    queue: str | None = None
    """The queue a job goes to when its specification names none."""

    container_launcher: Sequence[str] | None = None
    """An argv template that runs the executable in a container image, with an
    ``{image}`` placeholder, e.g. ``["apptainer", "exec", "--bind", "/cvmfs", "{image}"]``."""

    max_output_bytes: PositiveInt = 10 * 2**30
    """Collection of a job's output stops beyond this many bytes."""

    max_output_files: PositiveInt = 10_000
    """Collection of a job's output stops beyond this many files."""

    transfer_timeout_s: PositiveInt = 3600
    """How long collecting one job's output may take, in seconds."""

    @model_validator(mode="after")
    def _launcher_names_the_image(self) -> Self:
        launcher = self.container_launcher
        if launcher is not None and not any("{image}" in arg for arg in launcher):
            raise ValueError("container_launcher must contain an '{image}' placeholder")
        return self


# -- grid CEs that schedule by themselves -------------------------------------


class ARCConfig(_QueueConfig):
    """An ARC CE."""

    kind: Literal["arc"]
    """Selects this model."""

    endpoint: str
    """The CE's REST endpoint, e.g. ``"https://arc.example.org:443/arex"``."""

    auth: frozenset[Literal["token", "proxy"]] = frozenset({"token"})
    """Which credentials the provider must supply, all of them together: a token
    for the channel, a proxy for delegation, or both."""

    @model_validator(mode="after")
    def _auth_not_empty(self) -> Self:
        if not self.auth:
            raise ValueError("auth must name at least one of 'token', 'proxy'")
        return self


class HTCondorCEConfig(_QueueConfig):
    """An HTCondor-CE."""

    kind: Literal["htcondor-ce"]
    """Selects this model."""

    endpoint: str
    """The CE's collector, as ``"host"`` or ``"host:port"``, e.g. ``"ce.example.org:9619"``."""

    auth: frozenset[Literal["token", "proxy"]] = frozenset({"token"})
    """Which credentials the provider must supply. The token authenticates the
    channel; ``"proxy"`` adds an X.509 proxy for sites that account on VOMS attributes."""

    @model_validator(mode="after")
    def _token_required(self) -> Self:
        if "token" not in self.auth:
            raise ValueError(
                "an HTCondor-CE authenticates with a token; auth must include 'token'"
            )
        return self


# -- a cloud provider that boots one VM per job --------------------------------

_SECRET_NAMES = ("secret", "password", "token", "passphrase")
_SECRET_EXACT = ("key", "api_key", "access_key", "private_key")


class CloudConfig(_Config):
    """A cloud provider that boots one VM per job.

    There is no queue and no filesystem to collect from afterwards, so the
    queue, output limits and container launcher of the other kinds do not apply.
    """

    kind: Literal["cloud"]
    """Selects this model."""

    endpoint: str
    """The cloud's API endpoint, and its identity in job handles."""

    driver: str
    """The driver library's name for this cloud, e.g. ``"openstack"``."""

    driver_options: Mapping[str, str] = {}
    """Extra, non-secret arguments for the driver, e.g. ``{"ex_tenant_name": "dirac"}``.
    The cloud's API keys come from the credential provider, never from here."""

    vm_image: str
    """An image ID, or ``"name:<image name>"``."""

    vm_flavor: str
    """A flavor ID, or ``"name:<flavor name>"``."""

    vm_networks: Sequence[str] = ()
    """The networks to attach the VM to."""

    vm_ssh_key: str | None = None
    """The name of a key pair known to the cloud, not the key itself."""

    @model_validator(mode="after")
    def _no_secret_driver_options(self) -> Self:
        secret = sorted(
            name
            for name in self.driver_options
            if name.lower() in _SECRET_EXACT
            or any(word in name.lower() for word in _SECRET_NAMES)
        )
        if secret:
            raise ValueError(
                f"driver_options must not hold secrets {secret}; "
                "they come from the credential provider"
            )
        return self


# -- a batch system reached through a transport --------------------------------


class _BatchConfig(_QueueConfig):
    kind: Literal["batch"]
    """Selects a batch system; ``transport`` then selects the model."""

    scheduler: Literal["slurm", "htcondor", "direct"]
    """The batch system on the resource; ``"direct"`` runs jobs without one."""


class BatchLocalConfig(_BatchConfig):
    """A batch system on this host."""

    transport: Literal["local"]
    """Run the batch system's commands on this host."""


class BatchSSHConfig(_BatchConfig):
    """A batch system on a remote host, reached over SSH."""

    transport: Literal["ssh"]
    """Run the batch system's commands on ``host``, over SSH."""

    host: str
    """The host to log in to."""

    port: PositiveInt = 22
    """The SSH port on ``host``."""

    user: str
    """The account to log in as."""

    known_hosts: str
    """The host keys to expect, in ``known_hosts`` format. Verification is not optional."""

    auth: Literal["key", "password", "agent"] = "key"
    """How to log in: a key or a password from the credential provider, or an SSH agent."""

    jump_host: str | None = None
    """A host to connect through, as ``"[user@]host[:port]"``."""

    connect_timeout_s: PositiveInt = 30
    """How long connecting may take, in seconds."""

    @model_validator(mode="after")
    def _known_hosts_not_empty(self) -> Self:
        if not self.known_hosts.strip():
            raise ValueError("known_hosts must hold the expected host keys")
        return self


def _choice(value: Any) -> str | None:
    """Return which model a configuration is for: its ``kind``, and for ``batch`` its transport."""
    if isinstance(value, Mapping):
        kind, transport = value.get("kind"), value.get("transport")
    else:
        kind, transport = (
            getattr(value, "kind", None),
            getattr(value, "transport", None),
        )
    if not isinstance(kind, str):
        return None
    return f"batch+{transport}" if kind == "batch" else kind


_MODELS: dict[str, type[_Config]] = {
    "arc": ARCConfig,
    "htcondor-ce": HTCondorCEConfig,
    "cloud": CloudConfig,
    "batch+local": BatchLocalConfig,
    "batch+ssh": BatchSSHConfig,
}

BackendConfig = Annotated[
    Annotated[ARCConfig, Tag("arc")]
    | Annotated[HTCondorCEConfig, Tag("htcondor-ce")]
    | Annotated[CloudConfig, Tag("cloud")]
    | Annotated[BatchLocalConfig, Tag("batch+local")]
    | Annotated[BatchSSHConfig, Tag("batch+ssh")],
    Discriminator(_choice),
]
"""The configuration of any backend: one of the models above, chosen by ``kind``."""

_ADAPTER: TypeAdapter[BackendConfig] = TypeAdapter(BackendConfig)


def validate_config(config: BackendConfig | Mapping[str, Any]) -> BackendConfig:
    """Check a configuration and return the model of the backend it describes.

    A model is checked again, so one changed with ``model_copy``, which does not
    validate, cannot reach a backend with fields that do not apply.

    Args:
        config: A configuration model, or the same data as a mapping, for
            example a parsed TOML table.

    Returns:
        The model of the backend ``config`` describes.

    Raises:
        ConfigurationError: The configuration is invalid. The message names
            every problem by field, and never repeats a value it was given.
    """
    # Only what was set: defaults of another kind's fields are not the user's mistake.
    data = (
        config.model_dump(exclude_unset=True)
        if isinstance(config, BaseModel)
        else config
    )
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise ConfigurationError(_describe(data, exc)) from None


def _describe(data: Any, exc: ValidationError) -> str:
    """Report every problem by the field the consumer wrote, without its value.

    Args:
        data: The configuration that was refused.
        exc: What validating it raised.

    Returns:
        One line per problem, joined with "; ", such as
        "host: required for kind='batch' with transport='ssh'".
    """
    choice = _choice(data)
    model = _MODELS.get(choice or "")
    where = (
        "kind='batch' with transport={!r}".format(choice.removeprefix("batch+"))
        if choice and choice.startswith("batch+")
        else f"kind={choice!r}"
    )
    problems = []
    unknown = []
    for error in exc.errors():
        fields = [part for part in error["loc"] if isinstance(part, str)]
        field = fields[-1] if fields and fields[-1] not in _MODELS else None
        if error["type"] in ("union_tag_not_found", "union_tag_invalid"):
            problems.append(_unknown_kind(data))
        elif error["type"] == "extra_forbidden" and field is not None:
            unknown.append(field)
        elif error["type"] == "missing":
            problems.append(f"{field}: required for {where}")
        else:
            message = error["msg"].removeprefix("Value error, ")
            problems.append(f"{field}: {message}" if field else message)
    if unknown and model is not None:
        expected = ", ".join(name for name in model.model_fields if name != "kind")
        problems.insert(
            0, f"{', '.join(unknown)}: not for {where} (expected: {expected})"
        )
    return "; ".join(problems)


def _unknown_kind(data: Any) -> str:
    kinds = sorted({choice.split("+")[0] for choice in _MODELS})
    if isinstance(data, Mapping) and data.get("kind") == "batch":
        return "transport: must be one of 'local', 'ssh'"
    return f"kind: must be one of {', '.join(map(repr, kinds))}"
