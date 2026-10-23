# Renovate — dependency and version tracking

This repository uses [Renovate](https://www.mend.io/renovate/) to keep dependencies up to date.
The configuration is defined in [`renovate.json`](../../../renovate.json).

## Dependency groups

1. **Python dependencies** — core Python packages from `pyproject.toml`
2. **Testing dependencies** — Pytest and related testing tools
3. **Build dependencies** — Hatchling and build tools
4. **GitHub Actions** — GitHub Actions workflow dependencies
5. **Integration-stack backends** — ARC, HTCondor(-CE) and Slurm versions, one group per
   backend, so a failing update shows which backend caused it (see below)

## Update strategy

- **Minimum release age**: updates are proposed only once a release is at least 7 days old (30 days
  for major Python updates)
- **Update types**: minor, patch, pin, and digest updates
- **Grouping**: related dependencies are grouped together
- **Major versions**: handled separately for better control
- **Automerge**: disabled (requires manual review)
- **Security**: vulnerability alerts are enabled; security updates are labeled and pri## Integration stack backends

The integration stacks pin the version of each backend they test. Renovate opens a pull request
when a new version is released. If the tests fail, we know the new version is the cause.

Renovate finds the versions in three places:

- **`docker-compose`**: image tags in a stack's `compose.yml` (for example `htcondor/mini:<tag>`).
- **`dockerfile`**: base images in `tests/integration/stacks/_images/*/Dockerfile`.
- **`custom.regex`**: package versions in those Dockerfiles, written as an `ARG` with a
  `# renovate:` comment. The comment tells Renovate where to look for new versions:

  ```dockerfile
  # renovate: datasource=repology depName=fedora_epel_9/nordugrid-arc
  ARG ARC_VERSION=7.1.1
  ```

**A new stack needs no change to `renovate.json`.** Write the `ARG` with its comment and Renovate
finds it. Updates are grouped per backend (`arc`, `htcondor`, `slurm`), never mixed.

**Only the newest version is updated automatically.** Older versions that we still support (for
example an HTCondor LTS version in `versions: [lts, latest]`) are updated by hand. When you add
such a version, also add a `packageRule` (`allowedVersions` or `matchCurrentVersion`, or
`enabled: false`) so that Renovate proposes only patch updates for it.

 window.

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
