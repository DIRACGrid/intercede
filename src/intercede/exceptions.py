"""Exceptions for failures of a whole operation (IC-ADR-001 §3).

Anything that can fail for some jobs and succeed for others is reported in the
returned map instead, never raised.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "ConfigurationError",
    "InterCEdeError",
    "ResourceUnavailableError",
    "SpecificationRejectedError",
]


class InterCEdeError(Exception):
    """Root of every exception InterCEde raises."""


class ConfigurationError(InterCEdeError):
    """A backend configuration is invalid."""


class AuthenticationError(InterCEdeError):
    """The resource rejected the credentials (IC-ADR-003 §3)."""


class ResourceUnavailableError(InterCEdeError):
    """The resource could not be reached, or failed the whole operation."""


class SpecificationRejectedError(InterCEdeError):
    """The backend cannot honour this specification, so no copy was submitted."""
