"""Stack-aware fixtures for the integration suite (IC-ADR-002).

Tests are written once, against the interCEde contract, and parameterized by a *stack* — a named,
self-contained backend environment described in ``stacks.yml`` and started with
``docker compose -f stacks/<id>/compose.yml up --wait``. Select the stack with ``--stack`` and the
configuration with ``--config`` (registered in tests/conftest.py; see
docs/dev/how-to/run-integration-tests-locally.md).

Stack-bound tests skip themselves when no ``--stack`` is given, so the unit-test run
(``pixi run pytest``) stays green without any compose environment.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.integration.generate_matrix import STACKS_DIR, compose_stack, load_manifest


@pytest.fixture(scope="session")
def stack(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Return the stacks.yml entry selected by --stack; skip the test when no stack is given."""
    stack_id = request.config.getoption("--stack")
    if stack_id is None:
        pytest.skip("no --stack given (this test runs against a compose stack)")
    entries = {entry["id"]: entry for entry in load_manifest()}
    if stack_id not in entries:
        # A bad --stack is a usage error for the whole session, not a per-test failure: raising
        # UsageError from a fixture yields per-test tracebacks (repeated per xdist worker) and no
        # exit-4; pytest.exit stops the run once, cleanly.
        pytest.exit(
            f"unknown stack {stack_id!r}; known stacks: {', '.join(sorted(entries))}",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    return entries[stack_id]


@pytest.fixture(scope="session")
def stack_dir(stack: dict[str, Any]) -> Path:
    """Directory holding the stack's compose.yml, following the `stack:` alias if set."""
    return STACKS_DIR / compose_stack(stack)


@pytest.fixture(scope="session")
def client_config(
    request: pytest.FixtureRequest, stack: dict[str, Any]
) -> dict[str, Any]:
    """Load intercede-client.toml for --stack/--config: endpoint, credential type, queue names.

    The client config is resolved from the entry's *own* id, never the ``stack:`` alias: an aliased
    entry (e.g. ``local-slurm`` reusing ``ssh-slurm``'s compose) drives the shared backend over a
    different transport/endpoint and so keeps its own client half. Backend config and client config
    change together under one configuration name (IC-ADR-002 §1); this fixture is the client half.
    """
    config_name = request.config.getoption("--config")
    path = STACKS_DIR / stack["id"] / "config" / config_name / "intercede-client.toml"
    if not path.is_file():
        msg = f"stack {stack['id']!r} has no {config_name!r} configuration ({path} is missing)"
        # In CI a *selected* stack with no config is a real failure, not a skip: an all-skipped run
        # would report a green integration cell that verified nothing (IC-ADR-002 §5). Locally a
        # skip stays convenient while a stack is still being brought up.
        if os.environ.get("CI"):
            pytest.fail(msg)
        pytest.skip(f"{msg} - skipping (set CI=1 to make this a failure)")
    with path.open("rb") as f:
        return tomllib.load(f)
