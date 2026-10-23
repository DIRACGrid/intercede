"""Smoke tests to verify the package is importable and exposes a version."""

from __future__ import annotations

import intercede


def test_package_imports():
    assert intercede is not None


def test_version_is_defined():
    assert isinstance(intercede.version, str)
    assert intercede.version


def test_version_falls_back_when_not_installed():
    """Running from a source checkout, with no package metadata, still imports."""
    import subprocess
    import sys

    code = (
        "import importlib.metadata as metadata\n"
        "def missing(name):\n"
        "    raise metadata.PackageNotFoundError(name)\n"
        "metadata.version = missing\n"
        "import intercede\n"
        "assert intercede.__version__ == intercede.version == 'unknown', intercede.version\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603  (constant input)
