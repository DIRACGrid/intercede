"""When a backend asks its provider again, and what it accepts back (IC-ADR-003 §3)."""

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
    SSHPrivateKey,
    StaticCredentials,
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


# --------------------------------------------------------------------------
# is_stale
# --------------------------------------------------------------------------


class TestIsStale:
    def test_no_credentials_yet(self):
        assert is_stale(None, TOKEN_ONLY, now=NOW)

    def test_empty_set(self):
        assert is_stale(CredentialSet(), TOKEN_ONLY, now=NOW)

    def test_never_expiring_credential_is_never_stale(self):
        assert not is_stale(CredentialSet([token()]), TOKEN_ONLY, now=NOW)

    def test_far_from_expiry(self):
        assert not is_stale(
            CredentialSet([token(timedelta(hours=1))]), TOKEN_ONLY, now=NOW
        )

    @pytest.mark.parametrize(
        ("expires_in", "stale"),
        [
            (DEFAULT_MARGIN + timedelta(seconds=1), False),  # just outside the margin
            (DEFAULT_MARGIN, True),  # exactly at the margin
            (DEFAULT_MARGIN - timedelta(seconds=1), True),  # inside the margin
            (timedelta(0), True),  # expiring now
            (-timedelta(hours=1), True),  # already expired
        ],
        ids=["outside", "at", "inside", "now", "expired"],
    )
    def test_margin_boundary(self, expires_in: timedelta, stale: bool):
        assert (
            is_stale(CredentialSet([token(expires_in)]), TOKEN_ONLY, now=NOW) is stale
        )

    def test_custom_margin(self):
        credentials = CredentialSet([token(timedelta(minutes=30))])
        assert not is_stale(credentials, TOKEN_ONLY, now=NOW)
        assert is_stale(credentials, TOKEN_ONLY, margin=timedelta(hours=1), now=NOW)

    def test_zero_margin_only_refuses_expired(self):
        assert not is_stale(
            CredentialSet([token(timedelta(seconds=1))]),
            TOKEN_ONLY,
            margin=timedelta(0),
            now=NOW,
        )

    def test_one_missing_kind_makes_the_set_stale(self):
        assert is_stale(CredentialSet([token()]), TOKEN_AND_PROXY, now=NOW)

    def test_one_expiring_kind_makes_the_set_stale(self):
        credentials = CredentialSet([token(), proxy(timedelta(minutes=1))])
        assert is_stale(credentials, TOKEN_AND_PROXY, now=NOW)

    def test_every_kind_fresh(self):
        credentials = CredentialSet(
            [token(timedelta(hours=1)), proxy(timedelta(days=1))]
        )
        assert not is_stale(credentials, TOKEN_AND_PROXY, now=NOW)

    def test_kinds_not_required_are_ignored(self):
        # An expired proxy does not matter to a backend that only needs a token.
        credentials = CredentialSet([token(), proxy(-timedelta(hours=1))])
        assert not is_stale(credentials, TOKEN_ONLY, now=NOW)

    def test_defaults_to_the_current_time(self):
        assert is_stale(CredentialSet([token(-timedelta(days=365 * 100))]), TOKEN_ONLY)
        long_lived = BearerToken(
            value="t", expires_at=datetime.now(UTC) + timedelta(days=1)
        )
        assert not is_stale(CredentialSet([long_lived]), TOKEN_ONLY)

    def test_margin_default(self):
        assert DEFAULT_MARGIN == timedelta(minutes=5)


# --------------------------------------------------------------------------
# refresh_if_stale
# --------------------------------------------------------------------------


class TestRefreshIfStale:
    async def test_fresh_set_is_kept_without_asking(self):
        current = CredentialSet([token(timedelta(hours=1))])
        provider = Recording()
        assert await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW) is current
        assert provider.asked == []

    async def test_first_use_asks_the_provider(self):
        fresh = CredentialSet([token(timedelta(hours=1))])
        provider = Recording(fresh)
        assert await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW) is fresh
        assert provider.asked == [TOKEN_ONLY]

    async def test_expiring_set_is_replaced(self):
        fresh = CredentialSet([token(timedelta(hours=1))])
        provider = Recording(fresh)
        current = CredentialSet([token(timedelta(minutes=1))])
        assert await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW) is fresh

    async def test_the_provider_receives_the_requirements_unchanged(self):
        scoped = TOKEN_ONLY.model_copy(update={"scopes": frozenset({"compute.read"})})
        provider = Recording(CredentialSet([token()]))
        await refresh_if_stale(None, scoped, provider, now=NOW)
        assert provider.asked[0].scopes == {"compute.read"}

    async def test_works_with_static_credentials(self):
        key = SSHPrivateKey(pem=b"k")
        requirements = CredentialRequirements(
            resource="dirac@batch:22", kinds=frozenset({SSHPrivateKey})
        )
        async with StaticCredentials([key, token()]) as provider:
            supplied = await refresh_if_stale(None, requirements, provider)
        assert supplied.get(SSHPrivateKey) is key

    async def test_short_lived_credentials_are_accepted(self):
        # Inside the margin but not expired: usable now, asked again next time.
        fresh = CredentialSet([token(timedelta(minutes=1))])
        assert (
            await refresh_if_stale(None, TOKEN_ONLY, Recording(fresh), now=NOW) is fresh
        )

    async def test_asked_again_on_every_call_while_stale(self):
        short = CredentialSet([token(timedelta(minutes=1))])
        provider = Recording(short)
        current = await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)
        await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW)
        assert len(provider.asked) == 2


class TestProviderFailures:
    """Every failure is a whole-operation AuthenticationError (IC-ADR-003 §3)."""

    async def test_provider_raising_is_an_authentication_error(self):
        provider = Recording(error=RuntimeError("token service down"))
        with pytest.raises(AuthenticationError, match="token service down") as exc_info:
            await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "ce:9619" in str(exc_info.value)

    @pytest.mark.parametrize(
        "error", [AuthenticationError("denied"), InterCEdeError("x")]
    )
    async def test_typed_errors_pass_through_unchanged(self, error: InterCEdeError):
        with pytest.raises(type(error)) as exc_info:
            await refresh_if_stale(None, TOKEN_ONLY, Recording(error=error), now=NOW)
        assert exc_info.value is error

    async def test_cancellation_is_not_swallowed(self):
        import asyncio

        with pytest.raises(asyncio.CancelledError):
            await refresh_if_stale(
                None, TOKEN_ONLY, Recording(error=asyncio.CancelledError()), now=NOW
            )

    @pytest.mark.parametrize("result", [None, "token", [token()], {"value": "t"}])
    async def test_wrong_return_type(self, result: Any):
        with pytest.raises(AuthenticationError, match="not a CredentialSet"):
            await refresh_if_stale(None, TOKEN_ONLY, Recording(result), now=NOW)

    async def test_missing_kind(self):
        provider = Recording(CredentialSet([token()]))
        with pytest.raises(AuthenticationError, match="no X509Proxy"):
            await refresh_if_stale(None, TOKEN_AND_PROXY, provider, now=NOW)

    async def test_every_missing_kind_is_named(self):
        provider = Recording(CredentialSet())
        with pytest.raises(AuthenticationError, match="BearerToken, X509Proxy"):
            await refresh_if_stale(None, TOKEN_AND_PROXY, provider, now=NOW)

    @pytest.mark.parametrize("expires_in", [timedelta(0), -timedelta(minutes=1)])
    async def test_expired_credentials(self, expires_in: timedelta):
        provider = Recording(CredentialSet([token(expires_in)]))
        with pytest.raises(AuthenticationError, match="expired"):
            await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)

    async def test_a_failure_keeps_nothing(self):
        # The caller still holds its old set; nothing half-refreshed leaks out.
        current = CredentialSet([token(timedelta(minutes=1))])
        provider = Recording(error=RuntimeError("down"))
        with pytest.raises(AuthenticationError):
            await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW)
        assert current.get(BearerToken) is not None
