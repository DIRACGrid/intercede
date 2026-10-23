"""Backend configuration: how a consumer says which backend it wants.

One flat model describes every backend. ``kind`` picks the backend; a ``batch``
backend also picks a ``transport`` and a ``scheduler``. Only the fields that
apply to that choice may be set, and the model is checked when it is built, so
a mistake is reported here rather than at the first submit.

Configuration describes the resource and is never secret: keys, passwords,
tokens and proxies come from the credential provider at run time.
The kinds are a closed set: they list what interCEde ships.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import PositiveInt, ValidationError, model_validator

from intercede._backends.config import resolve
from intercede.models import _Model

__all__ = ["BackendConfig"]


class BackendConfig(_Model):
    """Which backend to use, and how to reach it.

    Examples, as TOML::

        kind = "arc"
        endpoint = "https://arc.example.org:443"
        auth = ["token", "proxy"]

        kind = "batch"
        transport = "ssh"
        scheduler = "slurm"
        host = "batch.example.org"
        user = "dirac"
        known_hosts = "batch.example.org ssh-ed25519 AAAA..."

        kind = "cloud"
        endpoint = "https://cloud.example.org:5000"
        driver = "openstack"
        vm_image = "name:alma9-pilot"
        vm_flavor = "name:m1.large"

    Which fields apply:

    ============================  ==========================================
    ``kind``                      fields
    ============================  ==========================================
    ``arc``, ``htcondor-ce``      ``endpoint``, ``auth`` (``token``, ``proxy``)
    ``cloud``                     ``endpoint``, ``driver``, ``vm_image``,
                                  ``vm_flavor``, ``driver_options``,
                                  ``vm_networks``, ``vm_ssh_key``,
                                  ``vm_max_lifetime_s``
    ``batch`` + ``local``         ``scheduler``
    ``batch`` + ``ssh``           ``scheduler``, ``host``, ``user``,
                                  ``known_hosts``, ``port``, ``jump_host``,
                                  ``connect_timeout_s``, ``auth`` (one of
                                  ``key``, ``password``, ``agent``)
    all but ``cloud``             ``queue``, ``container_launcher``,
                                  ``max_output_bytes``, ``max_output_files``,
                                  ``transfer_timeout_s``
    ============================  ==========================================
    """

    # What to use. ``kind`` is also what ``JobHandle.backend`` records.
    # Three families: grid CEs that schedule by themselves
    # ("arc", "htcondor-ce"), a cloud provider that boots a VM per job
    # ("cloud"), and a batch system reached through a transport ("batch").
    kind: Literal["arc", "htcondor-ce", "cloud", "batch"]
    transport: Literal["local", "ssh"] | None = None
    scheduler: Literal["slurm", "htcondor", "direct"] | None = None

    # Where it is.
    # A CE or cloud API: "https://arc.example.org:443", "ce.example.org:9619"
    endpoint: str | None = None
    host: str | None = None  # an SSH host
    port: PositiveInt | None = None
    user: str | None = None
    known_hosts: str | None = None  # expected host keys; verification is not optional
    jump_host: str | None = None  # "[user@]host[:port]"
    connect_timeout_s: PositiveInt | None = None

    # Which VM a cloud boots. The cloud's API keys come from the credential
    # provider, so driver_options holds only non-secret driver arguments.
    driver: str | None = (
        None  # the driver library's name for the cloud, e.g. "openstack"
    )
    driver_options: Mapping[str, str] | None = None
    vm_image: str | None = None  # an image ID, or "name:<image name>"
    vm_flavor: str | None = None  # a flavor ID, or "name:<flavor name>"
    vm_networks: Sequence[str] | None = None
    vm_ssh_key: str | None = None  # the NAME of a key pair known to the cloud
    vm_max_lifetime_s: PositiveInt | None = None

    # How to authenticate: which credentials the provider must supply, never
    # the credentials themselves. Defaults to a token for a CE, a key for SSH.
    auth: frozenset[str] | None = None

    # How to run jobs there.
    queue: str | None = None  # used when the spec does not name one
    # An argv template wrapping the executable in a container, e.g.
    # ["apptainer", "exec", "--bind", "/cvmfs", "{image}"].
    container_launcher: Sequence[str] | None = None

    # Deployment policy for output collection; defaults apply when unset.
    max_output_bytes: PositiveInt | None = None
    max_output_files: PositiveInt | None = None
    transfer_timeout_s: PositiveInt | None = None

    @model_validator(mode="after")
    def _fits_the_backend(self) -> Self:
        try:
            resolve(self)
        except ValidationError as exc:
            raise ValueError(_describe(self, exc)) from None
        return self


def _describe(config: BackendConfig, exc: ValidationError) -> str:
    """Report problems by the flat field names the consumer wrote."""
    fields = set(BackendConfig.model_fields)
    lines = []
    for error in exc.errors():
        named = [part for part in error["loc"] if part in fields]
        prefix = f"{named[-1]}: " if named else ""
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            message = f"does not apply to {_choice(config)}"
        elif error["type"] in ("missing", "union_tag_not_found"):
            message = f"required for {_choice(config)}"
        lines.append(prefix + message)
    return "; ".join(lines)


def _choice(config: BackendConfig) -> str:
    if config.kind != "batch":
        return f"kind={config.kind!r}"
    return f"kind='batch' with transport={config.transport!r}"
