"""What a backend asks its credential provider for, when, and what it accepts back.

``credential_requirements`` turns a configuration into what to ask for. This is
also the one place freshness is decided. A backend holds a ``CredentialCache``,
and before each operation asks it for that operation's credential: the provider
is asked only when there is none yet or it is about to expire. Where credentials
come from, and how they are renewed, stays the consumer's business, inside its
provider.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from intercede.auth.models import (
    BearerToken,
    Credential,
    CredentialRequirements,
    SSHPassword,
    SSHPrivateKey,
)
from intercede.auth.protocols import CredentialProvider
from intercede.config import (
    ARCConfig,
    BackendConfig,
    BatchSSHConfig,
    HTCondorCEConfig,
)
from intercede.exceptions import AuthenticationError, InterCEdeError

# What each configured SSH auth mode asks the credential provider for.
# "agent" is absent on purpose: an SSH agent holds the key, so nothing is asked.
AUTH_KINDS: dict[str, type[Credential]] = {
    "key": SSHPrivateKey,
    "password": SSHPassword,
}

# Ask again this long before a credential expires, so it cannot run out in the
# middle of an operation. A library default; whether a provider should be able
# to change it is still open.
DEFAULT_MARGIN = timedelta(minutes=5)


def credential_requirements(
    config: BackendConfig, scopes: frozenset[str] = frozenset()
) -> CredentialRequirements | None:
    """Say what a backend must ask its provider for.

    Args:
        config: The backend's validated configuration.
        scopes: The scopes of the operation about to run, for token-based
            backends; ignored by the others.

    Returns:
        What to ask for, or None when the backend needs no credential: it runs
        on this host, or an SSH agent holds the key.
    """
    if isinstance(config, ARCConfig | HTCondorCEConfig):
        # The endpoint is the resource, so a token is minted for this CE's audience.
        return CredentialRequirements(
            resource=config.endpoint,
            kind=BearerToken,
            scopes=scopes,
        )
    if isinstance(config, BatchSSHConfig) and config.auth != "agent":
        # The resource is the login, ``user@host:port``: a provider finds the key
        # or password by it, and the same host can take different users.
        return CredentialRequirements(
            resource=f"{config.user}@{config.host}:{config.port}",
            kind=AUTH_KINDS[config.auth],
        )
    return None


def is_stale(
    credential: Credential | None,
    requirements: CredentialRequirements,
    *,
    margin: timedelta = DEFAULT_MARGIN,
    now: datetime | None = None,
) -> bool:
    """Tell whether ``credential`` cannot serve ``requirements`` for ``margin`` more.

    A credential that never expires is never stale. Only the kind and the expiry
    are checked, not the scopes, since a token is opaque to interCEde: ``credential``
    must have been given for these same requirements, as ``CredentialCache`` ensures.

    Args:
        credential: The one the backend holds, or None before its first request.
        requirements: What the backend needs for the operation about to run.
        margin: How long the credential must stay valid from now.
        now: The current time; tests pass a fixed one.

    Returns:
        True when there is no credential yet, when it is of another kind than
        required, or when it expires within ``margin``.
    """
    if not isinstance(credential, requirements.kind):
        return True
    if credential.expires_at is None:
        return False
    return credential.expires_at - margin <= (now or datetime.now(UTC))


async def refresh_if_stale(
    current: Credential | None,
    requirements: CredentialRequirements,
    provider: CredentialProvider,
    *,
    margin: timedelta = DEFAULT_MARGIN,
    now: datetime | None = None,
) -> Credential:
    """Return ``current`` while it is fresh, otherwise a new one from ``provider``.

    Args:
        current: The credential the backend holds, or None before its first request.
        requirements: What the backend needs for the operation about to run.
        provider: The consumer's provider, asked only when ``current`` is stale.
        margin: How long the credential must stay valid from now.
        now: The current time; tests pass a fixed one.

    Returns:
        The credential to authenticate with: ``current``, or the provider's new one.

    Raises:
        AuthenticationError: The provider failed, returned something other than
            the required kind, or returned a credential that has already expired.
            Always a failure of the whole operation, never a per-job outcome.
    """
    if not is_stale(current, requirements, margin=margin, now=now):
        assert current is not None  # is_stale() is True for None
        return current

    try:
        fresh = await provider.get(requirements)
    except InterCEdeError:
        raise  # already typed, e.g. an AuthenticationError from the provider
    except Exception as exc:
        # Only the type: the provider's message may hold a token or a secret URL.
        # The exception itself stays available as __cause__.
        raise AuthenticationError(
            f"the credential provider failed for {requirements.resource} "
            f"with {type(exc).__name__}"
        ) from exc

    if not isinstance(fresh, requirements.kind):
        raise AuthenticationError(
            f"the credential provider returned a {type(fresh).__name__} "
            f"for {requirements.resource}, not a {requirements.kind.__name__}"
        )
    # Expiring within the margin is accepted (it is asked again next time);
    # already expired is not, since it cannot authenticate anything.
    if is_stale(fresh, requirements, margin=timedelta(0), now=now):
        raise AuthenticationError(
            f"the credential provider returned an expired credential "
            f"for {requirements.resource}"
        )
    return fresh


class CredentialCache:
    """Keeps one credential per requirements for one backend, so each scope gets its own token.

    The provider is asked again only when that credential is missing or about to expire.
    """

    def __init__(self, provider: CredentialProvider) -> None:
        self._provider = provider
        self._held: dict[CredentialRequirements, Credential] = {}

    async def fresh(self, requirements: CredentialRequirements) -> Credential:
        """Return the credential for these requirements, asking the provider if needed.

        Args:
            requirements: What the backend needs for the operation about to run.

        Returns:
            The credential to authenticate with.

        Raises:
            AuthenticationError: The provider failed, returned something other than
                the required kind, or returned a credential that has already expired.
        """
        credential = await refresh_if_stale(
            self._held.get(requirements), requirements, self._provider
        )
        self._held[requirements] = credential
        return credential

    def clear(self) -> None:
        """Forget every credential, as a backend does when it closes."""
        self._held.clear()
