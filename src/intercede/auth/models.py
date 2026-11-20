"""The public credential models a consumer of interCEde can use.

Credentials are immutable values. Secret material is held in pydantic's secret
types, so a credential cannot be printed or logged by accident: ``repr``,
``str`` and JSON all show ``**********``, and the content is only readable on
purpose, with ``get_secret_value()``.
"""

from __future__ import annotations

from typing import Self

from pydantic import (
    AwareDatetime,
    ConfigDict,
    SecretBytes,
    SecretStr,
    model_validator,
)

from intercede.models import _Model

__all__ = [
    "BearerToken",
    "Credential",
    "CredentialRequirements",
    "SSHPassword",
    "SSHPrivateKey",
]


# --------------------------------------------------------------------------
# Credential values
# --------------------------------------------------------------------------


class Credential(_Model):
    """Base of every credential: the one field the freshness check needs.

    Not used on its own: a provider returns one of the concrete kinds below.
    """

    # Added to _Model's settings: a value given by mistake may be the secret
    # itself, so no error message repeats one.
    model_config = ConfigDict(hide_input_in_errors=True, use_attribute_docstrings=True)

    expires_at: AwareDatetime | None = None
    """When the credential stops working, with a timezone, so it can be compared
    with the current time. None means it never expires, as for SSH secrets."""


class BearerToken(Credential):
    """A token for the channel to a CE. Opaque to interCEde."""

    value: SecretStr
    """The token itself."""


class SSHPrivateKey(Credential):
    """An SSH private key: the key itself, never a path on some host."""

    pem: SecretBytes
    """The private key, PEM-encoded."""

    passphrase: SecretStr | None = None
    """The passphrase the key is encrypted with, or None if it is not."""


class SSHPassword(Credential):
    """A password for an SSH login."""

    value: SecretStr
    """The password itself."""


# --------------------------------------------------------------------------
# What a backend asks for
# --------------------------------------------------------------------------


class CredentialRequirements(_Model):
    """What a backend asks its provider for.

    The backend computes it from its own configuration and keeps it to itself:
    a consumer only meets it as the argument to its provider's ``get``.
    """

    model_config = ConfigDict(use_attribute_docstrings=True)

    resource: str
    """Which backend instance is asking: the endpoint it talks to, the same
    string as ``JobHandle.resource``. For a token this is also the audience,
    in the form that CE expects."""

    kind: type[Credential]
    """The kind of credential needed: a backend authenticates with exactly one."""

    scopes: frozenset[str] = frozenset()
    """For a token, the scopes of the operation about to run; empty for the
    other kinds."""

    @model_validator(mode="after")
    def _concrete_kind(self) -> Self:
        if self.kind is Credential:
            raise ValueError("kind must be a concrete kind, not the Credential base")
        return self
