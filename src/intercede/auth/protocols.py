"""The public protocol for a ``CredentialProvider``."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from intercede.auth.models import Credential, CredentialRequirements

__all__ = ["CredentialProvider"]


@runtime_checkable
class CredentialProvider(Protocol):
    """The protocol any consumer's own credential provider must satisfy.

    The consumer creates the provider, shares it between backends and closes
    it: interCEde never closes a provider, because it did not create it. A
    backend calls ``get`` only when its current credential is missing or
    close to expiry, so the consumer decides where credentials come from and
    interCEde decides when to ask.
    """

    async def get(self, requirements: CredentialRequirements) -> Credential:
        """Return the credential a backend needs.

        Args:
            requirements: The resource, the kind of credential it takes, and
                the scopes of the operation about to run.

        Returns:
            One credential of ``requirements.kind``.

        Raises:
            Exception: Anything raised here fails the whole operation: the
                backend reports it as an ``AuthenticationError``, never as a
                per-job outcome.
        """
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
