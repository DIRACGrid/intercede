"""Pytest options for the integration suite.

Registered at the tests/ root so that every invocation (``pytest``, ``pytest tests/integration``)
accepts them; pytest only reads ``pytest_addoption`` from the initial conftest files.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("intercede-integration")
    group.addoption(
        "--stack",
        action="store",
        default=None,
        help="Stack id from tests/integration/stacks.yml to run against (e.g. arc-slurm)",
    )
    group.addoption(
        "--config",
        action="store",
        default="basic",
        help="Configuration name under stacks/<id>/config/ (default: basic)",
    )
