"""Moving collected output from a job's directory into ``dest``.

``condor_transfer_data`` writes into the directory a job was submitted from,
recorded as ``SUBMIT_Iwd`` in the job ad. That path comes from the CE, so it
is only trusted inside our own working area, and what lands there is checked
against the configured limits before anything reaches ``dest``.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from intercede.config import HTCondorCEConfig
from intercede.models import JobOutput, OpOutcome


def inside_workdir(iwd: Path, workdir: Path) -> bool:
    """Tell whether a job directory lies strictly inside our working area."""
    root = workdir.resolve()
    return (
        iwd.is_absolute()
        and iwd.resolve().is_relative_to(root)
        and iwd.resolve() != root
    )


def move_output(
    iwd: Path, ad: Mapping[str, Any], target: Path, limits: HTCondorCEConfig
) -> OpOutcome[JobOutput]:
    """Move a job's collected files to ``target`` and describe them in a manifest."""
    if not iwd.is_dir():
        return _failed("condor_transfer_data returned no files")

    found = _find_output_files(iwd)
    error = _validate_output_files(iwd, found, limits)
    if error is not None:
        return _failed(error)

    _prepare_output_directory(target)
    manifest = _move_output_files(found, iwd, target, ad)

    return OpOutcome[JobOutput](ok=True, value=JobOutput(**manifest))


def relative(value: object, iwd: Path) -> str | None:
    """Return the path of an ad's file attribute relative to the job directory.

    ``Out`` and ``Err`` are relative to it already; ``UserLog`` is absolute.
    """
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(PurePosixPath(iwd)).as_posix()
    except ValueError:
        return None


def _find_output_files(iwd: Path) -> list[Path]:
    """Find non-directory entries in the collected output tree."""
    return [path for path in iwd.rglob("*") if not path.is_dir()]


def _validate_output_files(
    iwd: Path, found: list[Path], limits: HTCondorCEConfig
) -> str | None:
    """Return an error message if the collected files are unsafe or too large."""
    root = iwd.resolve()

    for path in found:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return "the output holds a link or a path outside the job directory"

    if len(found) > limits.max_output_files:
        return f"more than {limits.max_output_files} output files"

    total_bytes = sum(path.stat().st_size for path in found)
    if total_bytes > limits.max_output_bytes:
        return f"output larger than {limits.max_output_bytes} bytes"

    return None


def _prepare_output_directory(target: Path) -> None:
    """Replace the previous collection with a fresh output directory."""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, mode=0o700)


def _move_output_files(
    found: list[Path], iwd: Path, target: Path, ad: Mapping[str, Any]
) -> dict[str, Any]:
    """Move files into the destination and build the output manifest."""
    special = {
        relative(ad.get("Out"), iwd): "stdout",
        relative(ad.get("Err"), iwd): "stderr",
        relative(ad.get("UserLog"), iwd): "log",
    }
    manifest: dict[str, Any] = {"files": {}}

    for path in found:
        name = path.relative_to(iwd).as_posix()
        destination = target / name

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, destination)

        if name in special:
            manifest[special[name]] = destination
        else:
            manifest["files"][name] = destination

    return manifest


def _failed(reason: str) -> OpOutcome[JobOutput]:
    return OpOutcome[JobOutput](ok=False, reason=reason)
