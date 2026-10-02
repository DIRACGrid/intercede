"""Data types of the InterCEde contract (IC-ADR-001 §2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Generic, Literal, NewType, Self, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    PositiveInt,
    SecretBytes,
    model_validator,
)

__all__ = [
    "ContainerSpec",
    "CopyVars",
    "Diagnostics",
    "FileRef",
    "JobHandle",
    "JobID",
    "JobOutput",
    "JobStatus",
    "OpOutcome",
    "OutputMember",
    "OutputSpec",
    "Resources",
    "StatusMap",
    "Submission",
    "SubmissionSpec",
]

T = TypeVar("T")


class _Model(BaseModel):
    """Base for every contract type: immutable, and strict about unknown fields.

    Values arrive from configuration and from stored handles, so they are
    validated rather than trusted (IC-ADR-001 §2).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_in_workdir(name: str) -> str:
    """Reject names that would leave the job's working directory (IC-ADR-001 §2, §3)."""
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or path == PurePosixPath(".")
    ):
        raise ValueError(
            f"{name!r} must be a relative path inside the working directory"
        )
    return name


WorkdirName = Annotated[str, AfterValidator(_check_in_workdir)]


# --------------------------------------------------------------------------
# What you submit
# --------------------------------------------------------------------------


class Resources(_Model):
    """What a job asks of the resource."""

    cpus: PositiveInt = 1 # resources must never be 0 or negative, and will be rejected
    memory_mb: PositiveInt | None = None
    wall_time_s: PositiveInt | None = None
    queue: str | None = None


class FileRef(_Model):
    """An input file: its name in the working directory, and where it comes from."""

    name: WorkdirName
    source: str  # a local path (staged by InterCEde) or a URL the resource fetches


class ContainerSpec(_Model):
    """The image the resource runs the executable in. InterCEde never pulls, reads or builds it."""

    image: str  # "docker://...", a .sif path or URL, "oras://..."


class OutputMember(_Model):
    """An output file, and who moves it."""

    pattern: WorkdirName  # a name or a glob in the working directory
    destination: str | None = (
        None  # None: kept for get_output(); URL: uploaded by the resource
    )


class OutputSpec(_Model):
    """Which outputs a job produces."""

    members: Sequence[OutputMember] = ()
    include_unlisted: bool = False  # never picks up stdout, stderr or the scheduler log


class CopyVars(_Model):
    """What differs between the copies of one specification (IC-ADR-001 §2.1)."""

    environment: Mapping[str, str] = {}  # identifiers; readable by the site
    # File name -> content, staged at 0600 and never put in the job description.
    # SecretBytes keeps the content out of repr(), logs and tracebacks.
    secrets: Mapping[WorkdirName, SecretBytes] = {}


class SubmissionSpec(_Model):
    """What every copy of a submission shares."""

    executable: str
    arguments: Sequence[str] = ()
    inputs: Sequence[FileRef] = ()
    outputs: OutputSpec = OutputSpec()
    resources: Resources = Resources()
    environment: Mapping[str, str] = {}
    container: ContainerSpec | None = None
    stdout: WorkdirName | None = None  # the backend picks a unique name when None
    stderr: WorkdirName | None = None
    tag: str | None = None  # consumer token for correlation and debugging only


# --------------------------------------------------------------------------
# What comes back
# --------------------------------------------------------------------------

JobID = NewType(
    "JobID", str
)  # the resource's own job ID, unique only within that resource


class JobHandle(_Model):
    """The durable, serialisable identity of one submitted job.

    The consumer stores it and passes it back into later calls. It carries
    everything the backend needs to find the job again, and nothing secret.
    Handles are hashable, and every per-job result map is keyed by them: a
    ``JobID`` alone is not unique across resources.

    Unknown fields are rejected, so a handle written by a newer format fails
    loudly instead of losing its routing; ``version`` is what a newer reader
    branches on.
    """

    version: Literal[1] = 1  # format of the handle itself, for stored handles
    backend: str  # the configuration kind, e.g. "arc" or "batch"
    resource: str  # endpoint or host; same string as CredentialRequirements.resource
    id: JobID
    routing: Mapping[str, str] = {}  # backend-specific, opaque to consumers

    def __hash__(self) -> int:
        # The default hash of a frozen model fails on the routing dict.
        return hash(
            (
                self.version,
                self.backend,
                self.resource,
                self.id,
                frozenset(self.routing.items()),
            )
        )


class JobStatus(StrEnum):
    """Common job states.

    Only UNKNOWN is fixed by the ADR. The other values are a proposal, aligned
    with DIRAC's pilot states; confirm before release.
    """

    SUBMITTED = "Submitted"
    WAITING = "Waiting"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    ABORTED = "Aborted"
    UNKNOWN = "Unknown"  # the backend no longer knows the job; never dropped


StatusMap = Mapping[JobHandle, JobStatus]


class Submission(_Model):
    """The outcome of submit().

    Both maps are keyed by copy index, so ``handles[i]`` belongs to ``copies[i]``
    even when other copies were refused. Every copy appears in exactly one map.
    """

    handles: Mapping[int, JobHandle]
    failures: Mapping[int, str] = {}  # copy index -> reason, for refused copies

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        if both := self.handles.keys() & self.failures.keys():
            raise ValueError(f"copies {sorted(both)} are both submitted and refused")
        return self


class JobOutput(_Model):
    """A manifest of collected output: paths, never file contents."""

    stdout: Path | None = None
    stderr: Path | None = None
    log: Path | None = None  # the scheduler or CE log, never a payload file
    files: Mapping[str, Path] = {}  # payload members only


class Diagnostics(_Model):
    """A file read from a job before it completes.

    The fields are a proposal; the ADR does not fix them.
    """

    name: str
    content: str


class OpOutcome(_Model, Generic[T]):
    """The outcome of an operation for one job (IC-ADR-001 §3).

    A failed outcome carries a ``reason`` and no value. A successful one carries
    a value, except for operations that return nothing (``OpOutcome[None]``);
    it may still give a ``reason``, such as "already finished".
    """

    ok: bool
    value: T | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.ok:
            if self.reason is None:
                raise ValueError("a failed outcome needs a reason")
            if self.value is not None:
                raise ValueError("a failed outcome cannot carry a value")
        elif self.value is None and self._expects_value():
            raise ValueError("a successful outcome needs a value")
        return self

    @classmethod
    def _expects_value(cls) -> bool:
        args = cls.__pydantic_generic_metadata__["args"]
        return bool(args) and args[0] not in (None, type(None))
