"""When a backend asks its credential provider, and what it accepts back.

This is the one place freshness is decided. A backend keeps the set it was
last given, privately, and before each operation calls ``refresh_if_stale``:
the provider is asked only when that set is missing a required kind or one of
its credentials is about to expire. Where credentials come from, and how they
are renewed, stays the consumer's business, inside its provider.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from intercede.auth.models import CredentialRequirements, CredentialSet
from intercede.auth.protocols import CredentialProvider
from intercede.exceptions import AuthenticationError, InterCEdeError

# Ask again this long before a credential expires, so it cannot run out in the
# middle of an operation. A library default; whether a provider should be able
# to change it is still open.
DEFAULT_MARGIN = timedelta(minutes=5)


def is_stale(
    credentials: CredentialSet | None,
    requirements: CredentialRequirements,
    *,
    margin: timedelta = DEFAULT_MARGIN,
    now: datetime | None = None,
) -> bool:
    """Tell whether ``credentials`` cannot serve ``requirements`` for ``margin`` more.

    Stale when there are no credentials yet, when a required kind is missing,
    or when a required credential expires within ``margin``. A credential that
    never expires is never stale.
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
        raise AuthenticationError(
            f"the credential provider failed for {requirements.resource}: {exc}"
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
