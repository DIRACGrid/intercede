# local-slurm stack

A containerised single-node Slurm cluster for testing job submission locally and in GitHub CI.
This is the `local-slurm` stack described by IC-ADR-002 (integration testing against
containerized backends, currently proposed in PR #4) — it shares its image with the not-yet-built
`ssh-slurm` stack; the only difference is that tests here exec into the container instead of
connecting over SSH.

## Architecture

Two containers, both running the shared `slurm` image built from
[`../_images/slurm/Dockerfile`](../_images/slurm/Dockerfile), on a shared network:

| Container | Hostname | Role |
|---|---|---|
| `intercede-slurmctld` | `intercede-slurmctld` | Controller (`slurmctld`) — submit jobs here |
| `intercede-c1` | `intercede-c1` | Compute node (`slurmd`) |

The munge authentication key is **not** baked into the image. The controller generates it on
first start into a shared `munge-key` volume; the compute node waits for it to appear. Tearing
the stack down with `podman-compose down -v` removes the volume, so the next `up` starts with a
fresh key.

Both services declare a `healthcheck` (the daemon process is running) and the compute node's
`depends_on` gates on the controller's healthcheck via `condition: service_healthy` — so
`podman-compose up -d` doesn't start the compute node until the controller is actually up, no
hand-rolled sleeps. (`podman-compose`'s own `up --wait` never returns even once both containers
are healthy — a bug in this version — so wait for readiness with
`podman inspect --format='{{.State.Health.Status}}'` instead, as the CI workflow does.)
`sinfo`/`squeue` polling is still needed after that to observe node registration and job
completion — that's cross-container state convergence, not container startup, and isn't something
a single container's healthcheck can express.

## Prerequisites

- **Fedora/RHEL:** `dnf install podman podman-compose`
- **Ubuntu 26.04 / 24.04:** `apt install podman podman-compose`
- **Ubuntu 22.04:** `apt install podman && pip install podman-compose` (the apt package is too old)

## Local usage

**Start the cluster** (pulls the prebuilt image from GHCR):

```bash
podman-compose up -d
```

**...or build the image locally instead of pulling, e.g. while iterating on the Dockerfile:**

```bash
podman-compose up --build -d
```

**Check the cluster is up:**

```bash
podman-compose exec intercede-slurmctld sinfo
```

You should see `intercede-c1` with state `idle`.

**Submit a job:**

```bash
podman-compose exec intercede-slurmctld sbatch /test-jobs/simple.sh
```

**Watch the queue:**

```bash
podman-compose exec intercede-slurmctld squeue
```

**Submit a job array (4 tasks):**

```bash
podman-compose exec intercede-slurmctld sbatch --array=1-4 /test-jobs/array.sh
```

**Read job output** (written to `/tmp/` inside the compute node):

```bash
podman-compose exec intercede-c1 bash -c 'cat /tmp/slurm-*.out'
```

**Tail the Slurm logs:**

```bash
# Controller log
podman-compose exec intercede-slurmctld tail -f /var/log/slurm/slurmctld.log

# Compute node log
podman-compose exec intercede-c1 tail -f /var/log/slurm/slurmd.log
```

**Tear down** (`-v` also drops the munge-key volume):

```bash
podman-compose down -v
```

## Running your own job script

Write a batch script with `#SBATCH` directives and submit it via the controller:

```bash
podman cp my-job.sh intercede-slurmctld:/tmp/my-job.sh
podman-compose exec intercede-slurmctld sbatch /tmp/my-job.sh
```

## GitHub CI

The workflow in `.github/workflows/slurm-integration.yml` runs automatically on push and pull
request when files under `tests/integration/stacks/local-slurm/`,
`tests/integration/stacks/_images/slurm/`, or `src/` change. It:

1. Starts the cluster with `podman-compose up -d` (pulls the image built by
   `build-local-slurm-image.yml`) and polls both containers' health status until healthy
2. Submits the simple, array, and Collatz test jobs
3. Waits for all jobs to leave the queue
4. Prints job output
5. On failure, dumps the `slurmctld` and `slurmd` logs

The image itself is built and pushed to
`ghcr.io/diracgrid/intercede-testenv/slurm:latest` by a separate workflow
(`.github/workflows/build-local-slurm-image.yml`), triggered when the Dockerfile changes and on a
weekly schedule for base-image security updates — so PR CI just pulls, it doesn't rebuild.

This workflow is intentionally **not** wired into `ci.yml` / required for merge yet: there's no
real interCEde-vs-Slurm contract suite (IC-ADR-002 §2) to gate on, since `src/intercede` doesn't
have backend code yet. The three shell test-jobs here are a stand-in smoke test until that lands.

## Project structure

```
tests/integration/stacks/
├── _images/slurm/
│   ├── Dockerfile                       # Ubuntu 26.04 + pinned Slurm + Munge (shared image)
│   └── entrypoint.sh                    # Starts the right daemon based on role arg
└── local-slurm/
    ├── compose.yml                      # image: (GHCR) + build: (local) for either workflow
    ├── config/basic/
    │   ├── slurm.conf                   # Slurm configuration, bind-mounted at runtime
    │   └── cgroup.conf                  # Disables systemd scope creation (container-safe)
    └── test-jobs/
        ├── simple.sh                    # Single-task job
        ├── array.sh                     # 4-task array job
        └── collatz.sh                   # Collatz sequence (starting number = job ID % 1000)
```

## Configuration notes

- `ProctrackType=proctrack/linuxproc` — avoids cgroup kernel requirements inside containers
- `TaskPlugin=task/none` — likewise avoids cgroup task management
- `ReturnToService=2` — nodes automatically return to service after being down, useful when the compute container starts slightly after the controller
