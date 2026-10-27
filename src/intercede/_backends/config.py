"""Settings of the in-memory fake, which no public configuration reaches.

Every other backend is built from its public configuration model in
``intercede.config``.
"""

from __future__ import annotations

from typing import Literal

from intercede.models import _Model


class FakeConfig(_Model):
    """The in-memory fake, for the test suites."""

    kind: Literal["fake"] = "fake"
    resource: str = "fake://local"
