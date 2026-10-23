# Integration stacks

## Words used here

- **Stack**: one backend that the tests run against, for example `htcondor` or `ssh-slurm`. Each
  stack is one entry in `tests/integration/stacks.yml`.
- **Compose environment**: the `compose.yml` file that starts the backend containers. It is
  usually in `stacks/<id>/`. Several stacks can share one (see `compose` below).
- **Configuration**: one way to set up the backend, for example `basic`. It is a folder
  `config/<name>/` with the backend's config files and `intercede-client.toml`, which tells the
  tests how to connect.
- **CI job**: one stack, with one configuration, at one backend version.

## Files

```
tests/integration/
├── stacks.yml              # list of stacks; CI creates its jobs from this file
├── conftest.py             # pytest fixtures: stack, stack_dir, client_config
├── generate_matrix.py      # turns stacks.yml into the list of CI jobs
├── test_manifest.py        # checks stacks.yml and the stack folders (no Docker needed)
└── stacks/
    ├── _template/          # copy this to start a new stack
    ├── _images/            # Dockerfiles (see integration-images.md)
    ├── htcondor/           # a stack with its own compose environment
    │   ├── compose.yml
    │   └── config/basic/   # backend config files + intercede-client.toml
    ├── slurm/              # a compose environment shared by two stacks
    │   ├── compose.yml
    │   └── config/basic/   # backend config files only (slurm.conf, cgroup.conf)
    ├── ssh-slurm/
    │   └── config/basic/   # intercede-client.toml only (connects over SSH)
    └── local-slurm/
        └── config/basic/   # intercede-client.toml only (runs inside the container)
```

## `stacks.yml` fields

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Name of the stack. Its client config is in `stacks/<id>/config/`. |
| `compose` | no | Folder in `stacks/` with the `compose.yml` to use. Default: the `id`. |
| `configs` | yes | Configuration names, folders in `config/`. Start with `[basic]`. |
| `versions` | no | Backend versions to test, for example `[lts, latest]`. Default: `[latest]`. |
| `markers` | yes | `pytest -m` expression that selects the tests for this stack. |
| `exec_in_container` | no | `true`: run pytest inside the backend container. Default: `false`. |

## When a stack gets CI jobs

A stack can be listed in `stacks.yml` before its files exist, for example while the pull request
that adds it is still open. `generate_matrix.py` creates CI jobs for a stack only when both of
these exist:

- `stacks/<compose>/compose.yml`
- `stacks/<id>/config/`

Otherwise it prints a notice and skips the stack.

## Sharing a compose environment

`ssh-slurm` and `local-slurm` test the same Slurm cluster. The only difference is how interCEde
connects to it. Both set `compose: slurm`, so both start `stacks/slurm/compose.yml`. Each stack has
its own `intercede-client.toml`.

`local-slurm` uses the markers `scheduler and slurm and not ssh`. Without `and not ssh`, the SSH
tests would run in both jobs. Add the same `and not ssh` to every new `local-*` stack.

## `exec_in_container: true`

pytest runs inside the backend container, and interCEde talks to the scheduler directly, without
SSH. For this to work (still to do, see #13 and #14):

- the image must contain pytest and the tests;
- pytest must run as a normal user, not root, because HTCondor and Slurm refuse to run jobs as
  root.

## Rules for a stack

Review checks these rules. `test_manifest.py` checks the file layout.

1. **It runs locally.** One `docker compose up --wait` starts it on a laptop, exactly as in CI.
2. **Every service has a healthcheck.** `up --wait` waits for the healthchecks. Do not use
   `sleep` and retry loops.
3. **No systemd.** Start daemons with a simple entrypoint script, in the foreground. systemd
   containers break easily on GitHub runners.
4. **Logs go to stdout/stderr.** On failure, CI saves only `docker compose logs`. If a daemon can
   only write to a file, use `/dev/stdout` as that file.
5. **Images come from `stacks/_images/`.** See [image rules](integration-images.md).
6. **Credentials are created when the container starts.** Never commit them. Write them, readable
   by everyone, to the `./credentials` bind mount, in `credentials/<id>/`. Do not use a Docker
   named volume: it belongs to root, so pytest on the host cannot read it. These are throwaway test
   credentials. The tests copy any file that needs strict permissions (0400/0600) to a private
   folder.
7. **A new configuration is a new folder.** Backend config and `intercede-client.toml` change
   together, in `config/<name>/`. To test something else (another auth method, queue, ...), add
   a new folder and a new name in `configs`. Do not change `basic`.
8. **The stack is listed in `stacks.yml`.** CI needs nothing else. You do not change the
   workflow.
9. **The host can reach it** (only when `exec_in_container` is `false`). Publish the backend port
   on `localhost` (compose `ports:`) and put the same `host:port` in `intercede-client.toml`. A
   TLS certificate must be valid for the name the client uses: create it for both the compose
   hostname and `localhost`. ARC on EL9 also needs the crypto policy set to `LEGACY`.

## pytest options and fixtures

Defined in `tests/conftest.py` (options) and `tests/integration/conftest.py` (fixtures):

| Name | Type | Meaning |
| --- | --- | --- |
| `--stack` | option | Stack `id` to test. Without it, tests that need a stack are skipped. |
| `--config` | option | Configuration name. Default: `basic`. |
| `stack` | fixture | The stack's entry from `stacks.yml`. |
| `stack_dir` | fixture | Folder with the stack's `compose.yml`. |
| `client_config` | fixture | The stack's `intercede-client.toml`, already parsed. |

If `intercede-client.toml` is missing, the test fails in CI (when `$CI` is set) and is skipped
locally. In CI, a job where every test is skipped would look green but test nothing.

Markers, defined in `pyproject.toml`: `remote`, `scheduler`, `arc`, `htcondor`, `slurm`, `ssh`,
`destructive_fetch`.
