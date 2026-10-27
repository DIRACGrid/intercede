"""What a backend asks its credential provider for, when, and what it accepts back.

``credential_requirements`` turns a configuration into what to ask for. This is
also the one place freshness is decided. A backend keeps the set it was
last given, privately, and before each operation calls ``refresh_if_stale``:
the provider is asked only when that set is missing a required kind or one of
its credentials is about to expire. Where credentials come from, and how they
are renewed, stays the consumer's business, inside its provider.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from intercede.auth.models import (
    BearerToken,
    Credential,
    CredentialRequirements,
    CredentialSet,
    SSHPassword,
    SSHPrivateKey,
    X509Proxy,
)
from intercede.auth.protocols import CredentialProvider
from intercede.config import (
    ARCConfig,
    BackendConfig,
    BatchSSHConfig,
    HTCondorCEConfig,
)
from intercede.exceptions import AuthenticationError, InterCEdeError

# What each configured auth mode asks the credential provider for.
# "agent" is absent on purpose: an SSH agent holds the key, so nothing is asked.
AUTH_KINDS: dict[str, type[Credential]] = {
    "token": BearerToken,
    "proxy": X509Proxy,
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
            kinds=frozenset(AUTH_KINDS[mode] for mode in config.auth),
            scopes=scopes,
        )
    if isinstance(config, BatchSSHConfig) and config.auth != "agent":
        # The resource is the login, ``user@host:port``: a provider finds the key
        # or password by it, and the same host can take different users.
        return CredentialRequirements(
            resource=f"{config.user}@{config.host}:{config.port}",
            kinds=frozenset({AUTH_KINDS[config.auth]}),
        )
    return None


def is_stale(
    credentials: CredentialSet | None,
    requirements: CredentialRequirements,
    *,
    margin: timedelta = DEFAULT_MARGIN,
    now: datetime | None = None,
) -> bool:
    """Tell whether ``credentials`` cannot serve ``requirements`` for ``margin`` more.

    A credential that never expires is never stale.

    Args:
        credentials: The set the backend holds, or None before its first request.
        requirements: What the backend needs for the operation about to run.
        margin: How long the credentials must stay valid from now.
        now: The current time; tests pass a fixed one.

    Returns:
        True when there are no credentials yet, when a required kind is
        missing, or when a required credential expires within ``margin``.
    """
    if credentials is None:
        return True
    moment = now or datetime.now(UTC)
    for kind in requirements.kinds:
        credential = credentials.get(kind)
        if credential is None:
            return True
        if (
            credential.expires_at is not None
            and credential.expires_at - margin <= moment
        ):
            return True
    return False


async def refresh_if_stale(
    current: CredentialSet | None,
    requirements: CredentialRequirements,
    provider: CredentialProvider,
    *,
    margin: timedelta = DEFAULT_MARGIN,
    now: datetime | None = None,
) -> CredentialSet:
    """Return ``current`` while it is fresh, otherwise a new set from ``provider``.

    Args:
        current: The set the backend holds, or None before its first request.
        requirements: What the backend needs for the operation about to run.
        provider: The consumer's provider, asked only when ``current`` is stale.
        margin: How long the credentials must stay valid from now.
        now: The current time; tests pass a fixed one.

    Returns:
        The set to authenticate with: ``current``, or the provider's new set.

    Raises:
        AuthenticationError: The provider failed, left out a required kind, or
            returned a credential that has already expired. Always a failure of
            the whole operation, never a per-job outcome.
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

    if not isinstance(fresh, CredentialSet):
        raise AuthenticationError(
            f"the credential provider returned a {type(fresh).__name__} "
            f"for {requirements.resource}, not a CredentialSet"
        )
    missing = []
    for kind in requirements.kinds:
        if fresh.get(kind) is None:
            missing.append(kind.__name__)
    if missing:
        raise AuthenticationError(
            # Sorted: kinds is a set, whose order changes from run to run.
            f"the credential provider returned no {', '.join(sorted(missing))} "
            f"for {requirements.resource}"
        )
    # Expiring within the margin is accepted (it is asked again next time);
    # already expired is not, since it cannot authenticate anything.
    if is_stale(fresh, requirements, margin=timedelta(0), now=now):
        raise AuthenticationError(
            f"the credential provider returned expired credentials "
            f"for {requirements.resource}"
        )
    return fresh
