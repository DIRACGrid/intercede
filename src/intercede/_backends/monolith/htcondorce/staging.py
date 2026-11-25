"""The private working area, and what a submission puts in it.

Everything here turns a ``SubmissionSpec`` into what HTCondor is given: files
staged under their names, one directory per copy holding only that copy's
secrets, and the executable, arguments and outputs in HTCondor's terms.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from intercede._backends._htcondor.submit import CopyPlan
from intercede.exceptions import ConfigurationError, SpecificationRejectedError
from intercede.models import CopyVars, SubmissionSpec

_GLOB = re.compile(r"[*?\[]")


# -- the working area ---------------------------------------------------------


def default_workdir() -> Path:
    """Return where this user's staging and collection areas live on this host."""
    return Path(tempfile.gettempdir()) / f"intercede-htcondor-ce-{os.getuid()}"


def ensure_workdir(workdir: Path) -> Path:
    """Create the private working area, refusing one somebody else controls."""
    workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = workdir.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        raise ConfigurationError(f"{workdir} is not a directory owned by this user")
    workdir.chmod(0o700)
    return workdir


def write_private(path: Path, content: bytes) -> None:
    """Create a file only this user can read (0600); never overwrite one."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)


# -- files --------------------------------------------------------------------


def stage_inputs(spec: SubmissionSpec, submission: Path) -> list[str]:
    """Copy local inputs under their names, so they spool with the right layout.

    Returns the entries for ``transfer_input_files``: the staged directory and
    the URLs the resource fetches itself.
    """
    staged: list[str] = []
    common = submission / "inputs"
    for ref in spec.inputs:
        if "://" in ref.source:
            if PurePosixPath(ref.source.split("?")[0]).name != ref.name:
                raise SpecificationRejectedError(
                    f"input {ref.name!r}: HTCondor names a URL input after the URL, "
                    f"so {ref.source!r} would arrive under another name"
                )
            staged.append(ref.source)  # fetched by the resource
            continue
        target = common / ref.name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Read now: the caller may delete its file once submit() returns.
        try:
            content = Path(ref.source).read_bytes()
        except OSError as exc:
            raise SpecificationRejectedError(
                f"input {ref.name!r}: cannot read {ref.source}: {exc.strerror}"
            ) from exc
        target.write_bytes(content)
    if common.exists():
        # Trailing slash: the directory's contents, keeping sub-directories.
        staged.append(f"{common}/")
    return staged


def stage_copy(spec: SubmissionSpec, copy: CopyVars, directory: Path) -> CopyPlan:
    """Give one copy its own directory, holding only its secrets (0600)."""
    directory.mkdir(mode=0o700)
    inputs: list[str] = []
    if copy.secrets:
        secrets = directory / "secrets"
        secrets.mkdir(mode=0o700)
        for name, secret in copy.secrets.items():
            target = secrets / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_private(target, secret.get_secret_value())  # HTCondor keeps the mode
        inputs.append("secrets/")
    return CopyPlan(
        initialdir=str(directory),
        environment={**spec.environment, **copy.environment},
        inputs=inputs,
    )


def group_by_environment(
    spec: SubmissionSpec, copies: Sequence[CopyVars]
) -> list[list[int]]:
    """Group copy indexes by the names of their environment variables.

    HTCondor merges each process's environment onto the previous one, so a
    variable of one copy would leak into the next in the same cluster: copies
    with different names must go to different clusters.
    """
    groups: dict[frozenset[str], list[int]] = {}
    for index, copy in enumerate(copies):
        names = frozenset({*spec.environment, *copy.environment})
        groups.setdefault(names, []).append(index)
    return list(groups.values())


# -- what HTCondor runs and returns ---------------------------------------------


def command(
    spec: SubmissionSpec, launcher: Sequence[str] | None, resource: str
) -> tuple[str, bool, list[str]]:
    """Return what HTCondor runs: the executable, whether to ship it, and its arguments.

    A file on this host is shipped; anything else is a command on the worker.
    A container image is run through the configured launcher.
    """
    executable = spec.executable
    arguments = list(spec.arguments)
    is_local_file = Path(spec.executable).is_file()

    if spec.container is None:
        return executable, is_local_file, arguments

    if launcher is None:
        raise SpecificationRejectedError(
            f"{resource} has no container_launcher configured, so it cannot run "
            f"{spec.container.image!r}"
        )

    if is_local_file:
        raise SpecificationRejectedError(
            "a container job runs a command inside the image, not a local file"
        )

    launcher_args = [arg.replace("{image}", spec.container.image) for arg in launcher]

    return (launcher_args[0], False, [*launcher_args[1:], executable, *arguments])


def outputs(spec: SubmissionSpec) -> tuple[list[str] | None, dict[str, str]]:
    """Return output files to retrieve and files to remap.

    None means retrieve all outputs. Remaps map output names to destinations.
    """
    if spec.outputs is None:
        return [], {}

    include_unlisted = spec.outputs.include_unlisted
    kept: list[str] = []
    remaps: dict[str, str] = {}

    for member in spec.outputs.members:
        pattern = member.pattern
        destination = member.destination

        if _GLOB.search(pattern):
            if not include_unlisted or destination is not None:
                raise SpecificationRejectedError(
                    f"output {pattern!r}: "
                    "HTCondor selects outputs by name, not by pattern"
                )
            continue  # include_unlisted brings it back anyway

        kept.append(pattern)

        if destination is not None:
            remaps[pattern] = destination  # uploaded by the resource

    return (None if include_unlisted else kept), remaps
