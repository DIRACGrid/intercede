"""The contract operations of the HTCondor-CE backend."""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
import weakref
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from intercede._backends._htcondor import status
from intercede._backends._htcondor.submit import SubmitPlan, render
from intercede._backends.credentials import credential_requirements, refresh_if_stale
from intercede._backends.monolith.htcondorce import collect, staging
from intercede._backends.monolith.htcondorce.process import Runner, run_process
from intercede._backends.monolith.htcondorce.session import (
    CondorSession,
    credential_files,
    failure,
)
from intercede.auth.models import BearerToken
from intercede.auth.protocols import CredentialProvider
from intercede.config import HTCondorCEConfig
from intercede.exceptions import InterCEdeError, SpecificationRejectedError
from intercede.models import (
    CopyVars,
    JobHandle,
    JobID,
    JobOutput,
    JobStatus,
    OpOutcome,
    StatusMap,
    Submission,
    SubmissionSpec,
)
from intercede.protocols import JobBackend, Killable, OutputRetriever, Purgeable

BACKEND = "htcondor-ce"
DEFAULT_PORT = 9619
PARALLEL_TRANSFERS = 4

# Token scopes per operation (WLCG token profile), so a status poller carries
# a read scope and nothing that would let it submit.
SUBMIT_SCOPES = frozenset({"compute.create"})
READ_SCOPES = frozenset({"compute.read"})
CANCEL_SCOPES = frozenset({"compute.cancel"})

_ACTIVE = frozenset({JobStatus.SUBMITTED, JobStatus.WAITING, JobStatus.RUNNING})
_JOB_ID = re.compile(r"^\d+\.\d+$")
# Attributes get_output() needs on top of the state.
_OUTPUT_ATTRIBUTES = (*status.STATE_ATTRIBUTES, "Out", "Err", "UserLog", "SUBMIT_Iwd")


class HTCondorCEBackend:
    """An HTCondor-CE: submit, status, output, kill and purge.

    It has no live diagnostics (issue #23); ``Diagnosable`` is absent on purpose.
    """

    def __init__(
        self,
        settings: HTCondorCEConfig,
        credentials: CredentialProvider,
        *,
        runner: Runner = run_process,
        workdir: Path | None = None,
    ) -> None:
        # Construction is cheap and does no I/O.
        self._settings = settings
        self._provider = credentials
        self._run = runner
        self._workdir = workdir or staging.default_workdir()
        self._host, port = split_endpoint(settings.endpoint)
        self._pool = f"{self._host}:{port}"
        # The CE's schedd is named after its host unless the site says otherwise.
        self._name = settings.schedd or self._host
        self._tokens: dict[frozenset[str], BearerToken] = {}  # per scope set
        # Only jobs being collected right now: a lock disappears with its last
        # user, so the backend keeps no record of the jobs it has seen.
        self._collecting: weakref.WeakValueDictionary[JobID, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._closed = False

    @classmethod
    def from_settings(
        cls, settings: HTCondorCEConfig, credentials: CredentialProvider
    ) -> Self:
        """Build the backend from its settings; how the registry constructs it.

        Args:
            settings: The CE's validated configuration.
            credentials: The consumer's provider, asked for a token when an
                operation first needs one.

        Returns:
            A new backend, not yet opened.
        """
        return cls(settings, credentials)

    @property
    def resource(self) -> str:
        """The CE this backend talks to; the ``resource`` of its job handles."""
        return self._settings.endpoint

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> Self:
        self._closed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Idempotent. Cached tokens are dropped; they were never written anywhere.
        self._closed = True
        self._tokens.clear()

    # -- Submitter ---------------------------------------------------------

    async def submit(
        self, spec: SubmissionSpec, copies: int | Sequence[CopyVars] = 1
    ) -> Submission:
        self._check_usable()
        copy_vars = [CopyVars()] * copies if isinstance(copies, int) else list(copies)
        if not copy_vars:
            return Submission(handles={})
        for index, copy in enumerate(copy_vars):
            # A secret and an input would be staged to the same file.
            if clashes := spec.clashes_with(copy):
                raise SpecificationRejectedError(
                    f"copy {index}: secrets {sorted(clashes)} are named like inputs"
                )
        outputs, remaps = staging.outputs(spec)
        executable, ship, arguments = staging.command(
            spec, self._settings.container_launcher, self.resource
        )

        handles: dict[int, JobHandle] = {}
        failures: dict[int, str] = {}
        async with self._session(SUBMIT_SCOPES) as session:
            submission = self._workdir / uuid.uuid4().hex
            submission.mkdir(mode=0o700)
            try:
                inputs = staging.stage_inputs(spec, submission)
                for group in staging.group_by_environment(spec, copy_vars):
                    plan = SubmitPlan(
                        executable=executable,
                        transfer_executable=ship,
                        arguments=arguments,
                        copies=[
                            staging.stage_copy(spec, copy_vars[i], submission / str(i))
                            for i in group
                        ],
                        inputs=inputs,
                        stdout=spec.stdout or SubmitPlan.stdout,
                        stderr=spec.stderr or SubmitPlan.stderr,
                        outputs=outputs,
                        output_remaps=remaps,
                        cpus=spec.resources.cpus,
                        memory_mb=spec.resources.memory_mb,
                        wall_time_s=spec.resources.wall_time_s,
                        queue=spec.resources.queue or self._settings.queue,
                    )
                    description = submission / f"group-{group[0]}.sub"
                    description.write_text(render(plan))
                    try:
                        ids = await session.submit(description, len(group))
                    except InterCEdeError as exc:
                        if not handles:
                            raise  # nothing submitted: the whole operation failed
                        failures.update({i: str(exc) for i in group})
                        continue
                    for index, job_id in zip(group, ids):
                        handles[index] = JobHandle(
                            backend=BACKEND, resource=self.resource, id=job_id
                        )
            finally:
                # Inputs are spooled to the CE by now; secrets must not linger here.
                shutil.rmtree(submission, ignore_errors=True)
        return Submission(handles=handles, failures=failures)

    # -- StatusReporter ----------------------------------------------------

    async def get_status(self, handles: Sequence[JobHandle]) -> StatusMap:
        self._check_usable()
        ads = await self._ads(self._ids(handles), status.STATE_ATTRIBUTES, history=True)
        return {
            handle: status.job_status(ads[handle.id])
            if self._owns(handle) and handle.id in ads
            else JobStatus.UNKNOWN
            for handle in handles
        }

    # -- OutputRetriever ---------------------------------------------------

    async def get_output(
        self, handles: Sequence[JobHandle], dest: Path
    ) -> Mapping[JobHandle, OpOutcome[JobOutput]]:
        self._check_usable()
        ids = self._ids(handles)
        queued = await self._ads(ids, _OUTPUT_ATTRIBUTES, history=False)
        gone = await self._ads(
            [i for i in ids if i not in queued], status.STATE_ATTRIBUTES, history=True
        )

        results: dict[JobHandle, OpOutcome[JobOutput]] = {}
        ready: list[tuple[JobHandle, Mapping[str, Any]]] = []
        for handle in handles:
            ad = queued.get(handle.id) if self._owns(handle) else None
            if ad is not None and status.is_removed(ad):
                # Purged a moment ago: released, though not out of the queue yet.
                results[handle] = OpOutcome[JobOutput](
                    ok=False, reason="the job was purged; its output is gone"
                )
            elif ad is None:
                known = self._owns(handle) and handle.id in gone
                reason = (
                    "the job has left the queue; its output is gone"
                    if known
                    else "unknown job"
                )
                results[handle] = OpOutcome[JobOutput](ok=False, reason=reason)
            elif status.job_status(ad) in _ACTIVE:
                results[handle] = OpOutcome[JobOutput](
                    ok=False, reason="job has not finished"
                )
            else:
                ready.append((handle, ad))

        if ready:
            limit = asyncio.Semaphore(PARALLEL_TRANSFERS)
            async with self._session(READ_SCOPES) as session:

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
        if not collect.inside_workdir(iwd, self._workdir):
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
                target = dest / self._host / handle.id
                return collect.move_output(iwd, ad, target, self._settings)
            finally:
                shutil.rmtree(iwd, ignore_errors=True)

    # -- Killable / Purgeable ----------------------------------------------

    async def kill(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        self._check_usable()
        ids = self._ids(handles)
        ads = await self._ads(ids, status.STATE_ATTRIBUTES, history=True)
        # Only active jobs are removed: removing a finished job would release
        # output that has not been collected yet. That is purge()'s job.
        active = [i for i in ids if i in ads and status.job_status(ads[i]) in _ACTIVE]
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
        self._check_usable()
        ids = self._ids(handles)
        queued = await self._ads(ids, status.STATE_ATTRIBUTES, history=False)
        gone = await self._ads(
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
        if not self._owns(handle) or handle.id not in ads:
            return OpOutcome[None](ok=False, reason="unknown job")
        if handle.id not in targeted:
            return OpOutcome[None](ok=True, reason=untouched)
        if handle.id in removed:
            return OpOutcome[None](ok=True)
        return OpOutcome[None](
            ok=False, reason=reason or "condor_rm did not remove the job"
        )

    # -- talking to the CE ----------------------------------------------------

    @asynccontextmanager
    async def _session(self, scopes: frozenset[str]) -> AsyncGenerator[CondorSession]:
        """Open the tools for one operation, with a token scoped to it."""
        token = await self._fresh(scopes)
        workdir = staging.ensure_workdir(self._workdir)
        async with credential_files(token, workdir) as files:
            yield CondorSession(self._run, files, self._pool, self._name)

    async def _fresh(self, scopes: frozenset[str]) -> BearerToken:
        requirements = credential_requirements(self._settings, scopes)
        assert requirements is not None  # an HTCondor-CE always asks for a token
        fresh = await refresh_if_stale(
            self._tokens.get(scopes), requirements, self._provider
        )
        assert isinstance(fresh, BearerToken)  # refresh_if_stale checked the kind
        self._tokens[scopes] = fresh
        return fresh

    async def _ads(
        self, ids: Sequence[JobID], attributes: Sequence[str], *, history: bool
    ) -> dict[JobID, Mapping[str, Any]]:
        if not ids:
            return {}
        async with self._session(READ_SCOPES) as session:
            return await session.ads(ids, attributes, history=history)

    async def _remove(self, ids: Sequence[JobID]) -> tuple[set[JobID], str]:
        if not ids:
            return set(), ""
        async with self._session(CANCEL_SCOPES) as session:
            return await session.remove(ids)

    # -- helpers -------------------------------------------------------------

    def _check_usable(self) -> None:
        if self._closed:
            raise InterCEdeError("backend is closed")

    def _owns(self, handle: JobHandle) -> bool:
        return (
            handle.backend == BACKEND
            and handle.resource == self.resource
            and bool(_JOB_ID.match(handle.id))
        )

    def _ids(self, handles: Iterable[JobHandle]) -> list[JobID]:
        """Return the ids to ask the CE about: ours, well-formed, each once.

        Ids come from stored handles, so they are checked before they reach a
        command line: a malformed one could otherwise pass as an option.
        """
        return list(
            dict.fromkeys(handle.id for handle in handles if self._owns(handle))
        )


def split_endpoint(endpoint: str) -> tuple[str, int]:
    """Split ``host[:port]``; an HTCondor-CE listens on 9619 by default."""
    host, separator, port = endpoint.partition(":")
    return host, int(port) if separator else DEFAULT_PORT


# The type checker proves conformance.
_backend: type[JobBackend] = HTCondorCEBackend
_output: type[OutputRetriever] = HTCondorCEBackend
_kill: type[Killable] = HTCondorCEBackend
_purge: type[Purgeable] = HTCondorCEBackend
_built_by_registry: Callable[[HTCondorCEConfig, CredentialProvider], JobBackend] = (
    HTCondorCEBackend.from_settings
)
