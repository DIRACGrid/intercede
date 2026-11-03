# Why we test against real backends

## The problem with fakes

The unit tests check interCEde against fake backends. A fake can agree with our code while the
real service behaves differently. Many bugs that interCEde must handle come from the real services:
behaviour that changes between versions, output that can be downloaded only once, unusual job
states. Unit tests cannot find these bugs.

Example: since HTCondor 25.8, downloading the output of a spooled job deletes it on the server. You
can download it only once. A fake HTCondor does not do this.

## What the integration tests do

The integration tests start real backend services (ARC, HTCondor, Slurm) in containers and run
on pull requests. Each backend version is pinned. When a new version is released, Renovate opens a
pull request that updates it. If the new version breaks something, that pull request fails, and we
know which version caused the problem.

## Optional features

Some backends do not support every feature. The tests check for a feature in the same way that
users of the library do (`isinstance` against a `runtime_checkable` protocol), and skip the test
when the backend does not have it. A test asks for a capability by taking its fixture
(`output_retriever`, `killable`, `purgeable`).

## Phases

- **Phase 0.** Before the interCEde protocols existed, a stack was tested with the backend's own
  command-line tools (`arcsub`, `arcstat`, `arcget`, `condor_submit`, `sbatch`).
- **Phase 2 (now).** One set of tests, written against the interCEde protocols, runs on every stack
  (`test_submit.py`, `test_status.py`, `test_output.py`, `test_kill.py`, `test_purge.py`, and
  `test_errors.py` for what a consumer sees when things go wrong). These tests carry the
  `contract` marker, which selects them whatever a stack's `markers` say. A new backend writes no
  tests of its own: its stack runs this suite. Behaviour that only one backend has (for example
  HTCondor's one-time output download) gets its own test and marker (`destructive_fetch`), not an
  `if` inside a shared test.
