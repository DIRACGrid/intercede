"""Smoke tests to verify the package is importable and exposes a version."""

from __future__ import annotations

import intercede


def test_package_imports():
    assert intercede is not None


def test_version_is_defined():
    assert isinstance(intercede.version, str)
    assert intercede.version
