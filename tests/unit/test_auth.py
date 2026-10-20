"""Credential values, sets, requirements and the provider protocol (IC-ADR-003)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import TracebackType
from typing import Any, Self

import pytest
from pydantic import ValidationError

import intercede.auth
from intercede.auth import (
    BearerToken,
    Credential,
    CredentialProvider,
    CredentialRequirements,
    CredentialSet,
    SSHPassword,
    SSHPrivateKey,
    StaticCredentials,
    X509Proxy,
)

SECRET = "TOPSECRET"  # noqa: S105  (a fake secret the tests look for)
LATER = datetime(2030, 1, 1, tzinfo=UTC)

# One instance of every kind, each holding SECRET in all of its secret fields.
EVERY_KIND: dict[type[Credential], Credential] = {
    BearerToken: BearerToken(value=SECRET),
    X509Proxy: X509Proxy(pem=SECRET.encode()),
    SSHPrivateKey: SSHPrivateKey(pem=SECRET.encode(), passphrase=SECRET),
    SSHPassword: SSHPassword(value=SECRET),
}
KINDS = list(EVERY_KIND)


def kind_id(kind: type[Credential]) -> str:
    return kind.__name__


# --------------------------------------------------------------------------
# Credential values
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS, ids=kind_id)
class TestEveryKind:
    def test_is_a_credential(self, kind: type[Credential]):
        assert isinstance(EVERY_KIND[kind], Credential)

    def test_never_expires_by_default(self, kind: type[Credential]):
        assert EVERY_KIND[kind].expires_at is None

    def test_can_expire(self, kind: type[Credential]):
        credential = EVERY_KIND[kind].model_copy(update={"expires_at": LATER})
        assert credential.expires_at == LATER

    def test_secret_never_appears_in_repr_str_or_json(self, kind: type[Credential]):
        credential = EVERY_KIND[kind]
        for shown in (repr(credential), str(credential), credential.model_dump_json()):
            assert SECRET not in shown

    def test_secret_never_appears_inside_a_set(self, kind: type[Credential]):
        credentials = CredentialSet([EVERY_KIND[kind]])
        assert SECRET not in repr(credentials)
        assert SECRET not in credentials.model_dump_json()

    def test_is_immutable(self, kind: type[Credential]):
        with pytest.raises(ValidationError):
            EVERY_KIND[kind].expires_at = LATER  # type: ignore[misc]

    def test_rejects_unknown_fields(self, kind: type[Credential]):
        data = {**EVERY_KIND[kind].model_dump(), "path": "/home/user/.ssh/id"}
        with pytest.raises(ValidationError, match="path"):
            kind.model_validate(data)


class TestExpiry:
    @pytest.mark.parametrize(
        "moment",
        [LATER, datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=2)))],
        ids=["utc", "other-zone"],
    )
    def test_any_timezone_is_accepted(self, moment: datetime):
        assert BearerToken(value="t", expires_at=moment).expires_at == moment

    def test_naive_datetime_is_refused(self):
        # It could not be compared with the current time in UTC.
        with pytest.raises(ValidationError, match="timezone"):
            BearerToken(value="t", expires_at=datetime(2030, 1, 1))  # noqa: DTZ001

    def test_parses_iso_strings_with_a_zone(self):
        token = BearerToken.model_validate(
            {"value": "t", "expires_at": "2030-01-01T00:00:00Z"}
        )
        assert token.expires_at == LATER


class TestSecretsAreReadableOnPurpose:
    def test_token(self):
        assert BearerToken(value="t").value.get_secret_value() == "t"

    def test_proxy(self):
        assert X509Proxy(pem=b"pem").pem.get_secret_value() == b"pem"

    def test_private_key_and_passphrase(self):
        key = SSHPrivateKey(pem=b"key", passphrase="pass")  # noqa: S106  (fake)
        assert key.pem.get_secret_value() == b"key"
        assert key.passphrase is not None
        assert key.passphrase.get_secret_value() == "pass"

    def test_private_key_without_passphrase(self):
        assert SSHPrivateKey(pem=b"key").passphrase is None

    def test_password(self):
        assert SSHPassword(value="pw").value.get_secret_value() == "pw"


@pytest.mark.parametrize(
    ("kind", "missing"),
    [
        (BearerToken, "value"),
        (X509Proxy, "pem"),
        (SSHPrivateKey, "pem"),
        (SSHPassword, "value"),
    ],
)
def test_secret_material_is_required(kind: type[Credential], missing: str):
    with pytest.raises(ValidationError, match=missing):
        kind()


# --------------------------------------------------------------------------
# CredentialSet
# --------------------------------------------------------------------------


class TestCredentialSet:
    def test_built_from_a_list_or_tuple(self):
        token = BearerToken(value="t")
        assert CredentialSet([token]) == CredentialSet((token,))

    def test_empty(self):
        assert CredentialSet().root == ()

    @pytest.mark.parametrize("kind", KINDS, ids=kind_id)
    def test_get_finds_each_kind(self, kind: type[Credential]):
        credentials = CredentialSet(list(EVERY_KIND.values()))
        assert credentials.get(kind) is EVERY_KIND[kind]

    @pytest.mark.parametrize("kind", KINDS, ids=kind_id)
    def test_get_returns_none_for_a_missing_kind(self, kind: type[Credential]):
        others = []
        for k, credential in EVERY_KIND.items():
            if k is not kind:
                others.append(credential)
        assert CredentialSet(others).get(kind) is None

    def test_get_on_an_empty_set(self):
        assert CredentialSet().get(BearerToken) is None

    def test_token_and_password_are_not_confused(self):
        # Both have a single "value" field; get() must go by kind, not by shape.
        credentials = CredentialSet([SSHPassword(value="pw")])
        assert credentials.get(BearerToken) is None

    @pytest.mark.parametrize("kind", KINDS, ids=kind_id)
    def test_one_credential_per_kind(self, kind: type[Credential]):
        twice = [EVERY_KIND[kind], EVERY_KIND[kind].model_copy()]
        with pytest.raises(ValidationError, match=kind.__name__):
            CredentialSet(twice)

    @pytest.mark.parametrize(
        "item",
        [{"value": "x"}, "token", b"pem", 42, None],
        ids=["dict", "str", "bytes", "int", "none"],
    )
    def test_plain_data_is_refused(self, item: Any):
        with pytest.raises(ValidationError, match="credential objects"):
            CredentialSet([item])

    def test_the_base_class_is_refused(self):
        with pytest.raises(ValidationError):
            CredentialSet([Credential()])

    def test_is_immutable(self):
        with pytest.raises(ValidationError):
            CredentialSet().root = (BearerToken(value="t"),)  # type: ignore[misc]

    def test_order_is_kept(self):
        values = list(EVERY_KIND.values())
        assert CredentialSet(values).root == tuple(values)


# --------------------------------------------------------------------------
# CredentialRequirements
# --------------------------------------------------------------------------


class TestCredentialRequirements:
    def test_token_and_proxy_together(self):
        requirements = CredentialRequirements(
            resource="ce.example.org:9619",
            kinds=frozenset({BearerToken, X509Proxy}),
            scopes=frozenset({"compute.create"}),
        )
        assert requirements.kinds == {BearerToken, X509Proxy}
        assert requirements.scopes == {"compute.create"}

    @pytest.mark.parametrize("kind", KINDS, ids=kind_id)
    def test_each_kind_alone(self, kind: type[Credential]):
        requirements = CredentialRequirements(resource="r", kinds=frozenset({kind}))
        assert requirements.kinds == {kind}

    def test_scopes_default_to_none(self):
        requirements = CredentialRequirements(
            resource="r", kinds=frozenset({SSHPassword})
        )
        assert requirements.scopes == frozenset()

    def test_needs_at_least_one_kind(self):
        with pytest.raises(ValidationError, match="at least one"):
            CredentialRequirements(resource="r", kinds=frozenset())

    def test_refuses_the_base_class(self):
        with pytest.raises(ValidationError, match="concrete"):
            CredentialRequirements(resource="r", kinds=frozenset({Credential}))

    @pytest.mark.parametrize(
        "kind",
        [str, int, CredentialSet, "BearerToken"],
        ids=["str", "int", "set", "name"],
    )
    def test_refuses_anything_but_credential_kinds(self, kind: Any):
        with pytest.raises(ValidationError, match="subclass of Credential"):
            CredentialRequirements(resource="r", kinds=frozenset({kind}))

    @pytest.mark.parametrize("missing", ["resource", "kinds"])
    def test_required_fields(self, missing: str):
        data: dict[str, Any] = {"resource": "r", "kinds": frozenset({BearerToken})}
        del data[missing]
        with pytest.raises(ValidationError, match=missing):
            CredentialRequirements.model_validate(data)

    def test_is_hashable_and_immutable(self):
        requirements = CredentialRequirements(
            resource="r", kinds=frozenset({BearerToken})
        )
        assert {requirements: 1}[requirements] == 1  # a provider may cache by them
        with pytest.raises(ValidationError):
            requirements.resource = "other"  # type: ignore[misc]

    def test_equal_requirements_are_equal(self):
        def make() -> CredentialRequirements:
            return CredentialRequirements(resource="r", kinds=frozenset({BearerToken}))

        assert make() == make()
        assert hash(make()) == hash(make())


# --------------------------------------------------------------------------
# CredentialProvider
# --------------------------------------------------------------------------


class StaticProvider:
    """The trivial provider of IC-ADR-003 §8 (b): fixed credentials, nothing to release."""

    def __init__(self, credentials: CredentialSet) -> None:
        self.credentials = credentials
        self.asked: list[CredentialRequirements] = []
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.closed = True

    async def get(self, requirements: CredentialRequirements) -> CredentialSet:
        self.asked.append(requirements)
        return CredentialSet(
            [
                credential
                for credential in self.credentials.root
                if type(credential) in requirements.kinds
            ]
        )


class TestCredentialProvider:
    def test_a_static_provider_qualifies(self):
        assert isinstance(StaticProvider(CredentialSet()), CredentialProvider)

    def test_without_lifecycle_it_does_not(self):
        class GetOnly:
            async def get(self, requirements: CredentialRequirements) -> CredentialSet:
                return CredentialSet()

        assert not isinstance(GetOnly(), CredentialProvider)

    def test_without_get_it_does_not(self):
        class LifecycleOnly:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *exc_info: object) -> None: ...

        assert not isinstance(LifecycleOnly(), CredentialProvider)

    async def test_round_trip(self):
        token, key = BearerToken(value="t"), SSHPrivateKey(pem=b"k")
        provider = StaticProvider(CredentialSet([token, key]))
        requirements = CredentialRequirements(
            resource="batch.example.org", kinds=frozenset({SSHPrivateKey})
        )
        async with provider as credentials:
            supplied = await credentials.get(requirements)
        assert supplied.get(SSHPrivateKey) is key
        assert supplied.get(BearerToken) is None  # only what was asked for
        assert provider.asked == [requirements]
        assert provider.closed


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def test_every_exported_name_exists():
    for name in intercede.auth.__all__:
        assert hasattr(intercede.auth, name), name


def test_exports_exactly_the_credential_api():
    assert set(intercede.auth.__all__) == {
        "BearerToken",
        "Credential",
        "CredentialProvider",
        "CredentialRequirements",
        "CredentialSet",
        "SSHPassword",
        "SSHPrivateKey",
        "StaticCredentials",
        "X509Proxy",
    }


def test_star_import_works():
    namespace: dict[str, Any] = {}
    exec("from intercede.auth import *", namespace)  # noqa: S102  (constant input)
    assert "CredentialProvider" in namespace


# --------------------------------------------------------------------------
# StaticCredentials
# --------------------------------------------------------------------------


class TestStaticCredentials:
    def test_is_a_provider(self):
        assert isinstance(StaticCredentials(), CredentialProvider)

    @pytest.mark.parametrize("kind", KINDS, ids=kind_id)
    async def test_hands_out_only_the_requested_kind(self, kind: type[Credential]):
        provider = StaticCredentials(list(EVERY_KIND.values()))
        requirements = CredentialRequirements(resource="r", kinds=frozenset({kind}))
        async with provider:
            supplied = await provider.get(requirements)
        assert supplied.root == (EVERY_KIND[kind],)

    async def test_hands_out_several_kinds_together(self):
        token, proxy = BearerToken(value="t"), X509Proxy(pem=b"p")
        provider = StaticCredentials([token, proxy, SSHPassword(value="pw")])
        requirements = CredentialRequirements(
            resource="ce:9619", kinds=frozenset({BearerToken, X509Proxy})
        )
        supplied = await provider.get(requirements)
        assert supplied.get(BearerToken) is token
        assert supplied.get(X509Proxy) is proxy
        assert supplied.get(SSHPassword) is None

    async def test_a_kind_it_does_not_hold_is_left_out(self):
        requirements = CredentialRequirements(
            resource="r", kinds=frozenset({X509Proxy})
        )
        assert (
            await StaticCredentials([BearerToken(value="t")]).get(requirements)
        ).root == ()

    async def test_accepts_a_credential_set(self):
        token = BearerToken(value="t")
        requirements = CredentialRequirements(
            resource="r", kinds=frozenset({BearerToken})
        )
        supplied = await StaticCredentials(CredentialSet([token])).get(requirements)
        assert supplied.get(BearerToken) is token

    def test_refuses_duplicates_at_construction(self):
        with pytest.raises(ValidationError):
            StaticCredentials([BearerToken(value="a"), BearerToken(value="b")])

    def test_refuses_plain_data_at_construction(self):
        with pytest.raises(ValidationError, match="credential objects"):
            StaticCredentials([{"value": "x"}])  # type: ignore[list-item]

    async def test_closing_is_harmless_and_repeatable(self):
        provider = StaticCredentials([BearerToken(value="t")])
        async with provider:
            pass
        async with provider:  # reusable: it holds nothing to release
            pass
        requirements = CredentialRequirements(
            resource="r", kinds=frozenset({BearerToken})
        )
        assert (await provider.get(requirements)).get(BearerToken) is not None

    def test_secrets_stay_hidden(self):
        assert SECRET not in repr(StaticCredentials(list(EVERY_KIND.values())))
