"""The HTCondor-CE backend: submit descriptions, outputs, containers and proxies."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from intercede import (
    ContainerSpec,
    CopyVars,
    FileRef,
    OutputMember,
    OutputSpec,
    ResourceUnavailableError,
    SpecificationRejectedError,
    SubmissionSpec,
)
from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede.auth import BearerToken, StaticCredentials, X509Proxy

from ._fake_condor import SPEC, TOKEN, FakeCondor, make, settings


class TestSubmit:
    async def test_a_secret_named_like_an_input_is_refused_before_any_call(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        source = tmp_path / "pilot.cfg"
        source.write_text("data")
        spec = SPEC.model_copy(
            update={"inputs": [FileRef(name="pilot.cfg", source=str(source))]}
        )
        with pytest.raises(SpecificationRejectedError, match="pilot.cfg"):
            await ce.submit(spec, [CopyVars(secrets={"pilot.cfg": b"s"})])
        assert condor.calls == []

    async def test_zero_copies_submit_nothing(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        assert (await ce.submit(SPEC, 0)).handles == {}
        assert condor.calls == []

    async def test_local_inputs_keep_their_directories(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        source = tmp_path / "anything.dat"
        source.write_text("data")
        spec = SPEC.model_copy(
            update={"inputs": [FileRef(name="sub/dir/in.txt", source=str(source))]}
        )
        await ce.submit(spec)
        staged = [
            p for p in condor.calls[0].files if p.endswith("inputs/sub/dir/in.txt")
        ]
        assert len(staged) == 1 and condor.calls[0].files[staged[0]][1] == b"data"

    async def test_secrets_are_private_and_gone_afterwards(
        self, ce: HTCondorCEBackend, condor: FakeCondor, workdir: Path
    ):
        copies = [
            CopyVars(secrets={"pilot.secret": b"s0"}),
            CopyVars(secrets={"pilot.secret": b"s1"}),
        ]
        await ce.submit(SPEC, copies)
        secrets = {
            path: entry
            for path, entry in condor.calls[0].files.items()
            if path.endswith("pilot.secret")
        }
        assert sorted(entry for entry in secrets.values()) == [
            (0o600, b"s0"),
            (0o600, b"s1"),
        ]
        assert not [p for p in workdir.rglob("*") if p.is_file()]

    async def test_secret_never_in_the_description(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        await ce.submit(SPEC, [CopyVars(secrets={"pilot.secret": b"TOPSECRET"})])
        assert "TOPSECRET" not in condor.descriptions[0]

    async def test_different_variable_names_get_separate_clusters(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        # HTCondor merges each process's environment onto the previous one, so
        # a variable of one copy would leak into the next in the same cluster.
        copies = [
            CopyVars(environment={"STAMP": "a"}),
            CopyVars(environment={"STAMP": "b", "EXTRA": "x"}),
            CopyVars(environment={"STAMP": "c"}),
        ]
        sub = await ce.submit(SPEC, copies)
        assert condor.tools().count("condor_submit") == 2
        clusters = {i: h.id.split(".")[0] for i, h in sub.handles.items()}
        assert clusters[0] == clusters[2] != clusters[1]
        assert "EXTRA" not in condor.descriptions[0]

    async def test_a_later_cluster_failing_keeps_the_submitted_copies(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        copies = [CopyVars(environment={"A": "1"}), CopyVars(environment={"B": "2"})]
        submitted = 0
        real = condor.__call__

        async def second_submit_fails(
            argv: Sequence[str], env: Mapping[str, str], timeout: float
        ) -> CommandResult:
            nonlocal submitted
            if argv[0] == "condor_submit":
                submitted += 1
                if submitted == 2:
                    return CommandResult(
                        returncode=1, stdout="", stderr="ERROR: schedd full"
                    )
            return await real(argv, env, timeout)

        backend = HTCondorCEBackend(
            settings(),
            StaticCredentials([BearerToken(value=TOKEN)]),
            runner=second_submit_fails,
            workdir=ce._workdir,
        )
        async with backend:
            sub = await backend.submit(SPEC, copies)
        assert sorted(sub.handles) == [0]
        assert sub.failures == {1: "condor_submit failed: ERROR: schedd full"}

    async def test_nothing_submitted_raises(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        condor.fail["condor_submit"] = CommandResult(
            returncode=1, stdout="", stderr="ERROR: schedd full"
        )
        with pytest.raises(ResourceUnavailableError, match="schedd full"):
            await ce.submit(SPEC, 2)

    async def test_url_inputs_are_left_to_the_resource(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        spec = SPEC.model_copy(
            update={
                "inputs": [
                    FileRef(name="in.dat", source="https://example.org/data/in.dat?x=1")
                ]
            }
        )
        await ce.submit(spec)
        assert "https://example.org/data/in.dat?x=1" in condor.descriptions[0]

    async def test_url_input_under_another_name_is_refused(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        spec = SPEC.model_copy(
            update={
                "inputs": [
                    FileRef(name="renamed.dat", source="https://example.org/in.dat")
                ]
            }
        )
        with pytest.raises(SpecificationRejectedError, match="another name"):
            await ce.submit(spec)
        assert "condor_submit" not in condor.tools()

    async def test_missing_local_input_is_refused(
        self, ce: HTCondorCEBackend, condor: FakeCondor, tmp_path: Path
    ):
        spec = SPEC.model_copy(
            update={"inputs": [FileRef(name="in", source=str(tmp_path / "missing"))]}
        )
        with pytest.raises(SpecificationRejectedError, match="cannot read"):
            await ce.submit(spec)
        assert "condor_submit" not in condor.tools()

    async def test_command_on_the_worker_is_not_shipped(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        # Not a file on this host, so it is a command on the worker node.
        await ce.submit(
            SPEC.model_copy(update={"executable": "/opt/site/bin/run-payload"})
        )
        assert "transfer_executable = false" in condor.descriptions[0]

    async def test_resources_and_queue(self, condor: FakeCondor, workdir: Path):
        spec = SPEC.model_copy(
            update={
                "resources": SPEC.resources.model_copy(
                    update={"cpus": 4, "memory_mb": 8000, "wall_time_s": 3600}
                )
            }
        )
        async with make(condor, workdir, queue="default") as backend:
            await backend.submit(spec)
        text = condor.descriptions[0]
        assert all(
            line in text
            for line in [
                "+xcount = 4",
                "+maxMemory = 8000",
                "+BatchRuntime = 3600",
                '+queue = "default"',
            ]
        )

    async def test_spec_queue_wins_over_configured_queue(
        self, condor: FakeCondor, workdir: Path
    ):
        spec = SPEC.model_copy(
            update={"resources": SPEC.resources.model_copy(update={"queue": "short"})}
        )
        async with make(condor, workdir, queue="default") as backend:
            await backend.submit(spec)
        assert '+queue = "short"' in condor.descriptions[0]


class TestSubmitOutputs:
    async def test_kept_and_uploaded_members(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        outputs = OutputSpec(
            members=[
                OutputMember(pattern="kept.txt"),
                OutputMember(pattern="sent.root", destination="https://se/sent.root"),
            ]
        )
        await ce.submit(SPEC.model_copy(update={"outputs": outputs}))
        text = condor.descriptions[0]
        assert "transfer_output_files = kept.txt, sent.root" in text
        assert 'transfer_output_remaps = "sent.root = https://se/sent.root"' in text

    async def test_include_unlisted_brings_back_everything(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        outputs = OutputSpec(
            members=[OutputMember(pattern="*.root")], include_unlisted=True
        )
        await ce.submit(SPEC.model_copy(update={"outputs": outputs}))
        assert "transfer_output_files" not in condor.descriptions[0]

    @pytest.mark.parametrize(
        "member",
        [
            OutputMember(pattern="*.root"),
            OutputMember(pattern="out?.txt"),
            OutputMember(pattern="[ab].txt"),
        ],
    )
    async def test_patterns_are_refused_without_include_unlisted(
        self, ce: HTCondorCEBackend, member: OutputMember
    ):
        with pytest.raises(SpecificationRejectedError, match="by name, not by pattern"):
            await ce.submit(
                SPEC.model_copy(update={"outputs": OutputSpec(members=[member])})
            )

    async def test_a_pattern_cannot_be_uploaded(self, ce: HTCondorCEBackend):
        member = OutputMember(pattern="*.root", destination="https://se/")
        with pytest.raises(SpecificationRejectedError, match="by name"):
            await ce.submit(
                SPEC.model_copy(
                    update={
                        "outputs": OutputSpec(members=[member], include_unlisted=True)
                    }
                )
            )


class TestContainers:
    async def test_refused_without_a_launcher(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        with pytest.raises(SpecificationRejectedError, match="no container_launcher"):
            await ce.submit(
                SPEC.model_copy(
                    update={"container": ContainerSpec(image="docker://alpine")}
                )
            )
        assert condor.calls == []

    async def test_wrapped_by_the_configured_launcher(
        self, condor: FakeCondor, workdir: Path
    ):
        spec = SubmissionSpec(
            executable="/usr/bin/run",
            arguments=["x"],
            container=ContainerSpec(image="/cvmfs/img.sif"),
        )
        async with make(
            condor, workdir, container_launcher=["apptainer", "exec", "{image}"]
        ) as backend:
            await backend.submit(spec)
        text = condor.descriptions[0]
        assert "executable = apptainer" in text
        assert "transfer_executable = false" in text
        assert "arguments = \"'exec' '/cvmfs/img.sif' '/usr/bin/run' 'x'\"" in text

    async def test_a_local_file_cannot_run_inside_an_image(
        self, condor: FakeCondor, workdir: Path, tmp_path: Path
    ):
        script = tmp_path / "s.sh"
        script.write_text("")
        spec = SubmissionSpec(
            executable=str(script), container=ContainerSpec(image="docker://a")
        )
        async with make(
            condor, workdir, container_launcher=["apptainer", "exec", "{image}"]
        ) as backend:
            with pytest.raises(SpecificationRejectedError, match="inside the image"):
                await backend.submit(spec)


class TestProxy:
    async def test_proxy_rides_in_the_job_without_delegation(
        self, condor: FakeCondor, workdir: Path
    ):
        credentials = [BearerToken(value=TOKEN), X509Proxy(pem=b"PROXY-PEM")]
        async with make(
            condor, workdir, credentials=credentials, auth=["token", "proxy"]
        ) as backend:
            await backend.submit(SPEC)
        call = condor.calls[0]
        proxy = re.search(r"^x509userproxy = (.*)$", condor.descriptions[0], re.M)
        assert proxy and call.files[proxy.group(1)] == (0o600, b"PROXY-PEM")
        assert not Path(proxy.group(1)).exists()
        assert call.env["_CONDOR_DELEGATE_JOB_GSI_CREDENTIALS"] == "false"

    async def test_token_only_has_no_proxy(
        self, ce: HTCondorCEBackend, condor: FakeCondor
    ):
        await ce.submit(SPEC)
        assert "x509userproxy" not in condor.descriptions[0]
        assert "_CONDOR_DELEGATE_JOB_GSI_CREDENTIALS" not in condor.calls[0].env
