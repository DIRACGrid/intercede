"""Submit descriptions: a ``SubmissionSpec`` written in HTCondor's submit language.

One description submits every copy of a spec as one cluster, one process per
copy. What differs between copies (environment, secrets, working directory) is
expanded per process through macros indexed by ``$(ProcId)``, so the copies
stay one bulk submission.

A description only says what to run and where its files go. It sets no
policy on the job's lifetime: no holds, no automatic removal, no retries.
Those are the consumer's decisions. The one exception is that a finished job
stays in the queue until it is purged, so its output can be collected again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


def quote_arguments(arguments: Sequence[str]) -> str:
    """Quote command arguments using HTCondor's new syntax."""
    quoted_arguments = [_quote_item(argument) for argument in arguments]

    joined_arguments = " ".join(quoted_arguments)
    return f'"{joined_arguments}"'


def quote_environment(environment: Mapping[str, str]) -> str:
    """Quote environment variables using HTCondor's new syntax."""
    for name in environment:
        _validate_environment_name(name)

    quoted_variables = [
        f"{name}={_quote_item(value)}" for name, value in sorted(environment.items())
    ]

    joined_variables = " ".join(quoted_variables)
    return f'"{joined_variables}"'


def _validate_environment_name(name: str) -> None:
    """Raise ValueError if the environment variable name is invalid."""
    is_valid = bool(name) and "=" not in name and name.replace("_", "").isalnum()

    if not is_valid:
        raise ValueError(f"{name!r} is not a valid environment variable name")


def _quote_item(value: str) -> str:
    """Escape and quote a single argument or environment variable value."""
    _refuse_newlines(value)

    escaped_value = value.replace('"', '""')
    escaped_value = escaped_value.replace("'", "''")
    escaped_value = _escape_macros(escaped_value)

    return f"'{escaped_value}'"


def _escape_macros(value: str) -> str:
    # "$(...)" is macro syntax in a submit description; $(DOLLAR) is a literal "$".
    return value.replace("$", "$(DOLLAR)")


def _refuse_newlines(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("a submit description value cannot contain a line break")


@dataclass(frozen=True)
class CopyPlan:
    """What one process of the cluster gets on its own."""

    initialdir: str  # absolute; outputs come back here (SUBMIT_Iwd)
    environment: Mapping[str, str] = field(default_factory=dict)
    # Inputs of this copy only, relative to ``initialdir`` (e.g. "secrets/").
    inputs: Sequence[str] = ()


@dataclass(frozen=True)
class SubmitPlan:
    """Everything a submit description says, resolved to HTCondor's terms."""

    executable: str
    transfer_executable: bool
    arguments: Sequence[str]
    copies: Sequence[CopyPlan]
    inputs: Sequence[str] = ()  # shared by every copy: absolute paths or URLs
    stdout: str = "$(ClusterId).$(ProcId).out"
    stderr: str = "$(ClusterId).$(ProcId).err"
    log: str = "$(ClusterId).$(ProcId).log"
    # None: every file the payload creates comes back; empty: none at all.
    outputs: Sequence[str] | None = ()
    output_remaps: Mapping[str, str] = field(default_factory=dict)  # name -> URL
    cpus: int = 1
    memory_mb: int | None = None
    wall_time_s: int | None = None
    queue: str | None = None
    proxy_file: str | None = None


def render(plan: SubmitPlan) -> str:
    """Write ``plan`` as a submit description that queues one process per copy."""
    _validate_plan(plan)

    lines = _render_basics(plan)
    lines.extend(_render_copies(plan))
    lines.extend(_render_file_transfer(plan))
    lines.extend(_render_resources(plan))
    lines.extend(_render_proxy(plan))
    lines.extend(_render_queue(plan))

    return "\n".join(lines) + "\n"


def _join(items: Sequence[str]) -> str:
    for item in items:
        _refuse_newlines(item)
        if "," in item:
            raise ValueError(f"{item!r}: HTCondor file lists cannot hold a comma")
    return ", ".join(_escape_macros(item) for item in items)


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _classad_string(value: str) -> str:
    _refuse_newlines(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + _escape_macros(escaped) + '"'


def _validate_plan(plan: SubmitPlan) -> None:
    if not plan.copies:
        raise ValueError("A submit description needs at least one copy")


def _render_basics(plan: SubmitPlan) -> list[str]:
    lines = [
        "universe = vanilla",
        f"executable = {_escape_macros(plan.executable)}",
        f"transfer_executable = {_bool(plan.transfer_executable)}",
    ]

    if plan.arguments:
        lines.append(f"arguments = {quote_arguments(plan.arguments)}")

    return lines


def _render_copies(plan: SubmitPlan) -> list[str]:
    lines = []

    for index, copy in enumerate(plan.copies):
        _refuse_newlines(copy.initialdir)
        lines.extend(
            [
                f"ic_dir_{index} = {_escape_macros(copy.initialdir)}",
                f"ic_env_{index} = {quote_environment(copy.environment)}",
                f"ic_in_{index} = {_join(copy.inputs)}",
            ]
        )

    lines.extend(
        ["initialdir = $(ic_dir_$(ProcId))", "environment = $(ic_env_$(ProcId))"]
    )

    return lines


def _render_file_transfer(plan: SubmitPlan) -> list[str]:
    shared_inputs = _join(plan.inputs)
    copy_inputs = "$(ic_in_$(ProcId))"
    inputs = f"{shared_inputs}, {copy_inputs}" if shared_inputs else copy_inputs

    lines = [
        "should_transfer_files = YES",
        "when_to_transfer_output = ON_EXIT_OR_EVICT",
        f"transfer_input_files = {inputs}",
        f"output = {plan.stdout}",
        f"error = {plan.stderr}",
        f"log = {plan.log}",
    ]

    if plan.outputs is not None:
        output_files = _join(plan.outputs) or '""'
        lines.append(f"transfer_output_files = {output_files}")

    if plan.output_remaps:
        remaps = "; ".join(
            f"{name} = {url}" for name, url in sorted(plan.output_remaps.items())
        )
        lines.append(f'transfer_output_remaps = "{_escape_macros(remaps)}"')

    return lines


def _render_resources(plan: SubmitPlan) -> list[str]:
    lines = [f"request_cpus = {plan.cpus}", f"+xcount = {plan.cpus}"]

    if plan.memory_mb is not None:
        lines.extend(
            [f"request_memory = {plan.memory_mb}", f"+maxMemory = {plan.memory_mb}"]
        )

    if plan.wall_time_s is not None:
        lines.append(f"+BatchRuntime = {plan.wall_time_s}")

    if plan.queue is not None:
        lines.append(f"+queue = {_classad_string(plan.queue)}")

    return lines


def _render_proxy(plan: SubmitPlan) -> list[str]:
    if plan.proxy_file is None:
        return []

    return [f"x509userproxy = {_escape_macros(plan.proxy_file)}"]


def _render_queue(plan: SubmitPlan) -> list[str]:
    return [
        # Keep completed jobs until purge() releases them
        "leave_in_queue = (JobStatus == 4)",
        f"queue {len(plan.copies)}",
    ]
