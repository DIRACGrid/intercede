"""Session-wide pytest options for interCEde (must live at the tests/ root).

``--stack``/``--config`` are consumed by the integration suite (tests/integration/conftest.py), but
pytest only honours ``pytest_addoption`` from *initial* conftest files — those in the rootdir and
the invocation's target directories. Registering the options here (the root of ``testpaths``) means
every invocation form accepts them: bare ``pytest``, ``pytest tests`` and
``pytest tests/integration`` all work, instead of only the last. The fixtures that read the options
stay in tests/integration/conftest.py.
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
