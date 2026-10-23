"""Turn a backend configuration into a backend.

The registry is closed: the tables below list every backend, transport and
scheduler interCEde ships, and nothing outside the library can add to them.
Each entry names its class as ``"module:Class"`` and is imported only when a
configuration asks for it, so installing interCEde never imports every backend.

Every registered class provides a ``from_settings`` classmethod, which is how
the registry builds it from the validated settings of ``_backends.config``.
What it builds is checked against the relevant protocol before it is returned,
so a broken entry fails here, with a clear error, and not at the first call.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from importlib import import_module
from typing import Any, get_args

from pydantic import ValidationError

from intercede._backends.batch.protocols import Scheduler, Transport
from intercede._backends.config import (
    BackendSettings,
    BatchSettings,
    FakeSettings,
    resolve,
)
from intercede.config import BackendConfig
from intercede.exceptions import ConfigurationError, RegistryError
from intercede.protocols import JobBackend

__all__ = ["available", "backend"]

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


def backend(config: BackendConfig | Mapping[str, Any]) -> JobBackend:
    """Build the backend a configuration describes.

    The backend is returned unopened: use it as ``async with registry.backend(config) as ce``.
    Construction does no I/O, so building a backend for an unreachable resource succeeds.

    Args:
        config: A ``BackendConfig``, or the same data as a mapping, for example
            a parsed TOML table.

    Returns:
        The backend, not yet opened.

    Raises:
        ConfigurationError: The configuration is invalid.
        RegistryError: The configuration is valid, but this version of
            interCEde does not provide what it asks for.

    """
    if not isinstance(config, BackendConfig):
        try:
            config = BackendConfig.model_validate(config)
        except ValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
    return _build(resolve(config))


def available() -> dict[str, tuple[str, ...]]:
    """List what a configuration can ask for in this version, per group.

    Returns:
        The names per group, sorted, for tooling. For example
        ``{"backend": ("arc", "batch"), "transport": ("ssh",), ...}``.

    """
    public = {
        "backend": _choices(BackendConfig.model_fields["kind"].annotation),
        "transport": _choices(BackendConfig.model_fields["transport"].annotation),
        "scheduler": _choices(BackendConfig.model_fields["scheduler"].annotation),
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
    """Collect the string values of a Literal, also inside ``Literal[...] | None``."""
    values: set[str] = set()
    for arg in get_args(annotation):
        if isinstance(arg, str):
            values.add(arg)
        else:
            values |= _choices(arg)
    return values


def _build(settings: BackendSettings | FakeSettings) -> JobBackend:
    """Build a backend from validated settings. Internal: the test suites call it directly."""
    if not isinstance(settings, BatchSettings):
        built = _load("backend", settings.kind).from_settings(settings)
        result: JobBackend = _checked("backend", settings.kind, built, JobBackend)
        return result

    transport_kind = settings.transport.kind
    transport: Transport = _checked(
        "transport",
        transport_kind,
        _load("transport", transport_kind).from_settings(settings.transport),
        Transport,
    )
    scheduler: Scheduler = _checked(
        "scheduler",
        settings.scheduler,
        _load("scheduler", settings.scheduler).from_settings(settings),
        Scheduler,
    )
    built = _load("backend", "batch").from_settings(
        settings, transport=transport, scheduler=scheduler
    )
    composed: JobBackend = _checked("backend", "batch", built, JobBackend)
    return composed


@cache
def _load(group: str, name: str) -> Any:
    """Import the class registered under ``name``, and only that one."""
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
    """Refuse an object that does not satisfy its protocol."""
    if not isinstance(built, protocol):
        raise RegistryError(
            f"{group} {name!r} built a {type(built).__name__}, which is not a {protocol.__name__}"
        )
    return built
