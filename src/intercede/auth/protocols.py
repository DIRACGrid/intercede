"""The public protocol for a ``CredentialProvider`` (IC-ADR-003 §3)."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from intercede.auth.models import CredentialRequirements, CredentialSet

__all__ = ["CredentialProvider"]


@runtime_checkable
class CredentialProvider(Protocol):
    """The protocol any consumer's own credential provider must satisfy.

    The consumer creates the provider, shares it between backends and closes
    it: interCEde never closes a provider, because it did not create it. A
    backend calls ``get`` only when its current credentials are missing or
    close to expiry, so the consumer decides where credentials come from and
    interCEde decides when to ask.
    """

    async def get(self, requirements: CredentialRequirements) -> CredentialSet:
        """Return the credentials ``requirements`` names, all of them together.

        Raising here is a whole-operation failure: the backend reports it as an
        ``AuthenticationError``, never as a per-job outcome.
        """
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
