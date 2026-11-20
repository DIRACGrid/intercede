"""Running one HTCondor tool as a subprocess."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

from intercede._backends.batch.protocols import CommandResult
from intercede.exceptions import ConfigurationError, ResourceUnavailableError

# How the backend runs a tool: argv, environment, timeout in seconds.
# Tests pass their own, to play the tools without HTCondor installed.
Runner = Callable[[Sequence[str], Mapping[str, str], float], Awaitable[CommandResult]]


async def run_process(
    argv: Sequence[str], env: Mapping[str, str], timeout: float
) -> CommandResult:
    """Run one HTCondor tool and wait for it, killing it after ``timeout`` seconds."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"{argv[0]} was not found: the HTCondor client tools must be installed"
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ResourceUnavailableError(
            f"{argv[0]} did not finish within {timeout:.0f} s"
        ) from exc
    return CommandResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )
