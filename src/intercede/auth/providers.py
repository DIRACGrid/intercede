"""Ready-made credential providers."""

from __future__ import annotations

from collections.abc import Iterable
from types import TracebackType
from typing import Self

from intercede.auth.models import Credential, CredentialRequirements
from intercede.auth.protocols import CredentialProvider
from intercede.exceptions import AuthenticationError

__all__ = ["StaticCredentials"]


class StaticCredentials:
    """A provider holding fixed credentials: files read once, or fixed values.

    For standalone use, tests and the integration stacks, where nothing issues
    or renews credentials. It holds at most one credential of each kind, since
    one provider can serve several backends, and each backend receives only the
    one it asks for. It holds nothing to release, so closing does nothing, but
    it is still a context manager, like every provider::

        async with StaticCredentials([SSHPrivateKey(pem=key)]) as credentials:
            backend = registry.get_backend(config, credentials)
    """

    def __init__(self, credentials: Iterable[Credential] = ()) -> None:
        # Validated now, so a duplicate kind or plain data fails at construction.
        self._credentials: dict[type[Credential], Credential] = {}
        for credential in credentials:
            # A plain dict is ambiguous: {"value": ...} fits both a BearerToken
            # and an SSHPassword. Guessing the kind of a secret is never right.
            # Never repeat the item in the message: it may be the secret itself.
            if not isinstance(credential, Credential) or type(credential) is Credential:
                raise ValueError(
                    "StaticCredentials holds concrete credential objects, "
                    "e.g. BearerToken(value=...), not plain data"
                )
            kind = type(credential)
            if kind in self._credentials:
                raise ValueError(f"more than one credential of kind {kind.__name__}")
            self._credentials[kind] = credential

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def get(self, requirements: CredentialRequirements) -> Credential:
        """Return the held credential of the requested kind.

        Args:
            requirements: What the backend asks for.

        Returns:
            The held credential of ``requirements.kind``.

        Raises:
            AuthenticationError: It holds no credential of that kind.
        """
        credential = self._credentials.get(requirements.kind)
        if credential is None:
            raise AuthenticationError(
                f"no {requirements.kind.__name__} for {requirements.resource}"
            )
        return credential


_provider: type[CredentialProvider] = StaticCredentials
