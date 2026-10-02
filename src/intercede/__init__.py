"""interCEde - Unified interfaces to Computing Elements and batch systems for DIRAC / DiracX."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version

from intercede.exceptions import (
    AuthenticationError,
    ConfigurationError,
    InterCEdeError,
    ResourceUnavailableError,
    SpecificationRejectedError,
)
from intercede.models import (
    ContainerSpec,
    CopyVars,
    Diagnostics,
    FileRef,
    JobHandle,
    JobID,
    JobOutput,
    JobStatus,
    OpOutcome,
    OutputMember,
    OutputSpec,
    Resources,
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
    StatusReporter,
    Submitter,
)

try:
    __version__ = get_version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"
version = __version__

__all__ = [
    "__version__",
    # protocols
    "Diagnosable",
    "JobBackend",
    "Killable",
    "OutputRetriever",
    "Purgeable",
    "StatusReporter",
    "Submitter",
    # data types
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
    # exceptions
    "AuthenticationError",
    "ConfigurationError",
    "InterCEdeError",
    "ResourceUnavailableError",
    "SpecificationRejectedError",
]
