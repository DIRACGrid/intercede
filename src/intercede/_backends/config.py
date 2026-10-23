"""What each backend needs from the public ``BackendConfig``.

The consumer describes a backend with one flat model. ``resolve`` turns it into
the settings of the one backend it names, and is where per-kind rules live: a
field that does not apply to the chosen kind is rejected, not ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import Discriminator, PositiveInt, TypeAdapter, model_validator

from intercede.models import _Model

if TYPE_CHECKING:
    from intercede.config import BackendConfig

LIMIT_FIELDS = ("max_output_bytes", "max_output_files", "transfer_timeout_s")
SSH_FIELDS = ("host", "port", "user", "known_hosts", "jump_host", "connect_timeout_s")


class Limits(_Model):
    """Bounds on output collection. Path containment is always on."""

    max_output_bytes: PositiveInt = 10 * 2**30
    max_output_files: PositiveInt = 10_000
    transfer_timeout_s: PositiveInt = 3600


class _BackendSettings(_Model):
    queue: str | None = None
    container_launcher: Sequence[str] | None = None
    limits: Limits = Limits()

    @model_validator(mode="after")
    def _launcher_names_the_image(self) -> Self:
        launcher = self.container_launcher
        if launcher is not None and not any("{image}" in arg for arg in launcher):
            raise ValueError("container_launcher must contain an '{image}' placeholder")
        return self


# -- monolithic backends ------------------------------------------------------


class ARCSettings(_BackendSettings):
    kind: Literal["arc"]
    endpoint: str
    # All of these together: a token for the channel, a proxy
    # for delegation, or both.
    auth: frozenset[Literal["token", "proxy"]] = frozenset({"token"})

    @model_validator(mode="after")
    def _auth_not_empty(self) -> Self:
        if not self.auth:
            raise ValueError("auth must name at least one of 'token', 'proxy'")
        return self


class HTCondorCESettings(_BackendSettings):
    kind: Literal["htcondor-ce"]
    endpoint: str
    # The token authenticates the channel; "proxy" adds an X.509 proxy to the
    # submit description for sites that account on VOMS attributes.
    auth: frozenset[Literal["token", "proxy"]] = frozenset({"token"})

    @model_validator(mode="after")
    def _token_required(self) -> Self:
        if "token" not in self.auth:
            raise ValueError(
                "an HTCondor-CE authenticates with a token; auth must include 'token'"
            )
        return self


# -- provider backends --------------------------------------------------------

_SECRET_NAMES = ("secret", "password", "token", "passphrase")
_SECRET_EXACT = ("key", "api_key", "access_key", "private_key")


class CloudSettings(_Model):
    """A cloud provider that boots one VM per job.

    There is no queue and no filesystem to collect from afterwards, so the
    queue, output limits and container launcher of the other families do not
    apply. The provider's API keys come from the credential provider, never
    from here.
    """

    kind: Literal["cloud"]
    endpoint: str  # the cloud's API endpoint, and its identity in job handles
    driver: str  # the driver library's name for this cloud, e.g. "openstack"
    # Extra, non-secret arguments for the driver, e.g. {"ex_tenant_name": "dirac"}.
    driver_options: Mapping[str, str] = {}
    vm_image: str  # an image ID, or "name:<image name>"
    vm_flavor: str  # a flavor ID, or "name:<flavor name>"
    vm_networks: Sequence[str] = ()
    vm_ssh_key: str | None = None  # the NAME of a key pair known to the cloud
    vm_max_lifetime_s: PositiveInt = 14 * 24 * 3600  # older VMs are removed

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


# -- composed backends --------------------------------------------------------


class LocalTransportSettings(_Model):
    kind: Literal["local"]


class SSHTransportSettings(_Model):
    kind: Literal["ssh"]
    host: str
    port: PositiveInt = 22
    user: str
    known_hosts: str  # host key verification is not optional
    auth: Literal["key", "password", "agent"] = "key"
    jump_host: str | None = None
    connect_timeout_s: PositiveInt = 30

    @model_validator(mode="after")
    def _known_hosts_not_empty(self) -> Self:
        if not self.known_hosts.strip():
            raise ValueError("known_hosts must hold the expected host keys")
        return self


class BatchSettings(_BackendSettings):
    kind: Literal["batch"]
    transport: Annotated[
        LocalTransportSettings | SSHTransportSettings, Discriminator("kind")
    ]
    scheduler: Literal["slurm", "htcondor", "direct"]


BackendSettings = Annotated[
    ARCSettings | HTCondorCESettings | CloudSettings | BatchSettings,
    Discriminator("kind"),
]


class FakeSettings(_Model):
    """The in-memory fake. Internal: no public configuration reaches it."""

    kind: Literal["fake"] = "fake"
    resource: str = "fake://local"


_SETTINGS: TypeAdapter[BackendSettings] = TypeAdapter(BackendSettings)


def resolve(config: BackendConfig) -> BackendSettings:
    """Turn the consumer's flat configuration into the settings of one backend.

    Raises:
        pydantic.ValidationError: A field is missing, invalid, or does not
            apply to the chosen kind.

    """
    data: dict[str, Any] = config.model_dump(exclude_none=True)
    if data["kind"] == "cloud":
        # Nothing to reshape: every field is the cloud's own, and any limit is
        # left where the consumer wrote it, to be refused by its own name.
        return _SETTINGS.validate_python(data)

    limits = {}
    for name in LIMIT_FIELDS:
        if name in data:
            limits[name] = data.pop(name)
    if limits:
        data["limits"] = limits

    if data["kind"] == "batch":
        # The transport's own fields, and the transport kind, move into one
        # nested object; anything left over is checked against the backend.
        transport = {}
        for name in SSH_FIELDS:
            if name in data:
                transport[name] = data.pop(name)
        if "transport" in data:
            transport["kind"] = data.pop("transport")
        if "auth" in data:
            auth = data.pop("auth")
            # An SSH connection authenticates one way, chosen here.
            transport["auth"] = next(iter(auth)) if len(auth) == 1 else sorted(auth)
        data["transport"] = transport

    return _SETTINGS.validate_python(data)
