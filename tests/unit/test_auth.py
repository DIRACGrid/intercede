"""Credential values, sets, requirements and the static provider."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

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

# One instance of every kind, each holding SECRET in all of its secret fields.
EVERY_KIND: dict[type[Credential], Credential] = {
    BearerToken: BearerToken(value=SECRET),
    X509Proxy: X509Proxy(pem=SECRET.encode()),
    SSHPrivateKey: SSHPrivateKey(pem=SECRET.encode(), passphrase=SECRET),
    SSHPassword: SSHPassword(value=SECRET),
}


@pytest.mark.parametrize("kind", list(EVERY_KIND), ids=lambda kind: kind.__name__)
def test_a_secret_never_shows(kind: type[Credential]):
    credential = EVERY_KIND[kind]
    credentials = CredentialSet([credential])
    for shown in (
        repr(credential),
        str(credential),
        credential.model_dump_json(),
        repr(credentials),
        credentials.model_dump_json(),
        repr(StaticCredentials([credential])),
    ):
        assert SECRET not in shown


def test_expiry_must_have_a_timezone():
    # A naive time could not be compared with the current time in UTC.
    with pytest.raises(ValidationError, match="timezone"):
        BearerToken(value="t", expires_at=datetime(2030, 1, 1))  # noqa: DTZ001


class TestCredentialSet:
    def test_get_goes_by_kind_not_by_shape(self):
        # A token and a password both have a single "value" field.
        password = SSHPassword(value="pw")
        credentials = CredentialSet([password])
        assert credentials.get(SSHPassword) is password
        assert credentials.get(BearerToken) is None

    def test_one_credential_per_kind(self):
        with pytest.raises(ValidationError, match="BearerToken"):
            CredentialSet([BearerToken(value="a"), BearerToken(value="b")])

    @pytest.mark.parametrize("item", [{"value": "x"}, "token", Credential()])
    def test_only_concrete_credentials(self, item: Any):
        with pytest.raises(ValidationError):
            CredentialSet([item])


class TestCredentialRequirements:
    def test_needs_at_least_one_kind(self):
        with pytest.raises(ValidationError, match="at least one"):
            CredentialRequirements(resource="r", kinds=frozenset())

    @pytest.mark.parametrize("kind", [Credential, str, "BearerToken"])
    def test_kinds_are_concrete_credential_classes(self, kind: Any):
        with pytest.raises(ValidationError):
            CredentialRequirements(resource="r", kinds=frozenset({kind}))


class TestStaticCredentials:
    def test_is_a_provider(self):
        assert isinstance(StaticCredentials(), CredentialProvider)

    async def test_hands_out_only_the_requested_kinds(self):
        token, proxy = BearerToken(value="t"), X509Proxy(pem=b"p")
        provider = StaticCredentials([token, proxy, SSHPassword(value="pw")])
        requirements = CredentialRequirements(
            resource="ce:9619", kinds=frozenset({BearerToken, X509Proxy, SSHPrivateKey})
        )
        async with provider:
            supplied = await provider.get(requirements)
        assert supplied.root == (token, proxy)

    def test_refuses_bad_input_at_construction(self):
        with pytest.raises(ValidationError):
            StaticCredentials([BearerToken(value="a"), BearerToken(value="b")])
