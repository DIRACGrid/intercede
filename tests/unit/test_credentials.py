"""When a backend asks its provider again, and what it accepts back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self

import pytest

from intercede import AuthenticationError, InterCEdeError, validate_config
from intercede._backends.credentials import (
    DEFAULT_MARGIN,
    CredentialCache,
    credential_requirements,
    is_stale,
    refresh_if_stale,
)
from intercede.auth import (
    BearerToken,
    Credential,
    CredentialRequirements,
    SSHPassword,
    SSHPrivateKey,
)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
TOKEN_ONLY = CredentialRequirements(resource="ce:9619", kind=BearerToken)


def token(expires_in: timedelta | None = None) -> BearerToken:
    return BearerToken(
        value="t", expires_at=None if expires_in is None else NOW + expires_in
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
        ("credential", "stale"),
        [
            (None, True),  # nothing asked for yet
            (token(), False),  # never expires
            (token(DEFAULT_MARGIN + timedelta(seconds=1)), False),
            (token(DEFAULT_MARGIN), True),  # inside the margin
            (token(-timedelta(hours=1)), True),  # expired
            (SSHPassword(value="p"), True),  # not what is required
        ],
        ids=[
            "none",
            "never-expires",
            "outside-margin",
            "at-margin",
            "expired",
            "another-kind",
        ],
    )
    def test_one_credential(self, credential: Credential | None, stale: bool):
        assert is_stale(credential, TOKEN_ONLY, now=NOW) is stale


class TestRefreshIfStale:
    async def test_fresh_credential_is_kept_without_asking(self):
        current = token(timedelta(hours=1))
        provider = Recording()
        assert await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW) is current
        assert provider.asked == []

    async def test_stale_credential_is_replaced(self):
        fresh = token(timedelta(hours=1))
        provider = Recording(fresh)
        current = token(timedelta(minutes=1))
        assert await refresh_if_stale(current, TOKEN_ONLY, provider, now=NOW) is fresh
        assert provider.asked == [TOKEN_ONLY]

    async def test_short_lived_credentials_are_accepted(self):
        # Inside the margin but not expired: usable now, asked again next time.
        fresh = token(timedelta(minutes=1))
        assert (
            await refresh_if_stale(None, TOKEN_ONLY, Recording(fresh), now=NOW) is fresh
        )


class TestProviderFailures:
    """Every failure is a whole-operation AuthenticationError."""

    async def test_provider_raising_is_an_authentication_error(self):
        provider = Recording(error=RuntimeError("no token for s3cret-url"))
        with pytest.raises(AuthenticationError) as exc_info:
            await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        message = str(exc_info.value)
        assert "ce:9619" in message and "RuntimeError" in message
        assert "s3cret" not in message  # the provider's text may hold a secret

    async def test_our_own_errors_pass_through_unchanged(self):
        error = InterCEdeError("x")
        with pytest.raises(InterCEdeError) as exc_info:
            await refresh_if_stale(None, TOKEN_ONLY, Recording(error=error), now=NOW)
        assert exc_info.value is error

    @pytest.mark.parametrize(
        ("result", "returned"),
        [
            (None, "NoneType"),
            ([token()], "list"),
            (SSHPassword(value="p"), "SSHPassword"),
        ],
        ids=["nothing", "several", "another-kind"],
    )
    async def test_anything_but_the_required_kind(self, result: Any, returned: str):
        with pytest.raises(
            AuthenticationError, match=f"returned a {returned} .*not a BearerToken"
        ):
            await refresh_if_stale(None, TOKEN_ONLY, Recording(result), now=NOW)

    async def test_an_expired_credential(self):
        provider = Recording(token(-timedelta(minutes=1)))
        with pytest.raises(AuthenticationError, match="expired"):
            await refresh_if_stale(None, TOKEN_ONLY, provider, now=NOW)


READ = CredentialRequirements(
    resource="ce:9619", kind=BearerToken, scopes=frozenset({"compute.read"})
)
CREATE = CredentialRequirements(
    resource="ce:9619", kind=BearerToken, scopes=frozenset({"compute.create"})
)


class PerScope(Recording):
    """A provider that mints a token named after the scopes it is asked for."""

    def __init__(self, short_lived: CredentialRequirements | None = None) -> None:
        super().__init__()
        self.short_lived = short_lived

    async def get(self, requirements: CredentialRequirements) -> BearerToken:
        self.asked.append(requirements)
        soon = datetime.now(UTC) + timedelta(minutes=1)
        return BearerToken(
            value=",".join(requirements.scopes),
            expires_at=soon if requirements == self.short_lived else None,
        )


def value(credential: Credential) -> str:
    assert isinstance(credential, BearerToken)
    return credential.value.get_secret_value()


class TestCredentialCache:
    async def test_a_token_for_one_scope_is_not_used_for_another(self):
        cache = CredentialCache(PerScope())
        assert value(await cache.fresh(READ)) == "compute.read"
        assert value(await cache.fresh(CREATE)) == "compute.create"

    async def test_the_provider_is_asked_once_per_requirement(self):
        provider = PerScope()
        cache = CredentialCache(provider)
        for requirements in (READ, CREATE, READ, CREATE):
            await cache.fresh(requirements)
        assert provider.asked == [READ, CREATE]

    async def test_only_the_credential_about_to_expire_is_asked_for_again(self):
        provider = PerScope(short_lived=READ)
        cache = CredentialCache(provider)
        for requirements in (READ, CREATE, READ, CREATE):
            await cache.fresh(requirements)
        assert provider.asked == [READ, CREATE, READ]

    async def test_clearing_forgets_every_credential(self):
        provider = PerScope()
        cache = CredentialCache(provider)
        await cache.fresh(READ)
        cache.clear()
        await cache.fresh(READ)
        assert provider.asked == [READ, READ]

    async def test_a_provider_failure_is_an_authentication_error(self):
        cache = CredentialCache(Recording(error=RuntimeError("s3cret-url")))
        with pytest.raises(AuthenticationError):
            await cache.fresh(READ)

    async def test_a_secret_never_shows(self):
        cache = CredentialCache(PerScope())
        await cache.fresh(READ)
        assert "compute.read" not in repr(cache)


# --------------------------------------------------------------------------
# From a configuration to what to ask the provider for
# --------------------------------------------------------------------------

ARC: dict[str, Any] = {"kind": "arc", "endpoint": "https://arc.example.org:443"}
CE: dict[str, Any] = {"kind": "htcondor-ce", "endpoint": "ce.example.org:9619"}
SSH: dict[str, Any] = {
    "kind": "batch",
    "transport": "ssh",
    "scheduler": "slurm",
    "host": "batch.example.org",
    "user": "dirac",
    "known_hosts": "batch.example.org ssh-ed25519 AAAAC3Nz...",
}


class TestCredentialRequirements:
    @pytest.mark.parametrize("base", [ARC, CE], ids=["arc", "htcondor-ce"])
    def test_a_ce_asks_for_a_token_for_its_endpoint(self, base: dict[str, Any]):
        scopes = frozenset({"compute.create"})
        requirements = credential_requirements(validate_config(base), scopes)
        assert requirements is not None
        assert requirements.kind is BearerToken
        assert requirements.resource == base["endpoint"]
        assert requirements.scopes == scopes

    @pytest.mark.parametrize(
        ("auth", "kind"), [("key", SSHPrivateKey), ("password", SSHPassword)]
    )
    def test_ssh_asks_for_the_login(self, auth: str, kind: type):
        config = validate_config({**SSH, "auth": auth, "port": 2222})
        requirements = credential_requirements(config)
        assert requirements is not None
        assert requirements.kind is kind
        assert requirements.resource == "dirac@batch.example.org:2222"

    @pytest.mark.parametrize(
        "data",
        [
            {**SSH, "auth": "agent"},
            {"kind": "batch", "transport": "local", "scheduler": "slurm"},
        ],
        ids=["ssh-agent", "local"],
    )
    def test_nothing_to_ask_for(self, data: dict[str, Any]):
        assert credential_requirements(validate_config(data)) is None
