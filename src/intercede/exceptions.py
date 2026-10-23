"""Exceptions for failures of a whole operation.

Anything that can fail for some jobs and succeed for others is reported in the
returned map instead, never raised.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "ConfigurationError",
    "InterCEdeError",
    "RegistryError",
    "ResourceUnavailableError",
    "SpecificationRejectedError",
]


class InterCEdeError(Exception):
    """Root of every exception InterCEde raises."""


class ConfigurationError(InterCEdeError):
    """A backend configuration is invalid."""


class AuthenticationError(InterCEdeError):
    """The resource rejected the credentials."""


class ResourceUnavailableError(InterCEdeError):
    """The resource could not be reached, or failed the whole operation."""


class SpecificationRejectedError(InterCEdeError):
    """The backend cannot honour this specification, so no copy was submitted."""


class RegistryError(InterCEdeError):
    """The registry cannot provide what a valid configuration asks for.

    Either this version of interCEde does not ship it, or its registry entry is
    broken: it cannot be imported, or what it builds does not satisfy its protocol.
    """
