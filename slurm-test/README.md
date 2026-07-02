# Slurm Test Cluster

A containerised single-node Slurm cluster for testing job submission locally and in GitHub CI.

## Architecture

Two containers are built from the same image and run on a shared network:

| Container | Hostname | Role |
|---|---|---|
| `intercede-slurmctld` | `intercede-slurmctld` | Controller (`slurmctld`) — submit jobs here |
| `intercede-c1` | `intercede-c1` | Compute node (`slurmd`) |

The munge authentication key is baked into the image at build time so both containers share it automatically — no runtime volume coordination needed.

## Prerequisites

- **Fedora/RHEL:** `dnf install podman podman-compose`
- **Ubuntu 26.04 / 24.04:** `apt install podman podman-compose`
- **Ubuntu 22.04:** `apt install podman && pip install podman-compose` (the apt package is too old)

## Local usage

**Build and start the cluster:**

```bash
podman-compose up --build
```

**In a separate terminal, check the cluster is up:**

```bash
podman exec intercede-slurmctld sinfo
```

You should see `intercede-c1` with state `idle`.

**Submit a job:**

```bash
podman exec intercede-slurmctld sbatch /test-jobs/simple.sh
```

**Watch the queue:**

```bash
podman exec intercede-slurmctld squeue
```

**Submit a job array (4 tasks):**

```bash
podman exec intercede-slurmctld sbatch --array=1-4 /test-jobs/array.sh
```

**Read job output** (written to `/tmp/` inside the compute node):

```bash
podman exec intercede-c1 bash -c 'cat /tmp/slurm-*.out'
```

**Tail the Slurm logs:**

```bash
# Controller log
podman exec intercede-slurmctld tail -f /var/log/slurm/slurmctld.log

# Compute node log
podman exec intercede-c1 tail -f /var/log/slurm/slurmd.log
```

**Tear down:**

```bash
podman-compose down
```

## Running your own job script

Write a batch script with `#SBATCH` directives and submit it via the controller:

```bash
podman cp my-job.sh intercede-slurmctld:/tmp/my-job.sh
podman exec intercede-slurmctld sbatch /tmp/my-job.sh
```

## GitHub CI

The workflow in `.github/workflows/slurm-test.yml` runs automatically on push and pull request. It:

1. Builds the image with `podman build`
2. Creates a `slurm` network and starts both containers
3. Polls `sinfo` until the compute node shows `idle`
4. Submits the simple and array test jobs
5. Waits for all jobs to leave the queue
6. Prints job output
7. On failure, dumps the `slurmctld` and `slurmd` logs

## Project structure

```
.
├── Containerfile                        # Rocky Linux 9 + EPEL Slurm + Munge
├── compose.yml                          # Local development with podman-compose
├── configs/
│   └── slurm.conf                       # Slurm configuration
├── scripts/
│   └── entrypoint.sh                    # Starts the right daemon based on role arg
├── test-jobs/
│   ├── simple.sh                        # Single-task job
│   └── array.sh                         # 4-task array job
└── .github/
    └── workflows/
        └── slurm-test.yml               # GitHub Actions CI workflow
```

## Configuration notes

- `ProctrackType=proctrack/linuxproc` — avoids cgroup kernel requirements inside containers
- `TaskPlugin=task/none` — likewise avoids cgroup task management
- `ReturnToService=2` — nodes automatically return to service after being down, useful when the compute container starts slightly after the controller
