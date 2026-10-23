"""When a backend asks its provider again, and what it accepts back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self

import pytest

from intercede import AuthenticationError, InterCEdeError
from intercede._backends.credentials import DEFAULT_MARGIN, is_stale, refresh_if_stale
from intercede.auth import (
    BearerToken,
    CredentialRequirements,
    CredentialSet,
    X509Proxy,
)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
TOKEN_ONLY = CredentialRequirements(resource="ce:9619", kinds=frozenset({BearerToken}))
TOKEN_AND_PROXY = CredentialRequirements(
    resource="ce:9619", kinds=frozenset({BearerToken, X509Proxy})
)


def token(expires_in: timedelta | None = None) -> BearerToken:
    return BearerToken(
        value="t", expires_at=None if expires_in is None else NOW + expires_in
    )


def proxy(expires_in: timedelta | None = None) -> X509Proxy:
    return X509Proxy(
        pem=b"p", expires_at=None if expires_in is None else NOW + expires_in
    )


class Recording:
    """A provider that returns whatever the test gives it, and counts calls."""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.asked: list[CredentialRequirements] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def get(self, requirements: CredentialRequirements) -> Any:
        self.asked.append(requirements)
        if self.error is not None:
            raise self.error
        return self.result


class TestIsStale:
    @pytest.mark.parametrize(
        ("credentials", "stale"),
        [
            (None, True),  # nothing asked for yet
            (CredentialSet([token()]), False),  # never expires
            (CredentialSet([token(DEFAULT_MARGIN + timedelta(seconds=1))]), False),
            (CredentialSet([token(DEFAULT_MARGIN)]), True),  # inside the margin
            (CredentialSet([token(-timedelta(hours=1))]), True),  # expired
        ],
        ids=["none", "never-expires", "outside-margin", "at-margin", "expired"],
    )
    def test_one_kind(self, credentials: CredentialSet | None, stale: bool):
        assert is_stale(credentials, TOKEN_ONLY, now=NOW) is stale

    def test_one_missing_or_expiring_kind_makes_the_set_stale(self):
        assert is_stale(CredentialSet([token()]), TOKEN_AND_PROXY, now=NOW)
        expiring = CredentialSet([token(), proxy(timedelta(minutes=1))])
        assert is_stale(expiring, TOKEN_AND_PROXY, now=NOW)

    def test_kinds_not_required_are_ignored(self):
        # An expired proxy does not matter to a backend that only needs a token.
        credentials = CredentialSet([token(), proxy(-timedelta(hours=1))])
        assert not is_stale(credentials, TOKEN_ONLY, now=NOW)


class TestRefreshIfStale:
    async def test_fresh_set_is_kept_without_asking(self):
        current = CredentialSet([token(timedelta(hours=1))])
        provider = Recording()
        assert await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW) is current
        assert provider.asked == []

    async def test_stale_set_is_replaced(self):
        fresh = CredentialSet([token(timedelta(hours=1))])
        provider = Recording(fresh)
        current = CredentialSet([token(timedelta(minutes=1))])
        assert await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW) is fresh
        assert provider.asked == [TOKEN_ONLY]

    async def test_short_lived_credentials_are_accepted(self):
        # Inside the margin but not expired: usable now, asked again next time.
        fresh = CredentialSet([token(timedelta(minutes=1))])
        assert (
            await refresh_if_stale(None, TOKEN_ONLY, Recording(fresh), now=NOW) is fresh
        )


class TestProviderFailures:
    """Every failure is a whole-operation AuthenticationError."""

    async def test_provider_raising_is_an_authentication_error(self):
        provider = Recording(error=RuntimeError("token service down"))
        with pytest.raises(AuthenticationError, match="token service down") as exc_info:
            await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "ce:9619" in str(exc_info.value)

    async def test_our_own_errors_pass_through_unchanged(self):
        error = InterCEdeError("x")
        with pytest.raises(InterCEdeError) as exc_info:
            await refresh_if_stale(None, TOKEN_ONLY, Recording(error=error), now=NOW)
        assert exc_info.value is error

    @pytest.mark.parametrize("result", [None, [token()]])
    async def test_wrong_return_type(self, result: Any):
        with pytest.raises(AuthenticationError, match="not a CredentialSet"):
            await refresh_if_stale(None, TOKEN_ONLY, Recording(result), now=NOW)

    async def test_every_missing_kind_is_named(self):
        provider = Recording(CredentialSet())
        with pytest.raises(AuthenticationError, match="BearerToken, X509Proxy"):
            await refresh_if_stale(None, TOKEN_AND_PROXY, provider, now=NOW)

    async def test_expired_credentials(self):
        provider = Recording(CredentialSet([token(-timedelta(minutes=1))]))
        with pytest.raises(AuthenticationError, match="expired"):
            await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)
