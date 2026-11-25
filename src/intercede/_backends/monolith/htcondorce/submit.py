"""Submitting to an HTCondor-CE: one submit description per group of copies.

Copies that share an environment share a description, and its copies are one
cluster. Inputs and secrets are written to a private directory and spooled to
the CE by ``condor_submit``; the directory is removed before ``submit`` returns.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from intercede._backends._htcondor.submit import SubmitPlan, render
from intercede._backends.monolith.htcondorce import staging
from intercede._backends.monolith.htcondorce.jobs import BACKEND, SUBMIT_SCOPES, Jobs
from intercede.config import HTCondorCEConfig
from intercede.exceptions import InterCEdeError, SpecificationRejectedError
from intercede.models import CopyVars, JobHandle, Submission, SubmissionSpec


class JobSubmitter:
    """Submits a specification's copies to the CE."""

    def __init__(self, settings: HTCondorCEConfig, jobs: Jobs) -> None:
        self._settings = settings
        self._jobs = jobs

    async def submit(
        self, spec: SubmissionSpec, copies: int | Sequence[CopyVars] = 1
    ) -> Submission:
        """Submit the copies; see ``Submitter.submit``."""
        copy_vars = [CopyVars()] * copies if isinstance(copies, int) else list(copies)
        if not copy_vars:
            return Submission(handles={})
        for index, copy in enumerate(copy_vars):
            # A secret and an input would be staged to the same file.
            if clashes := spec.clashes_with(copy):
                raise SpecificationRejectedError(
                    f"copy {index}: secrets {sorted(clashes)} are named like inputs"
                )
        resource = self._jobs.resource
        outputs, remaps = staging.outputs(spec)
        executable, ship, arguments = staging.command(
            spec, self._settings.container_launcher, resource
        )

        handles: dict[int, JobHandle] = {}
        failures: dict[int, str] = {}
        async with self._jobs.session(SUBMIT_SCOPES) as session:
            submission = self._jobs.workdir / uuid.uuid4().hex
            submission.mkdir(mode=0o700)
            try:
                inputs = staging.stage_inputs(spec, submission)
                # Every description is written before the first is submitted:
                # a value one cannot hold refuses the whole submission.
                descriptions: list[tuple[list[int], Path]] = []
                for group in staging.group_by_environment(spec, copy_vars):
                    plan = SubmitPlan(
                        executable=executable,
                        transfer_executable=ship,
                        arguments=arguments,
                        copies=[
                            staging.stage_copy(spec, copy_vars[i], submission / str(i))
                            for i in group
                        ],
                        inputs=inputs,
                        stdout=spec.stdout or SubmitPlan.stdout,
                        stderr=spec.stderr or SubmitPlan.stderr,
                        outputs=outputs,
                        output_remaps=remaps,
                        cpus=spec.resources.cpus,
                        memory_mb=spec.resources.memory_mb,
                        wall_time_s=spec.resources.wall_time_s,
                        queue=spec.resources.queue or self._settings.queue,
                    )
                    description = submission / f"group-{group[0]}.sub"
                    try:
                        description.write_text(render(plan))
                    except ValueError as exc:
                        raise SpecificationRejectedError(str(exc)) from exc
                    descriptions.append((group, description))
                for group, description in descriptions:
                    try:
                        ids = await session.submit(description, len(group))
                    except InterCEdeError as exc:
                        if not handles:
                            raise  # nothing submitted: the whole operation failed
                        failures.update({i: str(exc) for i in group})
                        continue
                    for index, job_id in zip(group, ids):
                        handles[index] = JobHandle(
                            backend=BACKEND, resource=resource, id=job_id
                        )
            finally:
                # Inputs are spooled to the CE by now; secrets must not linger here.
                shutil.rmtree(submission, ignore_errors=True)
        return Submission(handles=handles, failures=failures)
