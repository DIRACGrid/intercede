# IC-ADR-002: Integration testing against containerised backends

## Metadata

- **Created By:** Alexandre Boyer
- **Date:** 2026-07-02
- **Status:** Draft
- **Decision Maker(s):** Federico Stagni, Christophe Haen, Chris Burr
- **Stakeholders:** interCEde contributors and maintainers; DIRACGrid CI maintainers
- **Depends on:** IC-ADR-001 (protocols, composition, capability segmentation, closed backend set)

> **Scope, and how to read this.** This ADR sets a direction for the test architecture: what runs, where it runs, and how versions are managed. The contents of individual tests, the container definitions and the CI configuration are implementation, governed by the architecture decided here. Where a specific product is named below, it is because the decision depends on that product. Everything else is described by what it does, so that this document still reads correctly when the tool changes.

## Abstract

interCEde's contract is a set of structural protocols, and its unit tests exercise them against fakes. A structural protocol and a fake agree with each other forever, so the bugs interCEde exists to absorb are the ones unit tests cannot see.

This ADR therefore adds integration tests that run real backend daemons, in the combinations interCEde supports, on every pull request, inside a standard CI runner. The unit of everything is a **stack**: a named, self-contained backend environment described by one container-compose file plus versioned configuration, declared in a single manifest from which the CI matrix is generated. One backend-agnostic test suite is dispatched across stacks by capability. Every version is pinned and tracked automatically, so an upstream release that breaks a contract arrives as a red, bisectable pull request.

## Motivation

### What we have to support

1. **Sites run versions we do not control, and not the same ones.** A site may run a long-term-support release years after it shipped, or a version that is not the newest. Testing only against the newest daemon answers a question nobody asked.
2. **Some bugs exist only against a real daemon.** One HTCondor release changed output retrieval for spooled jobs so that collecting the files removed the job from the queue. Nothing about the client changed, and no unit test could have noticed.
3. **Several rules of the contract are only checkable against a real daemon.** Can output be collected twice? Did each copy of a submission receive its own secret? Do stdout and stderr stay clear of payload filenames? Is an impossible request refused at submit, rather than accepted and then failed?
4. **A new backend has to be able to prove it works.** IC-ADR-001 makes interCEde a closed library, and this suite is the reason: a backend is finished when it runs against a real daemon in CI, so the suite is the bar a contribution clears.
5. **A developer needs the same environment on a laptop.** Reproducing a site's behaviour should not require a site.

### Why fakes are not enough

The core architecture is easy to fake on purpose: everything is a protocol and backends are composed. That is also the testing risk. A fake keeps agreeing with the protocol while the real REST endpoint, the real submit-and-spool handshake, or the real quoting rules of a command run over SSH move away underneath it. Nothing in the type system notices, because nothing in the type system is wrong.

### Initial combinations

The first stacks cover each access path once and each scheduler once. A grid CE over REST sits in front of two different batch systems, and a grid gateway sits in front of a pool. SSH reaches a host running each of the two main batch systems. The same environment is also driven locally instead of over SSH. SSH reaches a host with no batch system at all. And a mock provider API stands in for a cloud, because the cloud backend is scheduled and its contract is small enough to test that way: submit, status, kill, and the reconciliation sweep that lists instances and releases the ones nobody recognises.

That last stack is the smallest end-to-end path in the library. A failure there cannot be the batch system's fault, which makes it the first place to look when the transport or sandbox layer misbehaves.

## Specification

### 1. A stack is the unit of everything

A **stack** is a named, self-contained backend environment. Each one has its own compose file, and shared plumbing is factored out by reference rather than by generating a combined file. A stack has to be startable locally with a single compose command: CI does nothing a developer cannot do on a laptop.

Every configurable surface of a stack lives under a named configuration directory. A configuration covers both halves at once: what is mounted into the containers, and what the test client needs in order to talk to them, such as the endpoint, the credential kind and the queue names. Both halves change together under one name, which is what makes growing the configuration matrix a mechanical step rather than a refactor. Each stack starts with one basic configuration.

A single manifest lists the stacks, the configurations and the versions to run, and the CI matrix is generated from it:

```yaml
stacks:
  - id: arc-slurm
    configs: [basic]
    versions: [latest]          # leading edge only; the support window is in §6
    markers: "remote and arc"
  - id: htcondor
    configs: [basic]
    versions: [lts, latest]     # long-term-support anchor plus leading edge
    markers: "remote and htcondor"
  - id: ssh-slurm
    configs: [basic]
    markers: "scheduler and slurm"
    exec_in_container: false
  - id: local-slurm
    stack: ssh-slurm            # reuses the ssh-slurm environment
    configs: [basic]
    markers: "scheduler and slurm"
    exec_in_container: true
  - id: ssh-direct
    configs: [basic]
    markers: "scheduler and direct"
    exec_in_container: false
  - id: cloud-mock
    configs: [basic]
    markers: "remote and cloud"
    exec_in_container: false
```

Adding a stack, a configuration or a supported version is one manifest entry plus files. No workflow edits are needed.

### 2. One contract test suite, dispatched by capability

There is a single integration suite, not one per backend. Tests are written against the interCEde protocols and parameterised by the stack's client configuration. Capability variance is handled the way the library handles it, by narrowing on the protocol:

```python
async def test_output_can_be_collected_twice(backend, submitted_job, tmp_path):
    if not isinstance(backend, OutputRetriever):
        pytest.skip("backend has no output retrieval")
    first = await backend.get_output([submitted_job], dest=tmp_path)
    second = await backend.get_output([submitted_job], dest=tmp_path)
    ...
```

That symmetry is the point: the suite is the executable form of the contract. A structural check that works in the harness works for the consumer. And where a backend's structural typing lies, because a runtime protocol check confirms only that methods exist, this is where the lie meets a real daemon.

Four rules of IC-ADR-001 are only checkable here, so the suite owns them:

- **Output can be collected twice.** IC-ADR-001 §3 makes retrieval repeatable on every backend, which for one scheduler depends on asking at submit time for the job to stay in its queue after collection. This test is what confirms that configuration actually holds, and it is the test that would have caught the upstream change described in the Motivation.
- **Per-copy identity and secrets.** Submit one specification with several sets of per-copy values (IC-ADR-001 §2.1) and assert that each job saw its own identifier and its own secret file, with the right mode. This catches a backend giving every copy the same value, which is the failure that matters most because the jobs still run.
- **stdout and stderr stay separate.** Submit a payload that writes files named the way the backend names its own streams, and assert the returned manifest keeps them apart (IC-ADR-001 §2.2).
- **Refusal is part of the contract.** A specification asking for something the backend cannot do has to fail at submit. There are three cases: an output left for collection on a backend that cannot collect, an output addressed to storage the resource cannot reach, and a container image on a backend with no way to honour it (IC-ADR-001 §2.3 and §2.4).

Markers select the applicable subset per stack through the manifest.

### 3. Prebuilt images

Some backends have no maintained upstream image, so we build our own from pinned packages. Rebuilding those on every pull request wastes several minutes per job, so a separate workflow builds and publishes them whenever their definitions change, including when an automated version bump changes a pinned version, plus a periodic rebuild for base-image security updates. Pull-request CI pulls by digest.

Daemons run in the foreground under a minimal supervisor or a plain entrypoint, never under a full init system. Privileged init containers are fragile on hosted runners and they hide daemon logs from the container runtime.

### 4. Credentials generated for each run

No credential is ever committed. Each stack generates its own at startup: a test certificate authority and host certificate where the backend needs one, a signed token where it uses tokens, and a fresh key pair for the SSH stacks with the host key pinned from the running container.

The shared credentials directory is the interface between a stack and the harness, and its layout is part of the stack contract. It is a host-readable bind mount rather than a volume owned by the container runtime, because the stacks that run the test client on the host cannot read a root-owned volume. The material is throwaway, so it is written world-readable; where a tool insists on private modes, the harness copies the file into a private per-run directory with the right mode rather than depending on user ids matching between host and container.

### 5. One job per stack

Each stack runs in its own CI job. That gives parallel wall-clock time, isolates failures, and keeps every job inside the resources of a standard hosted runner, which is what rules out running all backends together. Health checks are mandatory in every service, so readiness gating replaces hand-written sleep-and-retry loops.

Log artifacts on failure are mandatory. A red job with no daemon logs generates re-runs rather than information. Because daemons run in the foreground, the container runtime captures the whole failure story, and a daemon that insists on logging to a file redirects that file to its standard output instead.

### 6. Version tracking: two lanes

**Pinned and blocking.** Everything CI runs against is pinned: image tags, the package versions baked into our own images, and our Python dependencies. Automated dependency updates raise each bump as a pull request, which triggers an image rebuild and the full matrix.

A red version-bump pull request is the point of the design. It is the earliest, cheapest and fully reproducible signal that an upstream release broke a contract, and it names its own culprit as long as bumps are grouped per backend rather than mixed.

For a backend with a support window (§7), only the leading edge is tracked automatically. Older anchors are pinned and constrained so they accept fixes within their major version and never jump to a new one.

**Unpinned canary.** A scheduled workflow runs the same matrix against floating tags. It cannot block a merge. It exists to catch upstream changes before they reach the stable tags, and failures should open a deduplicated issue. This lane is planned rather than implemented.

### 7. Backend version support window

interCEde is a client that has to work with daemons whose version it does not control, so two kinds of version live in this repository and must not be confused.

Versions interCEde ships, meaning its own dependencies and base images, are ours, and "bump to latest" is the right answer. Backend server versions are not: there, a pinned version is a test fixture standing in for a version some site runs, not a dependency we upgrade.

Each backend therefore declares a support window: the set of versions interCEde claims to work against. A window is expressed as two ends. The **anchor** is the oldest version still supported, normally a long-term-support release, and it is the minimum a site can run and still be covered. The **leading edge** is the newest released version, and it is what tells us early that an upstream change has broken something. A window may have more than one anchor where sites are spread across two old majors.

The manifest of §1 is the authoritative list of which versions run, through its `versions` key, so the window for each backend can be read from one place rather than from prose. Version is a matrix axis alongside configuration. Moving the window, by dropping an old major or adopting a new one, is a deliberate human decision rather than an automatic merge, because dropping support for a version sites still run affects those sites.

Multi-version testing is also what justifies version-conditional client code, and the support window is where such code earns its regression coverage.

### 8. Growth path

The full matrix is stack times configuration times version. Pull-request CI runs the basic configuration and a representative subset of versions, and the scheduled workflow runs the full cross-product, so pull-request latency stays flat as the matrix grows.

Planned configuration axes, each an additive change:

- authentication variants, such as token against certificate, and shorter credential lifetimes;
- filesystem variants, covering whether the scheduler stages the sandbox itself;
- queue topology, with several queues and per-queue limits;
- resource constraints, which surface bugs in how requests are translated for each scheduler;
- a batch host with no usable interpreter, asserting that interCEde never needs to ship code to the remote host;
- an object store beside the grid stacks, so that resource-side staging is exercised for real, in both directions, with the harness never touching the bytes;
- a container variant, asserting that a backend either runs the payload in the requested image or refuses at submit;
- a site-override variant, asserting that text a site appends to the job description reaches the backend unchanged, and that an override colliding with a key the backend generates is refused at submit rather than silently applied.

## Rationale

Real daemons instead of fakes, because that is the only place the bugs live. One isolated environment per stack instead of a shared one, so a failure names its own backend and every job fits a standard runner. Pinning plus automated bumps instead of floating tags, so every version change arrives attached to a reviewable change with a CI verdict. Growth through manifest entries instead of workflow edits, so the matrix scales without CI surgery.

Two consequences are worth stating because they are easy to lose later. Stacks double as development environments, so "reproduce it locally" is the same command CI runs. And the rules that only a real daemon can check (§2) are the ones most likely to be quietly broken by a refactor, because nothing in the type system objects.

**Accepted costs**

- We maintain container definitions for daemons whose packaging we do not control, so upstream repository changes will occasionally break image builds. Contained to the image-building workflow.
- Integration jobs add several minutes of wall-clock time per pull request, in parallel across stacks. Unit tests remain the fast inner loop.
- Single-node pools do not exercise multi-node scheduling. Out of scope: the contract ends at the scheduler interface.
- The support window multiplies images and jobs per backend. Contained by running a subset on pull requests and the full cross-product on a schedule.
- The canary lane produces noise when upstream pre-releases are broken through no fault of ours. Mitigated by its non-blocking status and by issue deduplication.
- Hosted runners on one architecture only. Revisit if a consumer appears elsewhere.

## Rejected Ideas

- **A container orchestrator as the test substrate.** It buys multi-node realism and chart reuse, at the price of cluster start-up time, a large amount of configuration, and hard debugging inside a CI job. Nothing in the contract requires more than a daemon reachable on a port. Revisit if interCEde ever needs to test in-cluster deployment concerns, which today it does not have.
- **One container per test.** Right for a database, wrong for daemons that take tens of seconds to become ready. Session-scoped stacks pay that cost once per job, and per-test isolation is recovered logically through unique working directories rather than by restarting containers.
- **One combined environment with every backend.** One job, shared fate. A single unreliable daemon turns everything red, resource ceilings are shared, and reading logs means searching several daemons at once.
- **The CI provider's own service containers.** No compose semantics, no build step, no start-up ordering beyond individual health, and awkward log retrieval. Fine for a cache sidecar, not for a CE and its batch system.
- **Installing backends directly on the runner.** Fast to prototype, impossible to pin, since the distribution ships whatever it ships. Nothing is reusable locally, and some backends are not supported on the runner's distribution at all.
- **Reusing an existing full-stack certification environment.** It exists, but it pulls in a whole server stack and its configuration system. interCEde has to be testable by someone who has installed nothing else.
- **Tracking upstream only through the canary, with no pinning.** CI that is green today and red tomorrow, with no change to point at. Pinning keeps every version change attached to a reviewable pull request; the canary is an early warning, not the mechanism of record.
- **Nightly-only integration tests, with nothing on pull requests.** It separates a breakage from the change that caused it and lets contract violations merge. The value is the failure appearing on the pull request that introduces it, version bumps included.

- **Generating all credentials once, in a shared job before the backend jobs run.** It looks cheaper, and it would centralise teardown. It does not fit, for two reasons. The material is backend-specific and is produced by the daemon that will accept it: a test certificate authority for one backend, a signed token for another, a key pair the SSH host has to trust. A shared job would have to reimplement each of those mechanisms and then inject the result into containers that have not started yet. It would also break the rule that a stack runs on a laptop with one command, because a stack would no longer be able to bring itself up. Generating credentials inside each stack keeps the stack self-contained, and since every stack is torn down at the end of its own job, nothing outlives the run anyway.
