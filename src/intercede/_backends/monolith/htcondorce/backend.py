"""The HTCondor-CE backend: one module per capability, joined here.

Each operation is in a module of its own (``submit``, ``output``, ``cancel``), and
all of them share ``jobs``: which handles are this backend's, and what the CE
knows of them. This class builds them, and checks the backend is open before
each operation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

from intercede._backends.monolith.htcondorce import staging
from intercede._backends.monolith.htcondorce.cancel import JobCanceller
from intercede._backends.monolith.htcondorce.jobs import Jobs
from intercede._backends.monolith.htcondorce.output import OutputCollector
from intercede._backends.monolith.htcondorce.process import Runner, run_process
from intercede._backends.monolith.htcondorce.submit import JobSubmitter
from intercede.auth.protocols import CredentialProvider
from intercede.config import HTCondorCEConfig
from intercede.exceptions import InterCEdeError
from intercede.models import (
    CopyVars,
    JobHandle,
    JobOutput,
    OpOutcome,
    StatusMap,
    Submission,
    SubmissionSpec,
)
from intercede.protocols import JobBackend, Killable, OutputRetriever, Purgeable


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
        self._jobs = Jobs(
            settings, credentials, runner, workdir or staging.default_workdir()
        )
        self._submitter = JobSubmitter(settings, self._jobs)
        self._collector = OutputCollector(settings, self._jobs)
        self._canceller = JobCanceller(self._jobs)
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
        self._jobs.forget_tokens()

    def _check_usable(self) -> None:
        if self._closed:
            raise InterCEdeError("backend is closed")

    # -- the capabilities --------------------------------------------------

    async def submit(
        self, spec: SubmissionSpec, copies: int | Sequence[CopyVars] = 1
    ) -> Submission:
        self._check_usable()
        return await self._submitter.submit(spec, copies)

    async def get_status(self, handles: Sequence[JobHandle]) -> StatusMap:
        self._check_usable()
        return await self._jobs.status(handles)

    async def get_output(
        self, handles: Sequence[JobHandle], dest: Path
    ) -> Mapping[JobHandle, OpOutcome[JobOutput]]:
        self._check_usable()
        return await self._collector.get_output(handles, dest)

    async def kill(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        self._check_usable()
        return await self._canceller.kill(handles)

    async def purge(
        self, handles: Sequence[JobHandle]
    ) -> Mapping[JobHandle, OpOutcome[None]]:
        self._check_usable()
        return await self._canceller.purge(handles)


# The type checker proves conformance.
_backend: type[JobBackend] = HTCondorCEBackend
_output: type[OutputRetriever] = HTCondorCEBackend
_kill: type[Killable] = HTCondorCEBackend
_purge: type[Purgeable] = HTCondorCEBackend
_built_by_registry: Callable[[HTCondorCEConfig, CredentialProvider], JobBackend] = (
    HTCondorCEBackend.from_settings
)
