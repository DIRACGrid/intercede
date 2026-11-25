"""The CE's jobs as every operation sees them: which are ours, and what the CE knows.

Handles come from the consumer's storage, so ``owns`` checks one before its id
reaches a command line. Every request runs in a ``session``, with a token
scoped to its operation.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from intercede._backends._htcondor import status
from intercede._backends.credentials import credential_requirements, refresh_if_stale
from intercede._backends.monolith.htcondorce import staging
from intercede._backends.monolith.htcondorce.process import Runner
from intercede._backends.monolith.htcondorce.session import (
    CondorSession,
    credential_files,
)
from intercede.auth.models import BearerToken
from intercede.auth.protocols import CredentialProvider
from intercede.config import HTCondorCEConfig
from intercede.models import JobHandle, JobID, JobStatus, StatusMap

BACKEND = "htcondor-ce"
DEFAULT_PORT = 9619

# Token scopes per operation (WLCG token profile), so a status poller carries
# a read scope and nothing that would let it submit.
SUBMIT_SCOPES = frozenset({"compute.create"})
READ_SCOPES = frozenset({"compute.read"})
CANCEL_SCOPES = frozenset({"compute.cancel"})

ACTIVE = frozenset({JobStatus.SUBMITTED, JobStatus.WAITING, JobStatus.RUNNING})
_JOB_ID = re.compile(r"^\d+\.\d+$")


class Jobs:
    """The jobs of one CE, and the sessions every operation asks it through."""

    def __init__(
        self,
        settings: HTCondorCEConfig,
        credentials: CredentialProvider,
        runner: Runner,
        workdir: Path,
    ) -> None:
        self.resource = settings.endpoint
        self.workdir = workdir
        self.host, port = split_endpoint(settings.endpoint)
        self._settings = settings
        self._provider = credentials
        self._run = runner
        self._pool = f"{self.host}:{port}"
        # The CE's schedd is named after its host unless the site says otherwise.
        self._name = settings.schedd or self.host
        self._tokens: dict[frozenset[str], BearerToken] = {}  # per scope set

    def owns(self, handle: JobHandle) -> bool:
        """Tell whether a stored handle is one of this backend's, and well-formed."""
        return (
            handle.backend == BACKEND
            and handle.resource == self.resource
            and bool(_JOB_ID.match(handle.id))
        )

    def ids(self, handles: Iterable[JobHandle]) -> list[JobID]:
        """Return the ids to ask the CE about: ours, well-formed, each once.

        Ids come from stored handles, so they are checked before they reach a
        command line: a malformed one could otherwise pass as an option.
        """
        return list(dict.fromkeys(handle.id for handle in handles if self.owns(handle)))

    async def status(self, handles: Sequence[JobHandle]) -> StatusMap:
        """Return the status of each job, in the caller's order."""
        ads = await self.ads(self.ids(handles), status.STATE_ATTRIBUTES, history=True)
        return {
            handle: status.job_status(ads[handle.id])
            if self.owns(handle) and handle.id in ads
            else JobStatus.UNKNOWN
            for handle in handles
        }

    async def ads(
        self, ids: Sequence[JobID], attributes: Sequence[str], *, history: bool
    ) -> dict[JobID, Mapping[str, Any]]:
        """Return the job ad of each job the CE knows, with ``attributes``."""
        if not ids:
            return {}
        async with self.session(READ_SCOPES) as session:
            return await session.ads(ids, attributes, history=history)

    @asynccontextmanager
    async def session(self, scopes: frozenset[str]) -> AsyncGenerator[CondorSession]:
        """Open the tools for one operation, with a token scoped to it."""
        token = await self._fresh(scopes)
        workdir = staging.ensure_workdir(self.workdir)
        async with credential_files(token, workdir) as files:
            yield CondorSession(self._run, files, self._pool, self._name)

    def forget_tokens(self) -> None:
        """Drop the cached tokens; they were never written anywhere."""
        self._tokens.clear()

    async def _fresh(self, scopes: frozenset[str]) -> BearerToken:
        requirements = credential_requirements(self._settings, scopes)
        assert requirements is not None  # an HTCondor-CE always asks for a token
        fresh = await refresh_if_stale(
            self._tokens.get(scopes), requirements, self._provider
        )
        assert isinstance(fresh, BearerToken)  # refresh_if_stale checked the kind
        self._tokens[scopes] = fresh
        return fresh


def split_endpoint(endpoint: str) -> tuple[str, int]:
    """Split ``host[:port]``; an HTCondor-CE listens on 9619 by default."""
    host, separator, port = endpoint.partition(":")
    return host, int(port) if separator else DEFAULT_PORT
