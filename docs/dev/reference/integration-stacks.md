# Integration stacks — layout, manifest schema, stack contract

Reference for the integration-test infrastructure decided in
[IC-ADR-002](../../adr/IC-ADR-002_integration_tests.md).

## Layout

```
tests/integration/
├── stacks.yml                      # THE manifest: single source of truth for the CI matrix
├── conftest.py                     # --stack/--config options, stack fixtures, capability skip
├── generate_matrix.py              # manifest → CI matrix expansion (runnable locally)
├── test_manifest.py                # manifest schema + layout checks (run in plain unit CI)
├── test_*.py                       # the backend-agnostic contract suite (Phase 2)
└── stacks/
    ├── _template/                  # copy me: compose.yml + config/basic/intercede-client.toml
    ├── _images/                    # shared Dockerfiles (see integration-images.md)
    └── <stack-id>/
        ├── compose.yml
        └── config/
            └── basic/              # backend config + intercede-client.toml, changed together
```

## Manifest schema (`stacks.yml`)

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stack name; also the directory under `stacks/` unless `stack:` is set |
| `stack` | no | Alias: reuse another stack's compose environment (e.g. `local-slurm` → `ssh-slurm`) |
| `configs` | yes | Configuration names under `stacks/<id>/config/`; start with `[basic]` |
| `versions` | no | Backend support-window entries to run (IC-ADR-002 §8); absent = single leading-edge pin |
| `markers` | yes | `pytest -m` expression selecting the applicable contract-suite subset |
| `exec_in_container` | no | Run pytest inside the backend container instead of against it (default `false`) |

The CI matrix is the expansion `{stack × config × version}` produced by
`tests/integration/generate_matrix.py`; each matrix entry becomes its own CI job. Only
*harmonized* stacks — those with a `stacks/<compose_stack>/compose.yml` — become jobs; a
seeded-only entry is announced with a `::notice` by `generate_matrix.py` and skipped.

**Aliases resolve two ways.** `stack:` reuses another stack's *compose environment* (so
`local-slurm` starts `ssh-slurm`'s containers), but the *client configuration* is always resolved
from the entry's **own** id: `local-slurm` ships its own `stacks/local-slurm/config/basic/
intercede-client.toml` (a different `kind`/endpoint — Local vs SSH transport) and its `markers`
exclude the borrowed transport's tests (`scheduler and slurm and not ssh` — the pattern every
future `local-*` alias needs so an `ssh`-marked test does not run in both the ssh and local jobs).

**`exec_in_container: true` runs pytest inside the batch container** (Local transport, no port 22).
The image must ship the suite + pytest — mounted or baked in (open TODO #13/#14) — and an
unprivileged test user, since HTCondor and Slurm refuse to run jobs as root; the workflow (and the
local `compose exec` recipe) pin that user with compose `user:` / `exec --user`.

## The stack contract

A stack that lands in `stacks/<id>/` commits to all of the following (enforced partly by
`test_manifest.py`, partly by review):

1. **One `compose.yml`, runnable locally.** CI does nothing a developer cannot do on a laptop
   (see [the how-to](../how-to/run-integration-tests-locally.md)).
2. **Healthchecks on every service.** `up --wait` gates on them; hand-rolled sleep-and-retry
   loops are not accepted.
3. **Foreground daemons, never systemd.** A plain entrypoint or minimal supervisor — privileged
   systemd containers are fragile on GitHub runners and hide daemon logs from `docker logs`
   (IC-ADR-002 §3). Daemons **log to stdout/stderr** so `docker compose logs` is the whole failure
   story; a file-logging daemon redirects its log to `/dev/stdout` rather than expecting a bespoke
   log-directory upload (the CI failure artifact is compose logs only).
4. **Images from `stacks/_images/`,** EL9 base, pinned Renovate-annotated `ARG`s (see
   [image conventions](integration-images.md)).
5. **Ephemeral credentials, minted at container start** into the shared, host-readable
   `./credentials` bind mount (gitignored) — never committed, and never a Docker *named* volume
   (host pytest cannot read a root-owned named volume for `exec_in_container: false` stacks). The
   layout `credentials/<id>/…` is the interface between stack and harness (IC-ADR-002 §4). Mint
   throwaway Test-CA material world-readable; the harness copies anything needing private modes
   (0400/0600) into a private dir itself.
6. **Configurations are atomic directories.** `config/<name>/` holds the backend config *and*
   `intercede-client.toml` together; a new auth/filesystem/queue variant is a new directory plus
   a `configs:` entry in `stacks.yml`, never a mutation of `basic` (IC-ADR-002 §7).
7. **One `stacks.yml` entry.** That is how CI finds the stack — adding a stack requires no
   workflow edits.
8. **Host reachability + TLS naming** (host-run stacks). A stack the harness drives from the host
   (`exec_in_container: false`) publishes its backend port on localhost (compose `ports:`) and
   records the same `host:port` in `intercede-client.toml`. A TLS backend's Test-CA host
   certificate must carry a SAN for the name the client actually dials — mint it for both the
   compose hostname and `localhost`. ARC on EL9 also needs the system crypto-policy set to `LEGACY`
   for the proxy chain to validate.

## Test harness

Options and fixtures provided by `tests/integration/conftest.py`:

| Name | Kind | Meaning |
| --- | --- | --- |
| `--stack` | option | Stack id from `stacks.yml`; stack-bound tests self-skip when absent (registered in `tests/conftest.py`, so every `pytest` invocation form accepts it) |
| `--config` | option | Configuration name (default `basic`) |
| `stack` | fixture | The selected manifest entry |
| `stack_dir` | fixture | Directory with the stack's `compose.yml` (follows the `stack:` alias) |
| `client_config` | fixture | Parsed `intercede-client.toml`, resolved under the entry's *own* id (not the alias) |

Markers, registered in `pyproject.toml`: `remote`, `scheduler`, `arc`, `htcondor`, `slurm`,
`ssh`, `destructive_fetch`. Each stack's `markers:` expression selects its subset. The
capability-narrowing skip helper (`isinstance` against a `runtime_checkable` protocol) lands with
the Phase-2 contract suite that calls it, alongside the interCEde protocols — it is intentionally
not part of this scaffold, whose signature would otherwise be guessed ahead of its callers.

A *selected* stack whose `intercede-client.toml` is missing **fails** under CI (`$CI` set) rather
than skipping: an all-skipped run would report a green integration cell that verified nothing
(IC-ADR-002 §5). Locally the same case skips, which stays convenient while a stack is being
brought up.
