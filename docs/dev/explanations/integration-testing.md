# Why integration stacks — testing against real backend daemons

The full reasoning is [IC-ADR-002](../../adr/IC-ADR-002_integration_tests.md); this page is the
short version plus the phasing plan.

## Why real daemons

interCEde's architecture is deliberately mock-friendly — everything is a `Protocol`, and the unit
suite exercises contracts against fakes. That is also its greatest testing risk: a structural
protocol and a fake will happily agree with each other forever while the real ARC REST endpoint
or the real `condor_submit -spool` handshake drift away underneath. The bugs interCEde exists to
absorb (version-specific daemon behaviour, destructive output retrieval, status-mapping quirks)
are exactly the bugs unit tests structurally cannot see — HTCondor ≥ 25.8 silently turning
spooled-output retrieval into a one-shot operation is the canonical example.

So the integration suite runs **real backend daemons** in containerised **stacks**, on every pull
request, and every backend version is pinned and tracked by Renovate so an upstream release that
breaks a contract surfaces as a red, bisectable PR.

## Capability symmetry

The integration suite is the executable form of the contract: it narrows to optional capabilities
exactly the way the library's consumers do (`isinstance` against a `runtime_checkable` protocol)
and skips where a backend genuinely lacks a capability. If a capability check works in the test
harness, it works for DiracX; if a backend's structural typing lies, a real daemon is where the lie
is caught. The skip helper that does this lands with the Phase-2 contract suite that calls it —
its signature would otherwise be guessed ahead of its callers.

## Phase 0 vs Phase 2

**Phase 0 (now):** the `intercede` package's protocols don't exist yet, so stack PRs validate
their stacks with **native client tools** (`arcsub`/`arcstat`/`arcget`, `condor_submit`,
`sbatch`) in marker-selected test modules.

**Phase 2:** a single backend-agnostic **contract suite** (`test_submit.py`, `test_status.py`,
`test_output.py`, `test_kill.py`) written against the interCEde protocols replaces the
native-tool tests. Backend-specific semantics (e.g. HTCondor's destructive fetch) get dedicated
marker-selected tests (`destructive_fetch`) rather than conditionals inside generic ones.
