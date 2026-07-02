<p align="center">
  <img src="./docs/assets/intercede-logo.svg" alt="interCEde" width="380">
</p>

<p align="center">
  <em>Unified interfaces to Computing Elements and batch systems for DIRAC / DiracX and beyond —<br>
  submit, monitor, retrieve — validated against containerized backends.</em>
</p>

<p align="center">
  <a href="https://github.com/DIRACGrid/intercede/actions"><img src="https://img.shields.io/github/actions/workflow/status/DIRACGrid/intercede/ci.yml?branch=main" alt="CI"></a>
  <a href="https://pypi.org/project/intercede/"><img src="https://img.shields.io/pypi/v/intercede" alt="PyPI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/pypi/pyversions/intercede" alt="Python versions"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/DIRACGrid/intercede" alt="License"></a>
</p>

---

## What is interCEde?

**interCEde** sits between Workload Management Systems, such as [DiracX](https://github.com/DIRACGrid/diracx) and the
many resources where jobs actually run. It provides a single, consistent interface for
talking to **Computing Elements (CEs)** and **batch systems** — submitting jobs, querying
their status, and retrieving their outputs — regardless of which backend is on the other
end.

The name is the job description: the library *intercedes* on WMS' behalf, acting between
two parties so the rest of the stack never has to know whether it is talking to ARC,
HTCondor, Slurm over SSH, or a process on the local machine.

Every interface ships with **integration tests that run against containerized instances**
of the real backends, so a given CE type is verified against multiple versions and
configurations rather than against a mock that drifts from reality.

## Why it exists

- **One contract, many backends.** Calling code submits a job the same way everywhere; the
  backend-specific quirks live behind the interface.
- **Composable resources.** Backends combine — `SSH + Slurm`, `SSH + HTCondor`,
  `ARC + HTCondor`, or a plain `local` runner — and each combination is just another
  implementation of the same interface.
- **Tested against the real thing.** Containerized Slurm, HTCondor, and ARC instances are
  spun up in CI so behavior is checked against actual schedulers, not stubs.
- **Version coverage.** The same test suite runs across a matrix of backend versions to
  catch incompatibilities before they reach production.

## Relationship to DIRAC / DiracX

interCEde is part of the [DIRACGrid](https://github.com/DIRACGrid) ecosystem and is designed
to back the Computing Element layer used by [DiracX](https://github.com/DIRACGrid/diracx).
It can also be used standalone wherever a uniform interface to heterogeneous CEs and batch
systems is useful.
