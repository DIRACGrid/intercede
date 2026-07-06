# Renovate — dependency and version tracking

This repository uses [Renovate](https://www.mend.io/renovate/) to keep dependencies up to date.
The configuration is defined in [`renovate.json`](../../../renovate.json).

## Dependency groups

1. **Python dependencies** — core Python packages from `pyproject.toml`
2. **Testing dependencies** — Pytest and related testing tools
3. **Build dependencies** — Hatchling and build tools
4. **GitHub Actions** — GitHub Actions workflow dependencies
5. **Integration-stack backends** — ARC, HTCondor(-CE), and Slurm daemon versions, grouped per
   backend so a red bump PR names its culprit (see below)

## Update strategy

- **Minimum release age**: updates are proposed only once a release is at least 7 days old (30 days
  for major Python updates)
- **Update types**: minor, patch, pin, and digest updates
- **Grouping**: related dependencies are grouped together
- **Major versions**: handled separately for better control
- **Automerge**: disabled (requires manual review)
- **Security**: vulnerability alerts are enabled; security updates are labeled and prioritized

## Integration stack backends

The integration stacks ([IC-ADR-002](../../adr/IC-ADR-002_integration_tests.md) §6) pin the backend
daemon versions they test against, and Renovate tracks those pins so a new upstream release arrives
as a reviewable red-or-green PR rather than tribal knowledge. Three managers are enabled for this:

- **`docker-compose`** — upstream image tags referenced directly in a stack's `compose.yml`
  (e.g. `htcondor/mini:<tag>`).
- **`dockerfile`** — base images in `tests/integration/stacks/_images/*/Dockerfile`.
- **`custom.regex`** — pinned RPM/package versions carried as `# renovate:`-annotated `ARG`s in
  those Dockerfiles. The annotation itself declares the datasource, depName and (optionally)
  versioning:

  ```dockerfile
  # renovate: datasource=repology depName=fedora_epel_9/nordugrid-arc
  ARG ARC_VERSION=7.1.1
  ```

**Adding a stack needs no `renovate.json` edit.** A stack author writes the annotated `ARG`; the
manager and per-backend grouping already configured here pick it up. Bumps are grouped per backend
(`arc`, `htcondor`, `slurm`) and never mixed.

**Version windows (§8) are half-automatic.** Renovate tracks the *leading-edge* `ARG` normally.
The *older anchor* versions (e.g. an HTCondor LTS line) are human-managed: pin them and add a
`packageRule` with `allowedVersions`/`matchCurrentVersion` (or `enabled: false`) — landed alongside
the stack that introduces the anchor — so Renovate offers only in-major patch bumps and never
silently moves the support window.

## How it works

1. Renovate checks for dependency updates on schedule
2. Creates pull requests for available updates
3. Groups related dependencies together
4. Applies labels and follows the project's update strategy
5. Requires manual review and approval before merging

## Changing the configuration

Edit [`renovate.json`](../../../renovate.json) and adjust package rules, schedules, or grouping
strategies (see the [Renovate documentation](https://docs.renovatebot.com/)). Validate before
committing:

```sh
npx --package renovate renovate-config-validator renovate.json
```

## Ignored paths

Renovate ignores `.pixi/` environments, `__pycache__/` directories, and virtual environments, so it
only updates the actual dependency definitions in `pyproject.toml`, GitHub Actions workflows, and
the integration stacks' image / `ARG` version pins.
