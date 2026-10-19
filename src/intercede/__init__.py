"""interCEde - Unified interfaces to Computing Elements and batch systems for DIRAC / DiracX."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version

from intercede import registry
from intercede.config import BackendConfig
from intercede.exceptions import (
    AuthenticationError,
    ConfigurationError,
    InterCEdeError,
    RegistryError,
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
    "registry",
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
    # configuration
    "BackendConfig",
    # exceptions
    "AuthenticationError",
    "ConfigurationError",
    "InterCEdeError",
    "RegistryError",
    "ResourceUnavailableError",
    "SpecificationRejectedError",
]
