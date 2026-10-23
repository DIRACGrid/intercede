"""Stack fixtures for the integration suite, selected with --stack and --config."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.integration.generate_matrix import STACKS_DIR, compose_env, load_manifest


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
