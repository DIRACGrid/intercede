"""Credential values, requirements and the static provider."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from intercede.auth import (
    BearerToken,
    Credential,
    CredentialProvider,
    CredentialRequirements,
    SSHPassword,
    SSHPrivateKey,
    StaticCredentials,
)
from intercede.exceptions import AuthenticationError

SECRET = "TOPSECRET"  # noqa: S105  (a fake secret the tests look for)

# One instance of every kind, each holding SECRET in all of its secret fields.
EVERY_KIND: dict[type[Credential], Credential] = {
    BearerToken: BearerToken(value=SECRET),
    SSHPrivateKey: SSHPrivateKey(pem=SECRET.encode(), passphrase=SECRET),
    SSHPassword: SSHPassword(value=SECRET),
}


@pytest.mark.parametrize("kind", list(EVERY_KIND), ids=lambda kind: kind.__name__)
def test_a_secret_never_shows(kind: type[Credential]):
    credential = EVERY_KIND[kind]
    for shown in (
        repr(credential),
        str(credential),
        credential.model_dump_json(),
        repr(StaticCredentials([credential])),
    ):
        assert SECRET not in shown


@pytest.mark.parametrize(
    "build",
    [
        lambda: BearerToken(value="t", token=SECRET),
        lambda: SSHPrivateKey(pem=b"k", passphrase=[SECRET]),
        lambda: StaticCredentials([{"value": SECRET}]),
    ],
    ids=["unknown-field", "wrong-type", "plain-data-in-provider"],
)
def test_a_mistake_never_repeats_the_secret(build: Callable[[], Any]):
    with pytest.raises(ValueError) as exc_info:  # also pydantic's ValidationError
        build()
    assert SECRET not in str(exc_info.value)


def test_expiry_must_have_a_timezone():
    # A naive time could not be compared with the current time in UTC.
    with pytest.raises(ValidationError, match="timezone"):
        BearerToken(value="t", expires_at=datetime(2030, 1, 1))  # noqa: DTZ001


class TestCredentialRequirements:
    def test_names_one_kind(self):
        requirements = CredentialRequirements(resource="r", kind=BearerToken)
        assert requirements.kind is BearerToken

    def test_needs_a_kind(self):
        with pytest.raises(ValidationError, match="kind"):
            CredentialRequirements(resource="r")  # type: ignore[call-arg]

    @pytest.mark.parametrize("kind", [Credential, str, "BearerToken"])
    def test_the_kind_is_a_concrete_credential_class(self, kind: Any):
        with pytest.raises(ValidationError):
            CredentialRequirements(resource="r", kind=kind)


class TestStaticCredentials:
    def test_is_a_provider(self):
        assert isinstance(StaticCredentials(), CredentialProvider)

    async def test_hands_out_only_the_requested_kind(self):
        token, password = BearerToken(value="t"), SSHPassword(value="pw")
        provider = StaticCredentials([token, password])
        requirements = CredentialRequirements(resource="ce:9619", kind=BearerToken)
        async with provider:
            assert await provider.get(requirements) is token

    async def test_a_kind_it_does_not_hold_is_named(self):
        # A token and a password both have a single "value" field: it goes by
        # kind, not by shape.
        provider = StaticCredentials([SSHPassword(value="pw")])
        requirements = CredentialRequirements(resource="ce:9619", kind=BearerToken)
        with pytest.raises(AuthenticationError, match="no BearerToken for ce:9619"):
            await provider.get(requirements)

    @pytest.mark.parametrize(
        "credentials",
        [
            [BearerToken(value="a"), BearerToken(value="b")],
            [{"value": "x"}],
            ["token"],
            [Credential()],
        ],
        ids=["two-of-a-kind", "plain-data", "a-string", "the-base-class"],
    )
    def test_refuses_bad_input_at_construction(self, credentials: Any):
        with pytest.raises(ValueError):
            StaticCredentials(credentials)
