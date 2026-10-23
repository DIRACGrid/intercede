"""The public credential models a consumer of interCEde can use.

Credentials are immutable values. Secret material is held in pydantic's secret
types, so a credential cannot be printed or logged by accident: ``repr``,
``str`` and JSON all show ``**********``, and the content is only readable on
purpose, with ``get_secret_value()``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Self, TypeVar

from pydantic import (
    AwareDatetime,
    ConfigDict,
    RootModel,
    SecretBytes,
    SecretStr,
    model_validator,
)

from intercede.models import _Model

__all__ = [
    "BearerToken",
    "Credential",
    "CredentialRequirements",
    "CredentialSet",
    "SSHPassword",
    "SSHPrivateKey",
    "X509Proxy",
]


# --------------------------------------------------------------------------
# Credential values
# --------------------------------------------------------------------------


class Credential(_Model):
    """Base of every credential: the one field the freshness check needs.

    Not used on its own: a provider returns one of the concrete kinds below.
    """

    # None means it never goes stale (SSH secrets). Must carry a timezone, so
    # it can be compared with the current time.
    expires_at: AwareDatetime | None = None


class BearerToken(Credential):
    """A token for the channel to a CE. Opaque to interCEde."""

    value: SecretStr


class X509Proxy(Credential):
    """An X.509 proxy: private key and certificate chain together, as PEM."""

    pem: SecretBytes


class SSHPrivateKey(Credential):
    """An SSH private key: the key itself, never a path on some host."""

    pem: SecretBytes
    passphrase: SecretStr | None = None


class SSHPassword(Credential):
    """A password for an SSH login."""

    value: SecretStr


AnyCredential = BearerToken | X509Proxy | SSHPrivateKey | SSHPassword
C = TypeVar("C", bound=Credential)


# --------------------------------------------------------------------------
# What a provider returns
# --------------------------------------------------------------------------


class CredentialSet(RootModel[tuple[AnyCredential, ...]]):
    """The credentials a provider returns: at most one of each kind.

    A set rather than a single value, because a backend can need several kinds
    together::

        CredentialSet([BearerToken(value=token), X509Proxy(pem=proxy)])
    """

    model_config = ConfigDict(frozen=True)

    root: tuple[AnyCredential, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _objects_only(cls, data: Any) -> Any:
        # A plain dict is ambiguous: {"value": ...} fits both a BearerToken and
        # an SSHPassword. Guessing the kind of a secret is never right.
        is_sequence = isinstance(data, list | tuple | set | frozenset)
        if is_sequence and any(not isinstance(item, Credential) for item in data):
            raise ValueError(
                "a CredentialSet holds credential objects, "
                "e.g. BearerToken(value=...), not plain data"
            )
        return data

    @model_validator(mode="after")
    def _one_per_kind(self) -> Self:
        counts = Counter(type(credential).__name__ for credential in self.root)
        duplicates = []

        for kind, count in counts.items():
            if count > 1:
                duplicates.append(kind)

        if duplicates:
            raise ValueError(f"more than one credential of kind {duplicates}")

        return self

    def get(self, kind: type[C]) -> C | None:
        """Return the credential of ``kind``, or None if the set has none."""
        for credential in self.root:
            if isinstance(credential, kind):
                return credential
        return None


# --------------------------------------------------------------------------
# What a backend asks for
# --------------------------------------------------------------------------


class CredentialRequirements(_Model):
    """What a backend asks its provider for.

    The backend computes it from its own configuration and keeps it to itself:
    a consumer only meets it as the argument to its provider's ``get``.
    """

    # Which backend instance is asking: the endpoint it talks to. For a token
    # this is also the audience, in the form that CE expects. The same string
    # as JobHandle.resource.
    resource: str
    # Needed TOGETHER, not a menu of alternatives.
    kinds: frozenset[type[Credential]]
    # For token kinds, the scopes for THIS operation; empty for the others.
    scopes: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _concrete_kinds(self) -> Self:
        if not self.kinds:
            # A backend that needs nothing (e.g. SSH through an agent) never
            # calls its provider, so empty requirements are always a mistake.
            raise ValueError("kinds must name at least one credential kind")
        if Credential in self.kinds:
            raise ValueError("kinds must name concrete kinds, not the Credential base")
        return self
