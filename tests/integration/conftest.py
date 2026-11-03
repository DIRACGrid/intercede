"""Stack fixtures for the integration suite, selected with --stack and --config.

The tests marked ``contract`` are the shared suite: written once against the
interCEde protocols, run on every stack. A test that needs an optional
capability takes its fixture (``output_retriever``, ``killable``,
``purgeable``) and is skipped on a backend that does not have it.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

import pytest

from intercede import registry
from intercede.auth.models import BearerToken, Credential
from intercede.auth.providers import StaticCredentials
from intercede.protocols import JobBackend, Killable, OutputRetriever, Purgeable
from tests.integration.generate_matrix import STACKS_DIR, compose_env, load_manifest

# Nothing listens on port 1, so connecting is refused at once.
CLOSED_PORT = "127.0.0.1:1"


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects anything
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Select the shared suite for every stack, whatever its markers say.

    CI runs ``pytest -m "<markers>"`` with the stack's own expression. A shared
    test gets the markers that expression asks for, so it is selected too.
    """
    stack_id = config.getoption("--stack")
    entries = {entry["id"]: entry for entry in load_manifest()}
    if stack_id not in entries:
        return  # no stack: the `stack` fixture skips, or exits on an unknown id
    for name in required_markers(entries[stack_id]["markers"]):
        for item in items:
            if item.get_closest_marker("contract") is not None:
                item.add_marker(name)


def required_markers(expression: str) -> set[str]:
    """Return the marker names a ``-m`` expression asks for, i.e. not negated."""
    words = re.findall(r"[\w-]+", expression)
    return {
        word
        for previous, word in zip(["", *words], words)
        if word not in ("and", "or", "not") and previous != "not"
    }


@pytest.fixture(scope="session")
def stack(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Return the stacks.yml entry selected by --stack; skip the test when none is given."""
    stack_id = request.config.getoption("--stack")
    if stack_id is None:
        pytest.skip("no --stack given (this test runs against a compose stack)")
    entries = {entry["id"]: entry for entry in load_manifest()}
    if stack_id not in entries:
        pytest.exit(
            f"unknown stack {stack_id!r}; known stacks: {', '.join(sorted(entries))}",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    return entries[stack_id]


@pytest.fixture(scope="session")
def stack_dir(stack: dict[str, Any]) -> Path:
    """Return the directory holding the stack's compose.yml."""
    return STACKS_DIR / compose_env(stack)


@pytest.fixture(scope="session")
def client_config(
    request: pytest.FixtureRequest, stack: dict[str, Any]
) -> dict[str, Any]:
    """Load stacks/<id>/config/<config>/intercede-client.toml."""
    config_name = request.config.getoption("--config")
    path = STACKS_DIR / stack["id"] / "config" / config_name / "intercede-client.toml"
    if not path.is_file():
        msg = f"stack {stack['id']!r} has no {config_name!r} configuration ({path} is missing)"
        # In CI a missing config must fail: an all-skipped job would be green yet test nothing.
        if os.environ.get("CI"):
            pytest.fail(msg)
        pytest.skip(msg)
    with path.open("rb") as f:
        return tomllib.load(f)


# What a stack can declare its backend refuses, in [refusals] of its client config.
REFUSALS = frozenset({"containers", "url_inputs", "url_outputs"})


@pytest.fixture(scope="session")
def refusals(client_config: dict[str, Any]) -> frozenset[str]:
    """Return what this stack's backend cannot honour, as [refusals] declares it."""
    declared = client_config.get("refusals", {})
    if unknown := set(declared) - REFUSALS:
        pytest.fail(
            f"[refusals] has unknown keys {sorted(unknown)}; known: {sorted(REFUSALS)}"
        )
    return frozenset(name for name, refused in declared.items() if refused)


def _token(folder: Path) -> Credential:
    return BearerToken(value=(folder / "token").read_text().strip())


def _tampered_token(credential: Credential) -> Credential:
    # Same shape, wrong signature: what a revoked token, or one issued for
    # another resource, looks like to the resource.
    assert isinstance(credential, BearerToken)
    value = credential.value.get_secret_value()
    tail = "A" * 8 if value[-8:] != "A" * 8 else "B" * 8
    return BearerToken(value=value[:-8] + tail)


# How each credential type is read from the stack's credentials folder, and
# turned into one the resource rejects.
_READERS: dict[str, Callable[[Path], Credential]] = {"token": _token}
_TAMPERERS: dict[str, Callable[[Credential], Credential]] = {"token": _tampered_token}


@pytest.fixture
def credential(client_config: dict[str, Any], stack_dir: Path) -> Credential:
    """Read the credential the stack minted at start, as [credentials] names it."""
    if "credentials" not in client_config:
        pytest.skip("this stack's backend needs no credentials")
    section = client_config["credentials"]
    if section["type"] not in _READERS:
        pytest.fail(f"credential type {section['type']!r} is not supported yet")
    try:
        # Read here and handed over as a value: the backend writes its own private copy.
        return _READERS[section["type"]](stack_dir / section["path"])
    except FileNotFoundError as exc:
        pytest.fail(f"{exc.filename} is missing: is the stack up?")


@pytest.fixture
def credentials(credential: Credential) -> StaticCredentials:
    """Return a provider holding the stack's credential."""
    return StaticCredentials([credential])


@pytest.fixture
def rejected_credentials(
    client_config: dict[str, Any], credential: Credential
) -> StaticCredentials:
    """Return a provider holding a credential of the right kind that the resource rejects."""
    return StaticCredentials(
        [_TAMPERERS[client_config["credentials"]["type"]](credential)]
    )


@pytest.fixture
def unreachable_config(client_config: dict[str, Any]) -> dict[str, Any]:
    """Return the stack's [backend], pointed at a closed port, as when the resource is down."""
    config = dict(client_config["backend"])
    if "endpoint" in config:
        endpoint = urlsplit(config["endpoint"])
        if endpoint.scheme:  # a URL, e.g. "https://ce:443/arex"
            config["endpoint"] = endpoint._replace(netloc=CLOSED_PORT).geturl()
        else:  # "host:port"
            config["endpoint"] = CLOSED_PORT
    elif "host" in config:  # reached over SSH
        config["host"], port = CLOSED_PORT.split(":")
        config["port"] = int(port)
    else:
        pytest.skip("this stack's backend reaches no resource over the network")
    return config


@pytest.fixture
async def backend(
    client_config: dict[str, Any], credentials: StaticCredentials
) -> AsyncIterator[JobBackend]:
    """Yield the backend [backend] describes, built by the registry and open."""
    async with registry.get_backend(client_config["backend"], credentials) as opened:
        yield opened


T = TypeVar("T")


def _capable(backend: JobBackend, capability: type[T]) -> T:
    if not isinstance(backend, capability):
        pytest.skip(f"the backend is not {capability.__name__}")
    return backend


@pytest.fixture
def output_retriever(backend: JobBackend) -> OutputRetriever:
    """Return the backend as an OutputRetriever; skip the test when it is not one."""
    return _capable(backend, OutputRetriever)


@pytest.fixture
def killable(backend: JobBackend) -> Killable:
    """Return the backend as Killable; skip the test when it is not."""
    return _capable(backend, Killable)


@pytest.fixture
def purgeable(backend: JobBackend) -> Purgeable:
    """Return the backend as Purgeable; skip the test when it is not."""
    return _capable(backend, Purgeable)
