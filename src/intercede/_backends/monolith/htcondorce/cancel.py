"""Killing and purging jobs on an HTCondor-CE, both with ``condor_rm``.

Removing a job cancels it if it runs, and releases what the CE keeps of it. So
``kill`` removes only active jobs, whose output nobody can want yet, and
``purge`` removes every job still in the queue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from intercede._backends._htcondor import status
from intercede._backends.monolith.htcondorce.jobs import ACTIVE, CANCEL_SCOPES, Jobs
from intercede.models import JobHandle, JobID, OpOutcome


class JobCanceller:
    """Kills and purges jobs on the CE."""

    def __init__(self, jobs: Jobs) -> None:
        self._jobs = jobs

    async def kill(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        """Cancel each active job; see ``Killable.kill``."""
        ids = self._jobs.ids(handles)
        ads = await self._jobs.ads(ids, status.STATE_ATTRIBUTES, history=True)
        # Only active jobs are removed: removing a finished job would release
        # output that has not been collected yet. That is purge()'s job.
        active = [i for i in ids if i in ads and status.job_status(ads[i]) in ACTIVE]
        removed, reason = await self._remove(active)
        return {
            handle: self._outcome(
                handle, ads, active, removed, reason, "already finished"
            )
            for handle in handles
        }

    async def purge(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        """Cancel each job and release what the CE keeps; see ``Purgeable.purge``."""
        ids = self._jobs.ids(handles)
        queued = await self._jobs.ads(ids, status.STATE_ATTRIBUTES, history=False)
        gone = await self._jobs.ads(
            [i for i in ids if i not in queued], status.STATE_ATTRIBUTES, history=True
        )
        # A job removed a moment ago is still queued, but already released.
        targeted = [i for i, ad in queued.items() if not status.is_removed(ad)]
        removed, reason = await self._remove(targeted)
        ads = {**gone, **queued}
        return {
            handle: self._outcome(
                handle, ads, targeted, removed, reason, "already released"
            )
            for handle in handles
        }

    def _outcome(
        self,
        handle: JobHandle,
        ads: Mapping[JobID, Mapping[str, Any]],
        targeted: Sequence[JobID],
        removed: set[JobID],
        reason: str,
        untouched: str,
    ) -> OpOutcome[None]:
        if not self._jobs.owns(handle) or handle.id not in ads:
            return OpOutcome[None](ok=False, reason="unknown job")
        if handle.id not in targeted:
            return OpOutcome[None](ok=True, reason=untouched)
        if handle.id in removed:
            return OpOutcome[None](ok=True)
        return OpOutcome[None](
            ok=False, reason=reason or "condor_rm did not remove the job"
        )

    async def _remove(self, ids: Sequence[JobID]) -> tuple[set[JobID], str]:
        if not ids:
            return set(), ""
        async with self._jobs.session(CANCEL_SCOPES) as session:
            return await session.remove(ids)
