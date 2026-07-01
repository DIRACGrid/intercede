# ARC CE + SLURM integration test (GitLab CI)

Spins up a single Docker container running a NorduGrid **ARC Compute
Element (ARC7)** wired to a single-node **SLURM** batch system, then
drives `arcsub` / `arcstat` / `arcget` against it to prove the whole
submit → monitor → retrieve path works end to end. Designed to run as
a GitLab CI pipeline (build stage + test stage), but also runnable
locally with `docker-compose`.

## Layout

```
docker/
  Dockerfile            AlmaLinux 9 image: munge + SLURM + ARC7 + systemd
  slurm.conf             single-node SLURM cluster config
  cgroup.conf             cgroups disabled (see note below)
  arc.conf                ARC CE config, LRMS=slurm, REST interface on :443
  bootstrap.sh            one-shot startup script (systemd unit runs this)
  arc-bootstrap.service   systemd unit that runs bootstrap.sh at boot
  healthcheck.sh          Docker HEALTHCHECK / CI readiness probe
test/
  job.xrsl                the test job description (xRSL)
  run.sh                  payload script executed on the SLURM worker
  run_integration_test.sh submit -> monitor -> retrieve driver script
.gitlab-ci.yml            build_image + integration_test pipeline
docker-compose.yml         local equivalent of the CI run
```

## How it fits together

1. **Image build** installs `munge`, `slurm`/`slurm-slurmctld`/`slurm-slurmd`,
   and ARC7 (`nordugrid-arc7-arex`, `nordugrid-arc7-client`,
   `nordugrid-arc7-arcctl`) from EPEL on AlmaLinux 9, and enables
   `systemd` as PID 1 — this matters because ARC's own tooling
   (`arcctl`) and the SLURM/munge packages ship real systemd unit
   files, and re-using those is far more reliable than hand-rolling a
   supervisor script.

2. **Container start** (`arc-bootstrap.service`, ordered after
   `munge`/`slurmctld`/`slurmd`) runs `bootstrap.sh`, which:
   - waits until `munge` and `sinfo` actually work,
   - (re)generates the ARC **Test-CA** and a **host certificate** bound
     to the container's *runtime* hostname (`arcctl test-ca hostcert -n
     $(hostname) -f`) — this can't be baked into the image at build
     time because the build-time hostname is a random ID, not `arc-ce`,
   - starts `arc-arex` / `arc-arex-ws` (`arcctl service start
     --as-configured`),
   - mints a Test-CA **client certificate** for `griduser01`
     (`arcctl test-ca usercert --install-user griduser01 -f`), which
     `arcctl` automatically whitelists in
     `/etc/grid-security/testCA.allowed-subjects` — this is what makes
     the CE's default "closed by default" `[authgroup: zero]` accept
     that user,
   - waits for the REST endpoint to answer and writes `/run/arc-ready`.

3. **Docker HEALTHCHECK** (`healthcheck.sh`) only reports `healthy`
   once `/run/arc-ready` exists, `sinfo` works, and the REST endpoint
   responds — the CI job polls this instead of guessing a fixed sleep.

4. **The test itself** (`test/run_integration_test.sh`, run as
   `griduser01` inside the container via `docker exec`):
   - `arcproxy` — generate a short-lived proxy from the Test-CA user cert
   - `arcinfo -C https://arc-ce/arex` — sanity-check the CE is reachable
   - `arcsub -C https://arc-ce/arex job.xrsl` — **submit**
   - poll `arcstat <jobid>` until `Finished` (or fail fast on
     `Failed`/`Killed`) — **monitor**
   - `arcget <jobid>` — **retrieve** `stdout.log` and `result.txt`,
     then assert their contents
   - `arcclean <jobid>` to tidy up

## Why systemd + `--privileged`

SLURM's daemons and ARC's `arcctl` assume a normal init system
(starting/stopping via `systemctl`, log rotation, etc). Running
`systemd` as PID 1 inside the container needs elevated privileges to
manage cgroups, so both the GitLab job and local `docker-compose` run
the container with `--privileged`.

**In GitLab, this means your Runner's `config.toml` must allow
privileged containers for the `docker:dind` service:**

```toml
[[runners]]
  executor = "docker"
  [runners.docker]
    privileged = true
```

If you can't get a privileged runner, the alternative is to drop
systemd entirely and hand-roll process supervision (e.g. `supervisord`
calling `munged`, `slurmctld -D`, `slurmd -D`, and the `A-REX` daemon
binary directly) — more portable, but you lose the packaged unit files
and have to reproduce their startup ordering/flags yourself.

## Why `cgroup.conf` disables cgroups

`TaskPlugin=task/none` and `ProctrackType=proctrack/linuxproc` in
`slurm.conf` avoid SLURM's cgroup-based process tracking, which
typically isn't usable inside a CI container even with `--privileged`
unless you also bind-mount the host's cgroup hierarchy. Fine for an
integration test that just proves the plumbing works; not
representative of production resource enforcement.

## Running locally

```bash
docker compose up --build -d
# watch it come up
docker inspect -f '{{.State.Health.Status}}' arc-ce-slurm-test
# once "healthy":
docker cp test/. arc-ce-slurm-test:/opt/arc-test/
docker exec arc-ce-slurm-test chown -R griduser01:griduser01 /opt/arc-test
docker exec arc-ce-slurm-test chmod +x /opt/arc-test/run_integration_test.sh /opt/arc-test/run.sh
docker exec -u griduser01 arc-ce-slurm-test /opt/arc-test/run_integration_test.sh
```

## Running in GitLab CI

Just push this repo (or merge these files into yours) with
`.gitlab-ci.yml` at the root. The `build_image` stage builds and saves
the image as a job artifact; `integration_test` loads it, runs it
privileged, waits for the health check, executes the test script
inside the container, and archives ARC/SLURM logs as artifacts
regardless of pass/fail.

## Things you'll likely want to change for a real environment

- **Package versions**: this pins nothing beyond "ARC7 from EPEL on
  EL9". For reproducible CI, pin `nordugrid-arc7-arex-<version>` etc.
  explicitly, or build from the upstream NorduGrid repo instead of
  EPEL (see https://www.nordugrid.org/arc/arc7/common/repos/repository.html).
- **Multi-container topology**: this is deliberately an all-in-one
  container (CE + SLURM + client in one box) to keep the CI pipeline
  simple. For something closer to production, split into an `arc-ce`
  service, a `slurmctld`/`slurmd` service (or a real multi-node SLURM
  cluster), and a separate `client` container talking to the CE over
  the Docker network, sharing a `munge.key` via a named volume.
- **Certificates**: this uses ARC's built-in Test-CA, which is exactly
  what it's for (throwaway integration testing). Never use it for
  anything reachable from outside your CI network.
- **Job payload**: `test/job.xrsl` / `test/run.sh` are a minimal
  smoke test. Extend them to cover whatever your real batch workloads
  look like (multi-core requests, input/output staging from object
  storage, RunTime Environments, etc).
