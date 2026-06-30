# IC-ADR-002: Integration testing against containerized backends

## Metadata

- **Created By:** Alexandre Boyer
- **Date:** 2026-07-02
- **Status:** Draft
- **Decision Maker(s):** Federico Stagni, Christophe Haen, Chris Burr
- **Stakeholders:** interCEde contributors and maintainers; DIRACGrid CI maintainers
- **Depends on:** IC-ADR-001 (core architecture: Protocols, Transport × Scheduler composition, capability segmentation)

> **Scope and altitude.** Direction-setting for the *test architecture*: what runs (real backend
> daemons in per-stack compose environments), where (GitHub-hosted CI, on every PR), and how
> versions are managed (pins + Renovate + support windows). The contents of individual tests, the
> exact Dockerfiles, and the CI YAML are implementation, governed by the architecture decided
> here.

## Abstract

interCEde's contract is expressed as structural `Protocol`s exercised against fakes, which structurally cannot catch the bugs interCEde exists to absorb: version-specific daemon behaviour, destructive output retrieval, and status-mapping quirks. This ADR decides to add integration tests that run **real backend daemons** — in the combinations interCEde supports — in GitHub-hosted CI on every pull request, within a standard runner. The unit of everything is a **stack**: a named, self-contained backend environment defined by one `docker compose` file plus versioned configuration, declared in a single `stacks.yml` manifest from which the CI matrix is generated. A single, backend-agnostic contract suite is dispatched across stacks by capability (protocol narrowing), mirroring how the library itself handles capability variance. Backend images build from pinned RPMs, are prebuilt and pushed to GHCR, and every version — image tags, Dockerfile `ARG`s, Python dependencies — is pinned and tracked by Renovate, so an upstream release that breaks a contract surfaces as a red, bisectable Renovate PR rather than tribal knowledge. Credentials are generated ephemerally per run and never committed. A non-blocking weekly canary runs the same matrix against unpinned tags for early warning. Adding a backend or configuration is a manifest entry plus files, with no workflow edits.

## Motivation

interCEde's core architecture is deliberately mock-friendly: everything is a `Protocol`, backends
are composed, and the unit test suite exercises contracts against fakes. That is also its greatest
testing risk. A structural protocol plus a fake will happily agree with each other forever while
the real ARC REST endpoint, the real `condor_submit -spool` handshake, or the real
`sbatch`-over-SSH quoting rules drift away underneath. The bugs interCEde exists to absorb —
version-specific daemon behaviour, destructive output retrieval, status-mapping quirks — are
precisely the bugs that unit tests structurally cannot see.

DIRAC's history makes the point concretely: HTCondor ≥ 25.8 silently changed output retrieval for
spooled jobs into a one-shot operation. No unit test anywhere would have caught that; only a test
that submits a real job to a real schedd and fetches its output twice would go red.

We therefore need integration tests that:

1. run real backend daemons, in the combinations interCEde actually supports;
2. run in GitHub-hosted CI, on every pull request, within the resources of a standard runner
   (4 vCPU / 16 GB on public repositories);
3. surface **new upstream versions** (ARC, HTCondor, Slurm, OpenSSH, and our own Python
   dependencies) as reviewable Renovate pull requests whose CI result *is* the compatibility
   verdict;
4. support **multiple configurations per backend combination** eventually (token vs. proxy auth,
   shared vs. non-shared filesystem, alternative queue setups) while starting with exactly one
   basic configuration each.

### Initial target combinations

| Stack ID | Backend under test | interCEde components exercised |
|---|---|---|
| `arc-slurm` | ARC 7 CE (REST) → Slurm LRMS | `ARCBackend` (AREX REST), end-to-end through a real LRMS |
| `arc-condor` | ARC 7 CE (REST) → HTCondor LRMS | `ARCBackend` against the other major LRMS |
| `htcondor` | HTCondor-CE → HTCondor pool | `HTCondorCEBackend`; plain-schedd Condor scheduler tests reuse the same pool |
| `ssh-slurm` | sshd → Slurm | SSH transport × Slurm scheduler |
| `ssh-condor` | sshd → HTCondor (schedd) | SSH transport × Condor scheduler |
| `local-slurm` | Slurm, tests run *inside* the container | Local transport × Slurm scheduler |

`ssh-*` and `local-*` share the same images — the local variant simply executes pytest inside the
batch container instead of connecting over port 22. Which of `ssh-slurm` / `local-slurm` lands
first is an implementation detail; the architecture treats them identically. Local × HTCondor
(first-class in IC-ADR-001's driver list) is deliberately not an initial stack: the Local
transport is exercised by `local-slurm` and the Condor scheduler by `ssh-condor`, so the
combination adds no new axis; it can be added later as a manifest entry reusing the `ssh-condor`
stack (`exec_in_container: true`).

### Facts constraining the design

- **HTCondor** publishes official role images on Docker Hub (`htcondor/mini`, `htcondor/cm`,
  `htcondor/execute`, `htcondor/submit`), version-tagged (`<version>-el9`, `lts-el9`). These are
  Renovate-trackable out of the box.
- **HTCondor-CE** has no first-party image with clean semantic tags (the OSG images use date tags
  and OSG-series semantics); an in-repo Dockerfile installing a pinned `htcondor-ce` RPM is more
  Renovate-friendly than chasing OSG tags.
- **ARC** has no maintained official Docker image. The supported path is installing pinned
  `nordugrid-arc-*` RPMs on EL9 in our own Dockerfile. interCEde targets **ARC 7 and later
  only** — ARC 6 is out of scope (see §8). Crucially, ARC ships *zero configuration*:
  a minimal working CE with a **Test-CA and host certificate generated at install time**
  (`arcctl test-ca`), which removes the entire grid-PKI problem from CI.
- **Slurm** has no official image; community images are unmaintained to varying degrees. An
  in-repo Dockerfile with a pinned Slurm package version (single node running `slurmctld` +
  `slurmd` + `munged`) is small and fully under our control.
- **Renovate** is already configured in this repository (pip/pep621/github-actions managers). It
  additionally supports `dockerfile` and `docker-compose` managers (image tags), and
  `customManagers` (regex) for versions embedded in Dockerfiles as `ARG`s — the standard
  `# renovate: datasource=... depName=...` comment convention.
- GitHub Actions **service containers** cannot express what we need (no compose, no build step,
  no ordered startup, poor log access). Docker and `docker compose` are preinstalled on
  `ubuntu-latest`, and compose v2 supports `up --wait` gating on healthchecks.

## Specification

### 1. A *stack* is the unit of everything

A **stack** is a named, self-contained backend environment:

```
tests/integration/
├── stacks.yml                      # the manifest: single source of truth for the CI matrix
├── conftest.py                     # stack-aware fixtures, capability-based skipping
├── test_submit.py                  # ONE backend-agnostic contract suite …
├── test_status.py
├── test_output.py                  # … including the fetch-twice destructiveness test
├── test_kill.py
└── stacks/
    ├── _images/                    # Dockerfiles shared across stacks
    │   ├── arc/Dockerfile          #   EL9 + pinned nordugrid-arc RPMs (ARG ARC_VERSION)
    │   ├── slurm/Dockerfile        #   EL9 + pinned slurm packages   (ARG SLURM_VERSION)
    │   └── htcondor-ce/Dockerfile  #   EL9 + pinned htcondor-ce RPM  (ARG HTCONDOR_CE_VERSION)
    ├── arc-slurm/
    │   ├── compose.yml
    │   └── config/
    │       └── basic/              # arc.conf, slurm.conf, intercede-client.toml
    ├── arc-condor/…
    ├── htcondor/…
    └── ssh-slurm/…                 # sshd enabled; local-slurm reuses this stack
```

Rules:

- One `compose.yml` per stack. Shared plumbing (network, credential volume, healthcheck blocks)
  is factored with YAML anchors or compose `include:`, **not** with a generated mega-file.
  A stack must be runnable locally with exactly
  `docker compose -f tests/integration/stacks/arc-slurm/compose.yml up --wait` — CI does nothing
  a developer cannot do on a laptop.
- Every configurable surface lives under `config/<name>/`. Today each stack has only
  `config/basic/`. A *configuration* is mounted into the containers (arc.conf, condor config,
  slurm.conf) **and** consumed by the test client (`intercede-client.toml` describing endpoint,
  credential type, queue names). Backend config and client config change together, atomically,
  under one name — this is what makes future configuration-matrix growth mechanical rather than
  a refactor.
- `stacks.yml` is the manifest the CI matrix is generated from:

  ```yaml
  stacks:
    - id: arc-slurm
      configs: [basic]
      versions: [latest]          # ARC 7.x leading edge (support window in §8)
      markers: "remote and arc"
    - id: htcondor
      configs: [basic]
      versions: [lts, latest]     # HTCondor 24.0 LTS anchor + leading edge
      markers: "remote and htcondor"
    - id: ssh-slurm
      configs: [basic]
      markers: "scheduler and slurm"
      exec_in_container: false
    - id: local-slurm
      stack: ssh-slurm            # reuses the ssh-slurm compose stack
      configs: [basic]
      markers: "scheduler and slurm"
      exec_in_container: true
  ```

  `versions` names the entries of the backend's support window to run (§8); a bare or absent
  `versions` means the single leading-edge pin. Adding a stack, a configuration, or a supported
  version is one manifest entry plus files — no workflow edits.

### 2. One contract test suite, capability-dispatched

There is a **single** integration test suite, not one per backend. Tests are written against the
interCEde protocols and parameterized by the stack's client configuration. Capability variance is
handled exactly the way the library itself handles it — protocol narrowing:

```python
async def test_output_refetch(backend, submitted_job, tmp_path):
    if not isinstance(backend, OutputRetriever):
        pytest.skip("backend has no output retrieval")
    first = await backend.fetch_output([submitted_job], dest=tmp_path)
    ...
```

This symmetry is deliberate: the integration suite is the executable form of the contract. If a
capability check works in the test harness, it works for DiracX; if a backend's structural typing
lies (the `runtime_checkable` presence-only problem from IC-ADR-001), the integration suite is
where the lie is caught against a real daemon. Backend-specific quirks (e.g. the HTCondor
one-shot retrieval semantics) get dedicated marker-selected tests rather than conditionals inside
generic ones.

Markers (`remote`, `scheduler`, `arc`, `htcondor`, `slurm`, `ssh`, `destructive_fetch`) select
the applicable subset per stack via the manifest's `markers` expression.

### 3. Prebuilt stack images on GHCR

ARC and Slurm images build from RPMs; rebuilding them on every PR wastes 5–10 minutes per job. A
separate `build-images` workflow builds and pushes
`ghcr.io/diracgrid/intercede-testenv/{arc,slurm,htcondor-ce}:<version>` whenever the Dockerfiles
change (including when Renovate bumps a pinned `ARG`), plus a weekly rebuild for base-image
security updates. PR CI **pulls** by digest.

Daemons run in the foreground under a minimal supervisor (or a plain entrypoint script), **never
systemd** — privileged systemd containers are fragile on GitHub runners and hide daemon logs from
`docker logs`.

### 4. Ephemeral credentials, generated per run

No credential is ever committed:

- **ARC stacks**: the container entrypoint runs `arcctl test-ca init` etc., generating the CA, a host
  certificate, and a client credential; client credentials are written to a shared
  `credentials` volume that the test harness reads. Token auth is added later as a second
  configuration (`config/token/`), not baked into `basic`.
- **HTCondor / HTCondor-CE**: `IDTOKENS` auth; the CE entrypoint mints a token into the shared
  volume. No GSI, no VOMS in `basic`.
- **SSH stacks**: `ssh-keygen` in a job step, public key mounted into the container's
  `authorized_keys`, known-hosts pinned from `ssh-keyscan`.

The shared credentials volume *is* the interface between stack and harness; its layout
(`credentials/<stack>/…`) is part of the stack contract.

### 5. Manifest-driven GitHub Actions matrix

```
.github/workflows/integration.yml
├── job: matrix        — reads stacks.yml, emits JSON via fromJSON()
└── job: integration   — matrix: {stack × config × version}, fail-fast: false
    ├── docker compose up --wait          (healthchecks gate readiness; version → image tag)
    ├── pixi run pytest tests/integration -m "<markers>" --stack=<id> --config=<name>
    │     (or `docker compose exec` for exec_in_container stacks)
    └── on failure: `docker compose logs` + backend log dirs uploaded as artifacts
```

- Each stack runs in its **own job**: parallel wall-clock, isolated failure blast radius, and each
  stays comfortably inside a 4 vCPU runner. There is no combined all-backends job.
- Healthchecks are mandatory in every compose service: ARC (`curl -k` the REST endpoint's info
  URL), HTCondor (`condor_status -limit 1` / `condor_ce_status`), Slurm (`sinfo` reporting the
  node up), sshd (TCP probe). `up --wait` then replaces every hand-rolled sleep-and-retry loop.
- The integration workflow is `workflow_call`-reusable, invoked from `ci.yml` after unit tests,
  and required for merge. Log artifacts on failure are non-negotiable: a red integration job
  without daemon logs is a re-run generator, not a signal.

### 6. Renovate as the version radar — two lanes

**Lane 1 — pinned, blocking (PRs).** Everything CI runs against is pinned:

- image tags in `compose.yml` files → `docker-compose` manager (enable in `renovate.json`);
- base images and pinned package versions in Dockerfiles → `dockerfile` manager plus
  `customManagers` regex on annotated `ARG`s:

  ```dockerfile
  # renovate: datasource=repology depName=fedora_epel_9/nordugrid-arc
  ARG ARC_VERSION=7.1.1
  ```

- Python dependencies → existing pep621 manager, unchanged.

For a backend with a support window (§8), Renovate tracks **only the leading-edge ARG**; the older
anchor ARGs are pinned and constrained (`packageRule` `allowedVersions`/`matchCurrentVersion`, or
`enabled: false`) so they accept only patch bumps within their major and never jump it.

A Renovate bump PR (e.g. HTCondor 25.7 → 25.8) triggers image rebuild + full integration matrix.
**A red Renovate PR is the feature**: it is the earliest, cheapest, fully-reproducible signal
that an upstream release broke an interCEde contract — the 25.8 destructive-fetch change would
have surfaced as exactly this. Backend bumps are grouped per backend (`arc`, `htcondor`,
`slurm`), never mixed, so a red PR names its culprit.

**Lane 2 — unpinned canary (scheduled, non-blocking).** A weekly scheduled workflow runs the same
matrix against `latest`/nightly backend tags. It cannot block merges; it exists to catch
upstream changes *before* they reach the stable tags Renovate tracks. Failures open a
deduplicated issue via workflow automation.

### 7. Configuration growth path

The full matrix is `(stack × config × version)`, starting as `(6 × basic × leading-edge)`. Planned
configuration axes, added as `config/<name>/` directories and manifest entries — each an additive
change:

- auth variants (token vs. proxy for ARC; token lifetimes for HTCondor-CE);
- filesystem variants (shared vs. non-shared session directories — the `stages_own_files` axis);
- queue topology (multiple queues, per-queue limits);
- resource constraints (memory/CPU limits surfacing scheduler translation bugs);
- a batch-host variant with **no usable Python interpreter**, asserting IC-ADR-001 §3's
  no-interpreter-on-the-remote-host property (DIRAC's SSH CE shipped a Python driver to the host;
  interCEde must never need one).

PR CI runs `basic` only; the scheduled workflow runs the full configuration set. This keeps PR
latency flat as the configuration matrix grows.

### 8. Backend version support window

interCEde is a *client* that must interoperate with backend daemons whose version it does not
control. Two kinds of "version" therefore live in this repo and must not be conflated:

- **Versions interCEde ships** — Python dependencies, base images. We own these; "bump to latest"
  is correct and Renovate does exactly that (§6).
- **Backend server versions interCEde interoperates with** — ARC, HTCondor(-CE), Slurm daemons.
  Here a pinned version is a *test fixture standing in for a version some site runs*, not a
  dependency we upgrade. Real sites run a spread — HTCondor LTS years after release, older ARC 7
  where the latest hasn't rolled out — so testing only the newest daemon answers the wrong
  question.

Each backend therefore declares a **support window**: the set of upstream versions interCEde
claims to work against, expressed as an *oldest/LTS anchor* plus the *leading edge*. Version is a
matrix axis alongside `config` (the `versions:` key in `stacks.yml`, §1).

- **Renovate tracks only the leading edge.** The leading-edge `<BACKEND>_VERSION` ARG bumps
  normally — this is Lane 1's radar unchanged: a new upstream release arrives as a red-or-green PR
  that names its culprit.
- **Older anchors are human-managed.** Each anchor ARG is pinned and constrained (§6) so Renovate
  offers only patch bumps within the anchor's major. **Moving the window — dropping an old major,
  adopting a new one — is a deliberate human PR**, because dropping support for a version sites
  still run is a decision, not an auto-merge.
- **PR latency stays flat.** As with `config`, PR CI runs a representative subset (leading edge,
  plus one anchor for the backend most exposed to version skew); the scheduled workflow runs the
  full version cross-product.

Multi-version testing is also what *justifies* version-conditional client code: the HTCondor
≥ 25.8 one-shot-retrieval shim is exactly such a case, and the window is where it earns its
regression coverage.

**ARC is supported from version 7 only.** ARC 6 (pre-REST era: GridFTP/EMI-ES data staging, LDAP
infosystem) and ARC 7 (REST-first, `arcctl`) are close to different backends, and interCEde's AREX
path is REST-native. ARC 6 is explicitly out of scope, so the ARC window's floor is 7.x, with no
v6 anchor.

## Rationale

The design follows directly from the drivers above: real daemons over fakes (to catch what
structural typing cannot), per-stack isolation over a shared environment (attributable failures
inside a small runner), pinned-plus-Renovate over unpinned tracking (every version change attached
to a reviewable PR), and manifest-plus-files growth over workflow surgery (so the configuration
matrix scales without CI edits). The resulting benefits and the trade-offs deliberately accepted
in exchange:

**Benefits**

- Contract violations against real daemons are caught at PR time, per backend, with logs.
- Upstream version compatibility becomes a reviewed, bisectable git history of Renovate PRs
  instead of tribal knowledge.
- Stacks double as reproducible local development environments — `compose up`, point your client
  config at localhost, develop against a real ARC.
- Adding backends (future: Cloud via a mock EC2/OpenStack endpoint) or configurations is
  manifest-plus-files, no CI surgery.
- The destructive-fetch semantics, the sorest point of IC-ADR-001, get a permanent regression test
  (`fetch twice, assert contract`) on every backend.

**Trade-offs and accepted costs**

- We own three Dockerfiles for daemons whose packaging we don't control; EL9 repo layout changes
  will occasionally break image builds (contained to the `build-images` workflow).
- Integration jobs add ~5–8 minutes wall-clock per PR (parallel across stacks). Accepted; unit
  tests remain the fast inner loop.
- Single-node Slurm/HTCondor pools do not exercise multi-node scheduling behaviour. Out of scope:
  interCEde's contract ends at the CE/scheduler interface; scheduling fidelity beyond it is the
  backend's problem.
- The version support window (§8) multiplies images and CI jobs per backend. Contained by running
  only the leading edge (plus one anchor) on PRs and the full cross-product on the schedule, and by
  keeping the window to a small anchor set rather than every historical release.
- The canary lane will produce noise when upstream nightlies are broken through no fault of ours.
  Mitigated by non-blocking status and issue deduplication.
- GitHub-hosted runners only (linux/amd64). No arm64, no macOS backend testing. Revisit only if a
  consumer materializes there.

## Rejected Ideas

**Kubernetes (kind/k3s) as the orchestration layer.** Buys multi-node realism and Helm-chart
reuse (diracx-charts) at the price of cluster boot time, YAML volume, and debuggability inside a
CI job. Nothing in the CE contract requires more than "daemon reachable on a port"; compose
delivers that in one file per stack. Revisit if interCEde ever tests in-cluster deployment
concerns, which today it explicitly doesn't have.

**testcontainers-python / per-test containers.** Excellent for a Postgres; wrong for daemons with
30–60 s startup (ARC, slurmctld+munge). Session-scoped stacks amortize startup once per job.
Per-test isolation is recovered logically (unique job tags/working dirs per test), not by
container churn.

**One mega-compose with every backend.** Single job, shared fate: one flaky daemon reds the
world, resource ceilings are shared, and log spelunking spans six daemons. Parallel per-stack
jobs are faster and attribute failures for free.

**GitHub Actions `services:`.** No compose semantics, no build, no startup ordering beyond
health of individual containers, awkward log retrieval. Fine for a Redis sidecar; not for a CE
and its LRMS.

**Installing backends directly on the runner (apt/yum in the job).** Fast to prototype,
impossible to pin properly (Ubuntu's Slurm is whatever the distro ships), nothing is reusable
locally, and ARC on Ubuntu is not a supported combination. Containers or nothing.

**Reusing DIRAC's certification/integration environment.** It exists, but it drags in the full
DIRAC server stack and its configuration system — the exact coupling interCEde was extracted to
escape. interCEde must be testable by someone who has never installed DIRAC.

**Tracking upstream only via the canary (no pinning).** Green-today-red-tomorrow CI with no
diff to point at. Pinning plus Renovate keeps every version change attached to a reviewable PR;
the canary is an early-warning supplement, not the mechanism of record.

**Nightly-only integration tests (nothing on PR).** Decouples breakage from the change that
caused it and lets contract violations merge. The whole value proposition is the red X *on the
PR that introduces the problem* — including Renovate's version-bump PRs.

## Open Issues

- Exact Renovate datasource for EL9 RPM pins (`repology` vs. a custom endpoint for the NorduGrid
  repo) — to be settled when the ARC Dockerfile lands.
- Whether `htcondor` stack `basic` uses `htcondor/mini` behind the CE or a cm/execute/submit
  trio; `mini` is preferred until a test needs role separation.
- Whether Cloud backend testing (mock OpenStack/EC2) becomes a seventh stack or stays unit-level;
  deferred until the Cloud backend is scheduled.
