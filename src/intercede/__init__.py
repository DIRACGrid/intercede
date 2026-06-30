"""interCEde - Unified interfaces to Computing Elements and batch systems for DIRAC / DiracX."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version

try:
    __version__ = get_version(__name__)
    version = __version__
except PackageNotFoundError:
    version = "Unknown"

__all__ = ["__version__"]
