"""Turn a backend configuration into a backend.

The registry is closed: the tables below list every backend, transport and
scheduler interCEde ships, and nothing outside the library can add to them.
Each entry names its class as ``"module:Class"`` and is imported only when a
configuration asks for it, so installing interCEde never imports every backend.

Every registered class provides a ``from_settings`` classmethod, which is how
the registry builds it from a validated configuration model.
The parts that authenticate (single-class backends and transports) also
receive the consumer's credential provider; a scheduler only builds commands.
What it builds is checked against the relevant protocol before it is returned,
so a broken entry fails here, with a clear error, and not at the first call.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from importlib import import_module
from typing import Any, get_args

from intercede._backends.batch.protocols import Scheduler, Transport
from intercede._backends.config import FakeConfig
from intercede.auth.protocols import CredentialProvider
from intercede.config import (
    _MODELS as _CONFIG_MODELS,
)
from intercede.config import (
    BackendConfig,
    BatchLocalConfig,
    BatchSSHConfig,
    validate_config,
)
from intercede.exceptions import ConfigurationError, RegistryError
from intercede.protocols import JobBackend

__all__ = ["available", "get_backend"]

# Name -> "module:Class". A name is listed only once its class exists; a valid
# configuration asking for anything else gets a RegistryError naming what is
# available. Monolithic backends ("arc", "htcondor-ce") and provider backends
# ("cloud") are built from their own settings alone. "batch" is the composed
# backend: it is built from one transport and one scheduler, so combinations
# never need classes of their own.
_BACKENDS: dict[str, str] = {
    "fake": "intercede._backends.fake:FakeBackend",  # internal, for the test suites
}
_TRANSPORTS: dict[str, str] = {}
_SCHEDULERS: dict[str, str] = {}

_GROUPS: dict[str, dict[str, str]] = {
    "backend": _BACKENDS,
    "transport": _TRANSPORTS,
    "scheduler": _SCHEDULERS,
}


def get_backend(
    config: BackendConfig | Mapping[str, Any], credentials: CredentialProvider
) -> JobBackend:
    """Build the backend a configuration describes.

    The backend is returned unopened: use it as
    ``async with registry.get_backend(config, credentials) as ce``. Construction
    does no I/O and asks the provider for nothing, so building a backend for an
    unreachable resource succeeds. The backend asks for credentials when it
    first needs them, and again when they are about to expire.

    Args:
        config: A ``BackendConfig``, or the same data as a mapping, for example
            a parsed TOML table.
        credentials: The consumer's provider. The consumer opens and closes it;
            interCEde never does. One provider is normally shared by every
            backend a task drives.

    Returns:
        The backend, not yet opened.

    Raises:
        ConfigurationError: The configuration is invalid, or ``credentials``
            is not a ``CredentialProvider``.
        RegistryError: The configuration is valid, but this version of
            interCEde does not provide what it asks for.
    """
    validated = validate_config(config)
    if not isinstance(credentials, CredentialProvider):
        raise ConfigurationError(
            f"credentials must be a CredentialProvider, not a {type(credentials).__name__}"
        )
    return _build(validated, credentials)


def available() -> dict[str, tuple[str, ...]]:
    """List what a configuration can ask for in this version, per group.

    Returns:
        The names per group, sorted, for tooling. For example
        ``{"backend": ("arc", "batch"), "transport": ("ssh",), ...}``.
    """
    batch = (BatchLocalConfig, BatchSSHConfig)
    public = {
        "backend": set().union(
            *(
                _choices(model.model_fields["kind"].annotation)
                for model in _CONFIG_MODELS.values()
            )
        ),
        "transport": set().union(
            *(_choices(model.model_fields["transport"].annotation) for model in batch)
        ),
        "scheduler": _choices(BatchLocalConfig.model_fields["scheduler"].annotation),
    }
    result = {}
    for group, table in _GROUPS.items():
        names = []
        for name in table:
            if name in public[group]:
                names.append(name)
        result[group] = tuple(sorted(names))

    return result


def _choices(annotation: Any) -> set[str]:
    """Collect the string values of a Literal, also inside ``Literal[...] | None``.

    Args:
        annotation: A field's type annotation.

    Returns:
        Every string the annotation allows; empty when it is not a Literal.
    """
    values: set[str] = set()
    for arg in get_args(annotation):
        if isinstance(arg, str):
            values.add(arg)
        else:
            values |= _choices(arg)
    return values


def _build(
    config: BackendConfig | FakeConfig, credentials: CredentialProvider
) -> JobBackend:
    """Build a backend from a validated configuration. Internal: the test suites call it directly.

    Args:
        config: The validated configuration of one backend, or the fake's settings.
        credentials: The consumer's provider, for the parts that authenticate.

    Returns:
        The backend, not yet opened. A ``batch`` backend is composed of the
        transport and the scheduler its configuration names; both receive the
        whole configuration.

    Raises:
        RegistryError: Something the configuration names is not available, or
            what was built does not satisfy its protocol.
    """
    if not isinstance(config, BatchLocalConfig | BatchSSHConfig):
        built = _load("backend", config.kind).from_settings(config, credentials)
        result: JobBackend = _checked("backend", config.kind, built, JobBackend)
        return result

    transport: Transport = _checked(
        "transport",
        config.transport,
        _load("transport", config.transport).from_settings(config, credentials),
        Transport,
    )
    scheduler: Scheduler = _checked(
        "scheduler",
        config.scheduler,
        _load("scheduler", config.scheduler).from_settings(config),
        Scheduler,
    )
    built = _load("backend", "batch").from_settings(
        config, transport=transport, scheduler=scheduler
    )
    composed: JobBackend = _checked("backend", "batch", built, JobBackend)
    return composed


@cache
def _load(group: str, name: str) -> Any:
    """Import the class registered under ``name``, and only that one.

    Args:
        group: ``"backend"``, ``"transport"`` or ``"scheduler"``.
        name: The name the class is registered under, such as ``"ssh"``.

    Returns:
        The registered class. It is cached, so each entry is imported once.

    Raises:
        RegistryError: Nothing is registered under ``name``, or the entry
            cannot be imported or has no ``from_settings``.
    """
    table = _GROUPS[group]
    if name not in table:
        offered = ", ".join(available()[group]) or "none yet"
        raise RegistryError(
            f"{group} {name!r} is not available in this version of interCEde (available: {offered})"
        )

    target = table[name]
    module_name, _, attribute = target.partition(":")
    try:
        cls = getattr(import_module(module_name), attribute)
    except (
        ImportError,
        AttributeError,
        ValueError,
    ) as exc:  # ValueError: empty module name
        raise RegistryError(
            f"{group} {name!r} is registered as {target!r}, which cannot be imported"
        ) from exc
    if not callable(getattr(cls, "from_settings", None)):
        raise RegistryError(
            f"{group} {name!r} ({target}) has no from_settings() to build it with"
        )
    return cls


def _checked(group: str, name: str, built: Any, protocol: type) -> Any:
    """Refuse an object that does not satisfy its protocol.

    Args:
        group: The registry group the object was built from, for the error.
        name: The registered name it was built from, for the error.
        built: What the registered class built.
        protocol: The runtime-checkable protocol it must satisfy.

    Returns:
        ``built`` itself, once checked.

    Raises:
        RegistryError: ``built`` does not satisfy ``protocol``.
    """
    if not isinstance(built, protocol):
        raise RegistryError(
            f"{group} {name!r} built a {type(built).__name__}, which is not a {protocol.__name__}"
        )
    return built
