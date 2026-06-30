# Prior-art survey: existing job-submission abstractions

> Companion note to [IC-ADR-001](../adr/IC-ADR-001_computing_elements.md) (Rejected Ideas,
> *"Reuse an existing job-submission abstraction instead of defining our own"*). This records the
> survey evidence behind that rejection; the ADR keeps only the conclusion. Surveyed 2026-06/07 —
> per-tool facts here go stale independently of the decision.

## What interCEde needs

A modern async, typed Python library covering, together: SSH + local batch (Slurm, HTCondor,
LSF, …), ARC-CE over its REST interface, HTCondor-CE, and cloud (Libcloud-style providers). No
surveyed tool covers this set.

## HTCondor GAHP / blahp

Not a library but a *protocol* — no C/Python binding; you spawn a helper binary and speak ASCII
over stdin/stdout — and a *family* of binaries: `blahp` covers only **local** batch
(Slurm/PBS/LSF/SGE/Condor), while ARC and cloud are separate `arc_gahp`/`ec2_gahp`/`gce_gahp`/
`azure_gahp`. blahp has **no SSH** — remote-cluster access is a `condor_gridmanager` +
`condor_remote_cluster`/BOSCO feature — so "SSH + Slurm" would drag in the whole HTCondor grid
stack. The protocol is internal, self-described as "SECOND DRAFT", with no back-compat guarantee.

Apache-2.0. Its per-LRMS submit/status/cancel scripts and its async request-id/`RESULTS`-poll
model are worth *learning from* — they are cited in IC-ADR-001's Rationale as prior art for the
`Scheduler` protocol and the async, bulk, poll-based contract.

The reverse direction — HTCondor reusing interCEde — is a non-goal: its gridmanager is C++ and
speaks a process protocol, and HTCondor-CE is a backend interCEde *targets*, so the layering runs
the other way.

## ATLAS PanDA / Harvester

Python, Apache-2.0, and has the exact submitter/monitor/sweeper plugin shape interCEde wants
(HTCondor, ARC via **aCT**, Slurm/LSF/PBS, cloud, k8s, HPC) — but its communicator talks only to
the PanDA server and its data model is PanDA specs, so it is not an importable standalone
library. Notably, ATLAS built Harvester *because* aCT was too tied to ARC-CE for US HPCs — the
same "generic beats CE-specific" lesson IC-ADR-001 applies.

## ALICE JAliEn

Its `alien.site.batchqueue` layer (ARC, HTCondor, direct Slurm/PBS, NERSC SuperFacility) is the
closest HEP analogue and does *both* grid-CE and direct-batch — but it is Java, never released as
an artifact (CVMFS-only), coupled to JAliEn's LDAP/central/token machinery, and of unconfirmed
licence.

## Generic libraries

RADICAL-SAGA, PSI/J, DRMAA, Parsl, AiiDA, Dask-Jobqueue are batch/HPC-scheduler oriented: the
ARC-CE REST **+** HTCondor-CE **+** cloud triple never co-occurs in any one of them. In fact
**none of the six speaks ARC at all**, in either the modern ARC-CE REST or the classic
ARC0/EMI-ES interface (RADICAL-SAGA never shipped a NorduGrid adaptor in any release — the Python
`radical.saga` is distinct from the old C++/Java SAGA; AiiDA has no `aiida-arc` plugin in core or
its registry). Where they touch "HTCondor" it is vanilla HTCondor/Condor-G, not the HTCondor-CE
grid gateway.

ARC-CE REST is itself well-supported — HTCondor's `arc_gahp` speaks it in C++ (libcurl against
`/arex/rest/1.0`), and interCEde uses the Python `pyarcrest` — but only through ARC-specific
clients, never through one of these generic scheduler abstractions.

Concurrency is *not* the differentiator: most of the six are async (futures/callbacks or a
non-blocking submit + poll/wait); DRMAA is the lone synchronous-style one, and it is unmaintained.

## Conclusion

Every HEP experiment builds its own CE abstraction and none is a reusable standalone library. The
genuinely reusable layer is the WLCG grid-CE ecosystem itself (HTCondor-CE, ARC-CE) plus
first-party per-backend clients (`pyarcrest`, the `htcondor` bindings, Apache Libcloud).
interCEde composes those behind a typed, async contract rather than adopting any monolith.
