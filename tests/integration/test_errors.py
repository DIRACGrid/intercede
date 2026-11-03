"""Shared suite: what a consumer sees when things go wrong, on every stack.

Only failures that happen in practice: a credential the resource rejects or
that the provider does not hold, a resource that is down, a specification the
backend cannot honour, and per-job failures that must be reported, not raised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from intercede import registry
from intercede.auth.providers import StaticCredentials
from intercede.exceptions import (
    AuthenticationError,
    ResourceUnavailableError,
    SpecificationRejectedError,
)
from intercede.models import (
    ContainerSpec,
    CopyVars,
    FileRef,
    JobHandle,
    JobStatus,
    OutputMember,
    OutputSpec,
    SubmissionSpec,
)
from intercede.protocols import JobBackend, Killable, OutputRetriever, Purgeable
from tests.integration.jobs import release, script, wait_for

pytestmark = pytest.mark.contract


async def submitted(
    backend: JobBackend, tmp_path: Path, body: str = "true"
) -> JobHandle:
    """Submit one job and return its handle."""
    submission = await backend.submit(SubmissionSpec(executable=script(tmp_path, body)))
    return submission.handles[0]


# --------------------------------------------------------------------------
# Whole-operation failures: raised, because no job could be dealt with
# --------------------------------------------------------------------------


async def test_a_credential_the_resource_rejects(
    backend: JobBackend,
    client_config: dict[str, Any],
    rejected_credentials: StaticCredentials,
    tmp_path: Path,
):
    handle = await submitted(backend, tmp_path)
    try:
        async with registry.get_backend(
            client_config["backend"], rejected_credentials
        ) as rejected:
            with pytest.raises(AuthenticationError):
                await rejected.submit(
                    SubmissionSpec(executable=script(tmp_path, "true"))
                )
            with pytest.raises(AuthenticationError):
                await rejected.get_status([handle])
    finally:
        await release(backend, [handle])


async def test_a_provider_without_the_credential(
    client_config: dict[str, Any], credentials: StaticCredentials, tmp_path: Path
):
    # `credentials` only makes the test skip on stacks that need none.
    async with registry.get_backend(
        client_config["backend"], StaticCredentials()
    ) as empty:
        with pytest.raises(AuthenticationError):
            await empty.submit(SubmissionSpec(executable=script(tmp_path, "true")))


async def test_a_resource_that_is_down(
    backend: JobBackend,
    unreachable_config: dict[str, Any],
    credentials: StaticCredentials,
    tmp_path: Path,
):
    handle = await submitted(backend, tmp_path)
    try:
        async with registry.get_backend(unreachable_config, credentials) as down:
            with pytest.raises(ResourceUnavailableError):
                await down.submit(SubmissionSpec(executable=script(tmp_path, "true")))
            # A stored handle of a job on that resource, while it is down.
            stored = handle.model_copy(update={"resource": down.resource})
            with pytest.raises(ResourceUnavailableError):
                await down.get_status([stored])
    finally:
        await release(backend, [handle])


# --------------------------------------------------------------------------
# Specifications refused before anything is submitted
# --------------------------------------------------------------------------


async def test_a_secret_named_like_an_input(backend: JobBackend, tmp_path: Path):
    (tmp_path / "pilot.cfg").write_text("settings")
    spec = SubmissionSpec(
        executable=script(tmp_path, "true"),
        inputs=[FileRef(name="pilot.cfg", source=str(tmp_path / "pilot.cfg"))],
    )
    with pytest.raises(SpecificationRejectedError, match="pilot.cfg"):
        await backend.submit(spec, [CopyVars(secrets={"pilot.cfg": b"secret"})])


async def test_a_local_input_that_does_not_exist(backend: JobBackend, tmp_path: Path):
    spec = SubmissionSpec(
        executable=script(tmp_path, "true"),
        inputs=[FileRef(name="in.dat", source=str(tmp_path / "missing.dat"))],
    )
    with pytest.raises(SpecificationRejectedError):
        await backend.submit(spec)


async def test_an_output_left_for_collection_on_a_backend_that_cannot_collect(
    backend: JobBackend, tmp_path: Path
):
    if isinstance(backend, OutputRetriever):
        pytest.skip("the backend collects outputs")
    spec = SubmissionSpec(
        executable=script(tmp_path, "true"),
        outputs=OutputSpec(members=[OutputMember(pattern="result.txt")]),
    )
    with pytest.raises(SpecificationRejectedError):
        await backend.submit(spec)


# A request per refusal a stack can declare in [refusals] of its client config.
REFUSED_SPECS = {
    "containers": {"container": ContainerSpec(image="docker://almalinux:9")},
    "url_inputs": {
        "inputs": [FileRef(name="data.txt", source="https://example.org/data.txt")]
    },
    "url_outputs": {
        "outputs": OutputSpec(
            members=[
                OutputMember(
                    pattern="result.txt",
                    destination="https://storage.example.org/result.txt",
                )
            ]
        )
    },
}


@pytest.mark.parametrize("refusal", sorted(REFUSED_SPECS))
async def test_what_the_stack_cannot_honour_is_refused_at_submit(
    backend: JobBackend, refusals: frozenset[str], refusal: str, tmp_path: Path
):
    """Refused at submit, not accepted and then failed at the resource."""
    if refusal not in refusals:
        pytest.skip(f"this stack's backend supports {refusal} (see [refusals])")
    spec = SubmissionSpec(executable=script(tmp_path, "true"), **REFUSED_SPECS[refusal])
    with pytest.raises(SpecificationRejectedError):
        await backend.submit(spec)


# --------------------------------------------------------------------------
# Per-job failures: reported in the result, never raised
# --------------------------------------------------------------------------


async def test_the_output_of_a_running_job(
    backend: JobBackend, output_retriever: OutputRetriever, tmp_path: Path
):
    handle = await submitted(backend, tmp_path, "sleep 600")
    try:
        await wait_for(backend, [handle], frozenset({JobStatus.RUNNING}))

        outcome = (await output_retriever.get_output([handle], tmp_path / "out"))[
            handle
        ]

        assert not outcome.ok and outcome.reason
    finally:
        await release(backend, [handle])


async def test_a_job_of_another_resource(backend: JobBackend, tmp_path: Path):
    """A handle the consumer stored for a different resource is not this backend's job."""
    handle = await submitted(backend, tmp_path)
    try:
        theirs = handle.model_copy(update={"resource": "elsewhere.example.org:9619"})
        assert (await backend.get_status([theirs]))[theirs] == JobStatus.UNKNOWN
        outcomes = []
        if isinstance(backend, OutputRetriever):
            outcomes.append(await backend.get_output([theirs], tmp_path / "out"))
        if isinstance(backend, Killable):
            outcomes.append(await backend.kill([theirs]))
        if isinstance(backend, Purgeable):
            outcomes.append(await backend.purge([theirs]))
        for outcome in outcomes:
            assert not outcome[theirs].ok and outcome[theirs].reason
    finally:
        await release(backend, [handle])
