"""A simulated set of HTCondor tools, and helpers for driving the backend with it.

``FakeCondor`` answers the way HTCondor 25 does (checked against a live schedd):
``condor_q`` prints nothing for unknown jobs, ``condor_history`` an empty list,
``condor_rm`` exits 1 for jobs it cannot find, and ``condor_transfer_data``
writes into each job's ``SUBMIT_Iwd``.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intercede import HTCondorCEConfig, JobHandle, JobID, SubmissionSpec
from intercede._backends.batch.protocols import CommandResult
from intercede._backends.monolith.htcondorce import HTCondorCEBackend
from intercede.auth import BearerToken, StaticCredentials

ENDPOINT = "ce.example.org:9619"
SPEC = SubmissionSpec(executable="/bin/true")
TOKEN = "TOKEN-VALUE"  # noqa: S105  (a fake token the tests look for)


@dataclass
class Call:
    argv: list[str]
    env: dict[str, str]
    files: dict[str, tuple[int, bytes]] = field(
        default_factory=dict
    )  # path -> (mode, content)


class FakeCondor:
    """Plays condor_submit, condor_q, condor_history, condor_rm and condor_transfer_data."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.queue: dict[str, dict[str, Any]] = {}
        self.history: dict[str, dict[str, Any]] = {}
        self.outputs: dict[str, dict[str, bytes]] = {}  # files a job "created"
        self.descriptions: list[str] = []
        self.next_cluster = 1
        self.fail: dict[str, CommandResult] = {}  # tool -> forced result

    async def __call__(
        self, argv: Sequence[str], env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        call = Call(list(argv), dict(env))
        self.calls.append(call)
        self._snapshot(call)
        tool, args = argv[0], [a for a in argv[1:] if a != "-debug"]
        if tool in self.fail:
            return self.fail[tool]
        ids = [a for a in args if re.fullmatch(r"\d+\.\d+", a)]
        if tool == "condor_submit":
            return self._submit(Path(args[-1]))
        if tool == "condor_q":
            return self._ads(self.queue, ids, args, empty="")
        if tool == "condor_history":
            return self._ads(self.history, ids, args, empty="[\n]\n")
        if tool == "condor_rm":
            return self._remove(ids)
        if tool == "condor_transfer_data":
            return self._transfer(ids)
        raise AssertionError(f"unexpected tool {tool}")

    # -- the tools -----------------------------------------------------------

    def _submit(self, description: Path) -> CommandResult:
        text = description.read_text()
        self.descriptions.append(text)
        count = int(re.search(r"^queue (\d+)$", text, re.M).group(1))  # type: ignore[union-attr]
        cluster = self.next_cluster
        self.next_cluster += 1
        for proc in range(count):
            iwd = re.search(rf"^ic_dir_{proc} = (.*)$", text, re.M).group(1)  # type: ignore[union-attr]
            out = re.search(r"^output = (.*)$", text, re.M).group(1)  # type: ignore[union-attr]
            err = re.search(r"^error = (.*)$", text, re.M).group(1)  # type: ignore[union-attr]
            self.queue[f"{cluster}.{proc}"] = {
                "ClusterId": cluster,
                "ProcId": proc,
                "JobStatus": 1,
                "SUBMIT_Iwd": iwd,
                "Out": _expand(out, cluster, proc),
                "Err": _expand(err, cluster, proc),
                "UserLog": f"{iwd}/{cluster}.{proc}.log",
            }
        return ok(f"{cluster}.0 - {cluster}.{count - 1}\n")

    @staticmethod
    def _ads(
        store: Mapping[str, dict[str, Any]], ids: list[str], args: list[str], empty: str
    ) -> CommandResult:
        wanted = args[args.index("-attributes") + 1].split(",")
        found = [
            {k: v for k, v in store[i].items() if k in wanted}
            for i in ids
            if i in store
        ]
        return ok(json.dumps(found) if found else empty)

    def _remove(self, ids: list[str]) -> CommandResult:
        lines, missing = [], False
        for i in ids:
            if i in self.queue:
                ad = self.queue.pop(i)
                self.history[i] = {
                    **ad,
                    "LastJobStatus": ad["JobStatus"],
                    "JobStatus": 3,
                }
                lines.append(f"Job {i} marked for removal")
            else:
                missing = True
        if missing:
            return CommandResult(
                returncode=1,
                stdout="\n".join(lines),
                stderr="Couldn't find/remove all jobs\n",
            )
        return ok("\n".join(lines))

    def _transfer(self, ids: list[str]) -> CommandResult:
        for i in ids:
            ad = self.queue[i]
            iwd = Path(ad["SUBMIT_Iwd"])
            iwd.mkdir(parents=True, exist_ok=True)
            files = {
                name: content
                for name, content in [
                    (ad.get("Out"), b"stdout of " + i.encode()),
                    (ad.get("Err"), b""),
                    (Path(ad["UserLog"]).name if "UserLog" in ad else None, b"log"),
                ]
                if name
            }
            for name, content in {**files, **self.outputs.get(i, {})}.items():
                (iwd / name).parent.mkdir(parents=True, exist_ok=True)
                (iwd / name).write_bytes(content)
        return ok("Fetching data files...\n")

    # -- helpers for the tests --------------------------------------------------

    def _snapshot(self, call: Call) -> None:
        """Record staged and credential files as they exist during the call."""
        for key in ("_CONDOR_SCITOKENS_FILE",):
            if key in call.env:
                self._record(call, Path(call.env[key]))
        if call.argv[0] == "condor_submit":
            submission = Path(call.argv[-1]).parent
            for path in submission.rglob("*"):
                if path.is_file():
                    self._record(call, path)
            proxy = re.search(
                r"^x509userproxy = (.*)$", Path(call.argv[-1]).read_text(), re.M
            )
            if proxy:
                self._record(call, Path(proxy.group(1)))

    @staticmethod
    def _record(call: Call, path: Path) -> None:
        if path.is_file():
            call.files[str(path)] = (
                stat.S_IMODE(path.stat().st_mode),
                path.read_bytes(),
            )

    def tools(self) -> list[str]:
        return [call.argv[0] for call in self.calls]

    def finish(self, job: JobID | str, code: int = 4, **fields: Any) -> None:
        self.queue[job] = {**self.queue[job], "JobStatus": code, **fields}


def _expand(name: str, cluster: int, proc: int) -> str:
    return name.replace("$(ClusterId)", str(cluster)).replace("$(ProcId)", str(proc))


def ok(stdout: str) -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


def settings(**fields: Any) -> HTCondorCEConfig:
    return HTCondorCEConfig(**{"kind": "htcondor-ce", "endpoint": ENDPOINT, **fields})


def make(
    condor: FakeCondor,
    workdir: Path,
    credentials: list[Any] | None = None,
    **fields: Any,
) -> HTCondorCEBackend:
    provider = StaticCredentials(
        credentials if credentials is not None else [BearerToken(value=TOKEN)]
    )
    return HTCondorCEBackend(
        settings(**fields), provider, runner=condor, workdir=workdir
    )


def handle(job: str, **changes: str) -> JobHandle:
    return JobHandle(
        backend="htcondor-ce", resource=ENDPOINT, id=JobID(job)
    ).model_copy(update=changes)
