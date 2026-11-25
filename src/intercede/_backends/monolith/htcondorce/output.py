"""Collecting a finished job's output from an HTCondor-CE.

``condor_transfer_data`` fetches what the CE spooled into the directory the job
was submitted from, here on this host; ``collect`` then moves it into ``dest``.
"""

from __future__ import annotations

import asyncio
import shutil
import weakref
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from intercede._backends._htcondor import status
from intercede._backends.monolith.htcondorce import collect
from intercede._backends.monolith.htcondorce.jobs import ACTIVE, READ_SCOPES, Jobs
from intercede._backends.monolith.htcondorce.session import CondorSession, failure
from intercede.config import HTCondorCEConfig
from intercede.models import JobHandle, JobID, JobOutput, OpOutcome

PARALLEL_TRANSFERS = 4

# Attributes get_output() needs on top of the state.
_OUTPUT_ATTRIBUTES = (*status.STATE_ATTRIBUTES, "Out", "Err", "UserLog", "SUBMIT_Iwd")


class OutputCollector:
    """Copies the output of finished jobs into a directory of the caller's."""

    def __init__(self, settings: HTCondorCEConfig, jobs: Jobs) -> None:
        self._settings = settings
        self._jobs = jobs
        # Only jobs being collected right now: a lock disappears with its last
        # user, so the backend keeps no record of the jobs it has seen.
        self._collecting: weakref.WeakValueDictionary[JobID, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def get_output(
        self, handles: Sequence[JobHandle], dest: Path
    ) -> Mapping[JobHandle, OpOutcome[JobOutput]]:
        """Collect each finished job's output; see ``OutputRetriever.get_output``."""
        jobs = self._jobs
        ids = jobs.ids(handles)
        queued = await jobs.ads(ids, _OUTPUT_ATTRIBUTES, history=False)
        gone = await jobs.ads(
            [i for i in ids if i not in queued], status.STATE_ATTRIBUTES, history=True
        )

        results: dict[JobHandle, OpOutcome[JobOutput]] = {}
        ready: list[tuple[JobHandle, Mapping[str, Any]]] = []
        for handle in handles:
            ad = queued.get(handle.id) if jobs.owns(handle) else None
            if ad is not None and status.is_removed(ad):
                # Purged a moment ago: released, though not out of the queue yet.
                results[handle] = OpOutcome[JobOutput](
                    ok=False, reason="the job was purged; its output is gone"
                )
            elif ad is None:
                known = jobs.owns(handle) and handle.id in gone
                reason = (
                    "the job has left the queue; its output is gone"
                    if known
                    else "unknown job"
                )
                results[handle] = OpOutcome[JobOutput](ok=False, reason=reason)
            elif status.job_status(ad) in ACTIVE:
                results[handle] = OpOutcome[JobOutput](
                    ok=False, reason="job has not finished"
                )
            else:
                ready.append((handle, ad))

        if ready:
            limit = asyncio.Semaphore(PARALLEL_TRANSFERS)
            async with jobs.session(READ_SCOPES) as session:

                async def fetch(handle: JobHandle, ad: Mapping[str, Any]) -> None:
                    async with limit:
                        results[handle] = await self._collect(session, handle, ad, dest)

                await asyncio.gather(*(fetch(handle, ad) for handle, ad in ready))
        return {handle: results[handle] for handle in handles}  # in the caller's order

    async def _collect(
        self,
        session: CondorSession,
        handle: JobHandle,
        ad: Mapping[str, Any],
        dest: Path,
    ) -> OpOutcome[JobOutput]:
        iwd = Path(str(ad.get("SUBMIT_Iwd", "")))
        # SUBMIT_Iwd comes from the remote job ad: never let the CE choose
        # where on this host we write.
        if not collect.inside_workdir(iwd, self._jobs.workdir):
            return OpOutcome[JobOutput](
                ok=False,
                reason="the job's output directory is outside our working area",
            )
        # Two collections of one job would write into the same directory.
        lock = self._collecting.get(handle.id)
        if lock is None:
            lock = self._collecting[handle.id] = asyncio.Lock()
        async with lock:
            try:
                result = await session.transfer(
                    handle.id, self._settings.transfer_timeout_s
                )
                if result.returncode != 0:
                    return OpOutcome[JobOutput](
                        ok=False, reason=failure(result, "condor_transfer_data")
                    )
                target = dest / self._jobs.host / handle.id
                return collect.move_output(iwd, ad, target, self._settings)
            finally:
                shutil.rmtree(iwd, ignore_errors=True)
