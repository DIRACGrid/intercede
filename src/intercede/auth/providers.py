"""Ready-made credential providers."""

from __future__ import annotations

from collections.abc import Iterable
from types import TracebackType
from typing import Self

from intercede.auth.models import Credential, CredentialRequirements, CredentialSet
from intercede.auth.protocols import CredentialProvider

__all__ = ["StaticCredentials"]


class StaticCredentials:
    """A provider holding fixed credentials: files read once, or fixed values.

    For standalone use, tests and the integration stacks, where nothing issues
    or renews credentials. Each backend receives only the kinds it asks for,
    never the whole set. It holds nothing to release, so closing does nothing,
    but it is still a context manager, like every provider::

        async with StaticCredentials([SSHPrivateKey(pem=key)]) as credentials:
            backend = registry.backend(config, credentials)
    """

    def __init__(self, credentials: CredentialSet | Iterable[Credential] = ()) -> None:
        # Validated now, so a duplicate kind or plain data fails at construction.
        self._credentials = (
            credentials
            if isinstance(credentials, CredentialSet)
            else CredentialSet.model_validate(tuple(credentials))
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def get(self, requirements: CredentialRequirements) -> CredentialSet:
        """Return the held credentials of the requested kinds."""
        return CredentialSet(
            tuple(
                credential
                for credential in self._credentials.root
                if type(credential) in requirements.kinds
            )
        )


_provider: type[CredentialProvider] = StaticCredentials
