"""The argv of each HTCondor tool, and parsers for what they print.

``remote`` is how a tool reaches its schedd: ``schedd_options(pool, name)``
for a remote schedd, as an HTCondor-CE is reached, or ``()`` for the schedd
of the host the tool runs on, as the composed backend reaches it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from intercede.models import JobID

# Ids per command line, so a status poll of thousands of jobs stays bounded.
CHUNK = 100


def schedd_options(pool: str, name: str) -> list[str]:
    """Reach the schedd ``name`` through the collector ``pool`` (host:port)."""
    return ["-pool", pool, "-name", name]


def submit(
    description: str, pool: str | None = None, name: str | None = None
) -> list[str]:
    """Submit a description file and spool its inputs to the schedd.

    Spooling copies the inputs at submit time, so nothing on the submitting
    host is needed afterwards. With ``pool`` and ``name`` the schedd is a
    remote one (``-remote`` implies spooling); without, it is the local one.
    """
    if (pool is None) != (name is None):
        raise ValueError("a remote schedd needs both pool and name")

    command = ["condor_submit", "-terse"]

    if pool is None or name is None:
        command.append("-spool")
    else:
        command.extend(["-pool", pool, "-remote", name])

    command.append(description)
    return command


def query(
    ids: Sequence[JobID], attributes: Sequence[str], remote: Sequence[str]
) -> list[str]:
    """Read the ads of jobs still in the queue."""
    return ["condor_q", *remote, *ids, "-attributes", ",".join(attributes), "-json"]


def history(
    ids: Sequence[JobID], attributes: Sequence[str], remote: Sequence[str]
) -> list[str]:
    """Read the ads of jobs that have left the queue."""
    return [
        "condor_history",
        *remote,
        *ids,
        "-attributes",
        ",".join(attributes),
        "-json",
    ]


def remove(ids: Sequence[JobID], remote: Sequence[str]) -> list[str]:
    """Remove jobs: cancels active ones, releases finished ones."""
    return ["condor_rm", *remote, *ids]


def transfer_data(ids: Sequence[JobID], remote: Sequence[str]) -> list[str]:
    """Fetch the spooled output of jobs into the directory they were submitted from."""
    return ["condor_transfer_data", *remote, *ids]


def chunks(ids: Sequence[JobID]) -> list[Sequence[JobID]]:
    """Split ids into groups of at most ``CHUNK``."""
    return [ids[start : start + CHUNK] for start in range(0, len(ids), CHUNK)]


_TERSE = re.compile(
    r"\s*(?P<cluster>\d+)\.(?P<first>\d+)"
    r"\s*-\s*"
    r"(?P<last_cluster>\d+)\.(?P<last>\d+)\s*"
)


def parse_submit(raw: str) -> list[JobID]:
    """Parse job IDs from condor_submit -terse output, e.g. '12.0 - 12.3'."""
    lines = [line for line in raw.splitlines() if line.strip()]

    if len(lines) != 1:
        raise ValueError(f"unexpected condor_submit output: {raw.strip()!r}")

    match = _TERSE.fullmatch(lines[0])
    if match is None:
        raise ValueError(f"unexpected condor_submit output: {raw.strip()!r}")

    cluster = int(match["cluster"])
    first = int(match["first"])
    last_cluster = int(match["last_cluster"])
    last = int(match["last"])

    if cluster != last_cluster or last < first:
        raise ValueError(f"unexpected condor_submit output: {raw.strip()!r}")

    return [JobID(f"{cluster}.{proc}") for proc in range(first, last + 1)]


_REMOVED = re.compile(r"^Job (\d+\.\d+) marked for removal", re.MULTILINE)


def parse_remove(raw: str) -> set[JobID]:
    """Return the ids ``condor_rm`` reports as removed; any other id was not."""
    return {JobID(job) for job in _REMOVED.findall(raw)}
