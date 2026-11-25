"""One operation's connection to the CE: its credentials, and the tools run with them.

Every tool runs in its own subprocess, configured only through its
environment. The token for an operation is written to private files that exist
only while the operation runs, so backends with different credentials never
share HTCondor configuration.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intercede._backends._htcondor import commands, status
from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce.process import Runner
from intercede._backends.monolith.htcondorce.staging import write_private
from intercede.auth.models import BearerToken
from intercede.exceptions import AuthenticationError, ResourceUnavailableError
from intercede.models import JobID

COMMAND_TIMEOUT_S = 120  # every command but output transfer, which uses the limits


@dataclass(frozen=True)
class CredentialFiles:
    """Where one operation's token is, while it runs."""

    token: Path
    token_dir: Path
    scratch: Path  # HTCondor's LOG directory (SciTokens keeps a cache there)


@asynccontextmanager
async def credential_files(
    token: BearerToken, workdir: Path
) -> AsyncGenerator[CredentialFiles, None]:
    """Write the token to private files for the duration of one operation."""
    directory = Path(tempfile.mkdtemp(prefix="cred-", dir=workdir))  # mode 0700
    try:
        token_dir = directory / "tokens.d"
        token_dir.mkdir(mode=0o700)
        secret = token.value.get_secret_value().encode()
        for path in (directory / "token", token_dir / "token"):
            write_private(path, secret)
        scratch = directory / "log"
        scratch.mkdir(mode=0o700)
        yield CredentialFiles(
            token=directory / "token",
            token_dir=token_dir,
            scratch=scratch,
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def environment(files: CredentialFiles) -> dict[str, str]:
    """Return the environment a tool runs with, for the credentials in ``files``."""
    env = dict(os.environ)
    if "CONDOR_CONFIG" not in env:
        # No config file: this environment is all the configuration there is.
        env["CONDOR_CONFIG"] = "ONLY_ENV"
    env.update(
        {
            # Never fall back to an unauthenticated connection: by default
            # HTCondor would quietly continue anonymously after a rejected token.
            "_CONDOR_SEC_CLIENT_AUTHENTICATION": "REQUIRED",
            # SCITOKENS for a CE; IDTOKENS for a schedd that issues its own
            # tokens. The same token is offered to both, in that order.
            "_CONDOR_SEC_CLIENT_AUTHENTICATION_METHODS": "SCITOKENS,IDTOKENS",
            "_CONDOR_SCITOKENS_FILE": str(files.token),
            "_CONDOR_SEC_TOKEN_DIRECTORY": str(files.token_dir),
            "_CONDOR_LOG": str(files.scratch),
            "_CONDOR_TOOL_DEBUG": "D_SECURITY",
        }
    )
    if "X509_CERT_DIR" in env:  # the CAs the CE's host certificate is checked against
        env["_CONDOR_AUTH_SSL_CLIENT_CADIR"] = env["X509_CERT_DIR"]
    return env


class CondorSession:
    """The HTCondor tools, run against one CE with one operation's credentials."""

    def __init__(
        self, runner: Runner, files: CredentialFiles, pool: str, name: str
    ) -> None:
        self.files = files
        self._run = runner
        self._pool = pool
        self._name = name
        self._remote = commands.schedd_options(pool, name)
        self._env = environment(files)

    async def run(
        self, argv: Sequence[str], timeout: float = COMMAND_TIMEOUT_S
    ) -> CommandResult:
        """Run a tool; raise when the CE rejected us or could not be reached."""
        # -debug with D_SECURITY makes the tool say why a connection failed:
        # the only way to tell a rejected token from an unreachable CE.
        result = await self._run([argv[0], "-debug", *argv[1:]], self._env, timeout)
        if result.returncode == 0:
            return result
        text = f"{result.stdout}\n{result.stderr}"
        if rejected := _AUTH_FAILURE.search(text):
            raise AuthenticationError(
                f"{self._name} rejected the credentials: {_line_of(text, rejected)}"
            )
        if _UNREACHABLE.search(text):
            raise ResourceUnavailableError(
                f"{self._pool} could not be reached: {last_line(result)}"
            )
        return result

    async def submit(self, description: Path, expected: int) -> list[JobID]:
        """Submit a description file and return the ids of the jobs it created."""
        result = await self.run(
            commands.submit(str(description), self._pool, self._name)
        )
        require_success(result, "condor_submit")
        try:
            ids = commands.parse_submit(result.stdout)
        except ValueError as exc:
            raise ResourceUnavailableError(str(exc)) from exc
        if len(ids) != expected:
            raise ResourceUnavailableError(
                f"condor_submit created {len(ids)} jobs, expected {expected}"
            )
        return ids

    async def ads(
        self, ids: Sequence[JobID], attributes: Sequence[str], *, history: bool
    ) -> dict[JobID, Mapping[str, Any]]:
        """Read job ads: from the queue, then (with ``history``) the rest from the history."""
        found: dict[JobID, Mapping[str, Any]] = {}
        for chunk in commands.chunks(ids):
            result = await self._read(commands.query(chunk, attributes, self._remote))
            found.update(result)
        if history:
            missing = [i for i in ids if i not in found]
            for chunk in commands.chunks(missing):
                result = await self._read(
                    commands.history(chunk, attributes, self._remote)
                )
                found.update(result)
        return found

    async def remove(self, ids: Sequence[JobID]) -> tuple[set[JobID], str]:
        """Remove jobs; return those removed, and why the others were not."""
        removed: set[JobID] = set()
        reason = ""
        for chunk in commands.chunks(ids):
            result = await self.run(commands.remove(chunk, self._remote))
            parsed_result = commands.parse_remove(result.stdout)
            removed.update(parsed_result)
            if result.returncode != 0:
                reason = failure(result, "condor_rm")
        return removed, reason

    async def transfer(self, job_id: JobID, timeout: float) -> CommandResult:
        """Fetch one job's spooled output into the directory it was submitted from."""
        return await self.run(commands.transfer_data([job_id], self._remote), timeout)

    async def _read(self, argv: Sequence[str]) -> dict[JobID, Mapping[str, Any]]:
        result = await self.run(argv)
        require_success(result, argv[0])
        try:
            ads = status.parse_ads(result.stdout)
        except ValueError as exc:  # also covers json.JSONDecodeError
            raise ResourceUnavailableError(
                f"{argv[0]} printed unreadable output: {exc}"
            ) from exc
        return {
            status.job_id(ad): ad for ad in ads if "ClusterId" in ad and "ProcId" in ad
        }


# -- reading what a tool said ---------------------------------------------------

# Markers in the -debug output. An authentication failure also reports that
# the collector "could not be contacted", so it is checked first.
_AUTH_FAILURE = re.compile(
    # condor_submit prints only the SECMAN line; condor_q also the AUTHENTICATE
    # summary. Not "method ... failed": that is logged even when the next
    # method then succeeds.
    r"required authentication with .* failed|AUTHENTICATE:\d+|PERMISSION DENIED|not authorized",
    re.IGNORECASE,
)
_UNREACHABLE = re.compile(
    r"Connection refused|connect errno|Failed to connect|Couldn't contact|Can't find address|"
    r"couldn't locate|Unable to locate|Name or service not known|timed out",
    re.IGNORECASE,
)
_DEBUG_LINE = re.compile(r"^\d\d/\d\d/\d\d \d\d:\d\d:\d\d ")


def _line_of(text: str, match: re.Match[str]) -> str:
    """Return the line holding ``match``, without its -debug timestamp."""
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    line = text[start : end if end != -1 else len(text)].strip()
    return _DEBUG_LINE.sub("", line)


def last_line(result: CommandResult) -> str:
    """Return the tool's own last message, without the -debug lines."""
    lines = [
        line.strip()
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
        if line.strip() and not _DEBUG_LINE.match(line)
    ]
    if lines:
        return lines[-1]
    else:
        return f"exit code {result.returncode}"


def failure(result: CommandResult, tool: str) -> str:
    """Describe why a tool failed, in one line."""
    return f"{tool} failed: {last_line(result)}"


def require_success(result: CommandResult, tool: str) -> None:
    """Raise when a tool whose failure fails the whole operation did not succeed."""
    if result.returncode != 0:
        raise ResourceUnavailableError(failure(result, tool))
