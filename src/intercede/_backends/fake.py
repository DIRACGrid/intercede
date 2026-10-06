"""An in-memory backend that implements the whole contract.

It proves the contract is easy to implement and to fake, and it is what the
unit conformance suite runs against. Nothing leaves the process.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

from intercede.exceptions import (
    InterCEdeError,
    ResourceUnavailableError,
    SpecificationRejectedError,
)
from intercede.models import (
    CopyVars,
    Diagnostics,
    FileRef,
    JobHandle,
    JobID,
    JobOutput,
    JobStatus,
    OpOutcome,
    OutputMember,
    StatusMap,
    Submission,
    SubmissionSpec,
)
from intercede.protocols import (
    Diagnosable,
    JobBackend,
    Killable,
    OutputRetriever,
    Purgeable,
)

BACKEND = "fake"
_ACTIVE = (JobStatus.SUBMITTED, JobStatus.WAITING, JobStatus.RUNNING)


def _is_url(source: str) -> bool:
    return "://" in source


@dataclass
class _FakeJob:
    environment: Mapping[str, str]
    inputs: dict[str, bytes]  # contents, read at submission (see submit)
    secrets: dict[str, bytes]
    status: JobStatus = JobStatus.SUBMITTED
    stdout: str = ""
    stderr: str = ""
    files: dict[str, bytes] = field(default_factory=dict)


class FakeBackend:
    """In-memory backend with every capability, and hooks for tests.

    Args:
        resource: Identity of the fake resource, stored in every handle.
        refuse_copies: Copy indexes that ``submit`` refuses, to test partial submission.
        fail_jobs: Job IDs for which later operations fail, to test per-job failures.
        resource_staging: Whether the resource moves URL inputs and outputs itself;
            when False, a spec that needs it is rejected at submit.
        containers: Whether the resource can run a container image; when False, a
            spec that asks for one is rejected at submit.

    """

    def __init__(
        self,
        resource: str = "fake://local",
        *,
        refuse_copies: Collection[int] = (),
        fail_jobs: Collection[str] = (),
        resource_staging: bool = True,
        containers: bool = True,
    ) -> None:
        # Construction is cheap and does no I/O (IC-ADR-001 §3).
        self.resource = resource
        self.refuse_copies = set(refuse_copies)
        self.fail_jobs = set(fail_jobs)
        self.resource_staging = resource_staging
        self.containers = containers
        self.unavailable = False  # set to True to make whole operations fail
        self._jobs: dict[JobID, _FakeJob] = {}
        self._next_id = 0
        self._closed = False

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
        self._closed = True  # idempotent: closing twice is harmless

    def _check_usable(self) -> None:
        if self._closed:
            raise InterCEdeError("backend is closed")
        if self.unavailable:
            raise ResourceUnavailableError(f"{self.resource} is unavailable")

    # -- essential capabilities -------------------------------------------

    async def submit(
        self, spec: SubmissionSpec, copies: int | Sequence[CopyVars] = 1
    ) -> Submission:
        self._check_usable()
        self._check_supported(spec)
        copy_vars = [CopyVars()] * copies if isinstance(copies, int) else list(copies)

        # File ownership: read local inputs now, so the backend never depends on
        # caller-owned paths after submit() returns.
        inputs = self._read_local_inputs(spec.inputs)

        handles: dict[int, JobHandle] = {}
        failures: dict[int, str] = {}
        for index, copy in enumerate(copy_vars):
            if index in self.refuse_copies:
                failures[index] = "refused by the fake backend"
                continue
            job_id = JobID(str(self._next_id))
            self._next_id += 1
            self._jobs[job_id] = _FakeJob(
                environment={**spec.environment, **copy.environment},
                inputs=dict(inputs),
                secrets={
                    name: secret.get_secret_value()
                    for name, secret in copy.secrets.items()
                },
            )
            handles[index] = JobHandle(
                backend=BACKEND, resource=self.resource, id=job_id
            )
        return Submission(handles=handles, failures=failures)

    async def get_status(self, handles: Sequence[JobHandle]) -> StatusMap:
        self._check_usable()

        results: dict[JobHandle, JobStatus] = {}
        for handle in handles:
            job = self._find(handle)
            if job is not None:
                results[handle] = job.status
            else:
                results[handle] = JobStatus.UNKNOWN
        return results

    # -- optional capabilities --------------------------------------------

    async def get_output(
        self, handles: Sequence[JobHandle], dest: Path
    ) -> Mapping[JobHandle, OpOutcome[JobOutput]]:
        self._check_usable()
        results: dict[JobHandle, OpOutcome[JobOutput]] = {}
        for handle in handles:
            job, error = self._lookup(handle)
            if job is None:
                results[handle] = OpOutcome[JobOutput](ok=False, reason=error)
            elif job.status in _ACTIVE:
                results[handle] = OpOutcome[JobOutput](
                    ok=False, reason="job has not finished"
                )
            else:
                results[handle] = self._write_output(handle.id, job, dest)
        return results

    async def kill(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        self._check_usable()
        results: dict[JobHandle, OpOutcome[None]] = {}
        for handle in handles:
            job, error = self._lookup(handle)
            if job is None:
                results[handle] = OpOutcome[None](ok=False, reason=error)
            elif job.status in _ACTIVE:
                job.status = JobStatus.ABORTED
                results[handle] = OpOutcome[None](ok=True)
            else:
                results[handle] = OpOutcome[None](ok=True, reason="already finished")
        return results

    async def purge(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        self._check_usable()
        results: dict[JobHandle, OpOutcome[None]] = {}
        for handle in handles:
            job, error = self._lookup(handle)
            if job is None:
                results[handle] = OpOutcome[None](ok=False, reason=error)
            else:
                del self._jobs[handle.id]
                results[handle] = OpOutcome[None](ok=True)
        return results

    async def get_diagnostics(
        self, handles: Sequence[JobHandle], name: str | None = None
    ) -> Mapping[JobHandle, OpOutcome[Diagnostics]]:
        self._check_usable()
        results: dict[JobHandle, OpOutcome[Diagnostics]] = {}
        for handle in handles:
            job, error = self._lookup(handle)
            if job is None:
                results[handle] = OpOutcome[Diagnostics](ok=False, reason=error)
            else:
                diagnostics = Diagnostics(name=name or "stdout", content=job.stdout)
                results[handle] = OpOutcome[Diagnostics](ok=True, value=diagnostics)
        return results

    # -- test hooks --------------------------------------------------------

    def finish(
        self,
        handle: JobHandle,
        *,
        status: JobStatus = JobStatus.DONE,
        stdout: str = "",
        files: Mapping[str, bytes] | None = None,
    ) -> None:
        """Mark a job as finished, with the given output. Test hook, not part of the contract."""
        job = self._jobs[handle.id]
        job.status = status
        job.stdout = stdout
        job.files = dict(files or {})

    # -- helpers -----------------------------------------------------------

    def _check_supported(self, spec: SubmissionSpec) -> None:
        # Refuse at submit what the resource cannot honour (IC-ADR-001 §2.3, §2.4).
        self._check_container_support(spec)

        if not self.resource_staging:
            self._check_can_fetch(spec.inputs)
            self._check_can_upload(spec.outputs.members)

    def _check_container_support(self, spec: SubmissionSpec) -> None:
        if spec.container is not None and not self.containers:
            raise SpecificationRejectedError(
                f"{self.resource} cannot run container images"
            )

    def _check_can_fetch(self, inputs: Sequence[FileRef]) -> None:
        for ref in inputs:
            if _is_url(ref.source):
                raise SpecificationRejectedError(
                    f"{self.resource} cannot fetch inputs from URLs"
                )

    def _check_can_upload(self, members: Sequence[OutputMember]) -> None:
        for member in members:
            if member.destination is not None:
                raise SpecificationRejectedError(
                    f"{self.resource} cannot upload outputs to URLs"
                )

    def _find(self, handle: JobHandle) -> _FakeJob | None:
        if handle.backend != BACKEND or handle.resource != self.resource:
            return None  # a handle for another backend is never ours
        return self._jobs.get(handle.id)

    def _lookup(self, handle: JobHandle) -> tuple[_FakeJob | None, str | None]:
        if handle.id in self.fail_jobs:
            return None, "injected failure"
        job = self._find(handle)
        if job is not None:
            return (job, None)
        else:
            return (None, "unknown job")

    @staticmethod
    def _read_local_inputs(inputs: Sequence[FileRef]) -> dict[str, bytes]:
        contents: dict[str, bytes] = {}
        for ref in inputs:
            if not _is_url(ref.source):
                contents[ref.name] = Path(ref.source).read_bytes()
        return contents

    @staticmethod
    def _write_output(job_id: JobID, job: _FakeJob, dest: Path,) -> OpOutcome[JobOutput]:
        job_dir = (dest / job_id).resolve()
        files_dir = job_dir / "files"

        targets, escaping = FakeBackend._build_output_targets(files_dir, job.files)

        if escaping:
            return OpOutcome[JobOutput](
                ok=False,
                reason=f"members escape the output directory: {escaping}",
            )

        job_dir.mkdir(parents=True, exist_ok=True)

        stdout = job_dir / "stdout"
        stderr = job_dir / "stderr"

        stdout.write_text(job.stdout)
        stderr.write_text(job.stderr)

        FakeBackend._write_job_files(job.files, targets)

        return OpOutcome[JobOutput](
            ok=True,
            value=JobOutput(
                stdout=stdout,
                stderr=stderr,
                files=targets,
            ),
        )

    @staticmethod
    def _build_output_targets(
        files_dir: Path,
        files: Mapping[str, bytes],
    ) -> tuple[dict[str, Path], list[str]]:
        targets = {}
        escaping = []

        for name in files:
            path = (files_dir / name).resolve()

            if path.is_relative_to(files_dir):
                targets[name] = path
            else:
                escaping.append(name)

        return targets, sorted(escaping)

    @staticmethod
    def _write_job_files(
        files: Mapping[str, bytes],
        targets: Mapping[str, Path],
    ) -> None:
        for name, path in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])

# The type checker proves conformance: these assignments fail mypy if the fake
# ever drifts from the contract (IC-ADR-001 §4).
_backend: type[JobBackend] = FakeBackend
_output: type[OutputRetriever] = FakeBackend
_kill: type[Killable] = FakeBackend
_purge: type[Purgeable] = FakeBackend
_diag: type[Diagnosable] = FakeBackend
