"""From HTCondor job ads to ``JobStatus``.

Reading only: the state is taken from what HTCondor records about a job, never
from a policy interCEde sets on it. A finished job reads as FAILED when its
payload exited non-zero or was killed by a signal, from ``ExitCode`` and
``ExitBySignal``.

Ported from DIRAC's ``BatchSystems/Condor.py`` (``STATES_MAP`` and
``getCondorStatus``), with differences: DIRAC puts a payload that fails on
hold to recognise it, where the exit code is read here instead; the states
DIRAC leaves unmapped (6, 7) are mapped; and a removed job reports what it did
before it was removed, so a purged job keeps its result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from intercede.models import JobID, JobStatus

# The ad attributes every status query asks for.
STATE_ATTRIBUTES: Sequence[str] = (
    "ClusterId",
    "ProcId",
    "JobStatus",
    "LastJobStatus",
    "ExitCode",
    "ExitBySignal",
    "HoldReasonCode",
    "HoldReason",
)

# HTCondor's JobStatus codes.
_IDLE, _RUNNING, _REMOVED, _COMPLETED, _HELD, _TRANSFERRING, _SUSPENDED = range(1, 8)

_STATES: Mapping[int, JobStatus] = {
    _IDLE: JobStatus.WAITING,
    _RUNNING: JobStatus.RUNNING,
    _TRANSFERRING: JobStatus.RUNNING,  # transferring output after running
    _SUSPENDED: JobStatus.WAITING,
}

_HOLD_SPOOLING = 16  # HoldReasonCode: input files are still being spooled


def job_id(ad: Mapping[str, Any]) -> JobID:
    """Return the ``cluster.proc`` id of a job ad."""
    return JobID(f"{ad['ClusterId']}.{ad['ProcId']}")


def job_status(ad: Mapping[str, Any]) -> JobStatus:
    """Map one job ad to the common status."""
    code = ad.get("JobStatus")
    if code == _COMPLETED:
        return _result(ad)
    if code == _REMOVED:
        # purge() removes finished jobs to release them, after which the
        # history only says "removed": the state it left tells the result.
        return (
            _result(ad) if ad.get("LastJobStatus") == _COMPLETED else JobStatus.ABORTED
        )
    if code == _HELD:
        # Held by the site or by HTCondor, not by interCEde: the job is stuck,
        # except while its inputs are still being spooled.
        return (
            JobStatus.WAITING
            if ad.get("HoldReasonCode") == _HOLD_SPOOLING
            else JobStatus.ABORTED
        )
    return (
        _STATES.get(code, JobStatus.UNKNOWN)
        if isinstance(code, int)
        else JobStatus.UNKNOWN
    )


def is_removed(ad: Mapping[str, Any]) -> bool:
    """Tell whether a job was removed, even while it is still in the queue.

    ``condor_rm`` only marks a job for removal: the schedd drops it from the
    queue a moment later. In between it is already released.

    Args:
        ad: A job ad with ``JobStatus``.

    Returns:
        True when the job has been removed.

    """
    return ad.get("JobStatus") == _REMOVED


def _result(ad: Mapping[str, Any]) -> JobStatus:
    """Tell DONE from FAILED for a job whose payload has finished."""
    if ad.get("ExitBySignal") is True:
        return JobStatus.FAILED
    exit_code = ad.get("ExitCode")
    return (
        JobStatus.FAILED
        if isinstance(exit_code, int) and exit_code != 0
        else JobStatus.DONE
    )


def hold_reason(ad: Mapping[str, Any]) -> str | None:
    """Return why a held job is held, if it is."""
    reason = ad.get("HoldReason")
    return reason if ad.get("JobStatus") == _HELD and isinstance(reason, str) else None


def parse_ads(raw: str) -> list[dict[str, Any]]:
    """Parse ``condor_q -json`` or ``condor_history -json`` output.

    ``condor_q`` prints nothing at all when no job matches, and
    ``condor_history`` prints an empty list.
    """
    if not raw.strip():
        return []
    ads = json.loads(raw)
    if not isinstance(ads, list) or not all(isinstance(ad, dict) for ad in ads):
        raise ValueError("expected a JSON list of job ads")
    return ads
