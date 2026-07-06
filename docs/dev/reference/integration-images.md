# Integration images — Dockerfile conventions

Dockerfiles under `tests/integration/stacks/_images/` are shared across stacks (`arc/`, `slurm/`,
`htcondor-ce/`, …) and follow [IC-ADR-002](../../adr/IC-ADR-002_integration_tests.md) §3 and §6:

1. **EL9 base** (`almalinux:9`), pinned by tag; backend daemons installed from **pinned RPMs**.
2. **Every version is a Renovate-annotated `ARG`** so upstream releases arrive as red-or-green
   Renovate PRs (Lane 1):

   ```dockerfile
   FROM almalinux:9

   # renovate: datasource=repology depName=fedora_epel_9/nordugrid-arc
   ARG ARC_VERSION=7.1.1

   RUN dnf install -y epel-release \
       && dnf install -y "nordugrid-arc-arex-${ARC_VERSION}" \
       && dnf clean all
   ```

   The `dockerfile` + `custom.regex` managers that read these annotations are already enabled in
   [`renovate.json`](../../../renovate.json) (per-backend grouping and the leading-edge/anchor
   convention are documented in [the Renovate reference](renovate.md)) — **adding a stack needs no
   `renovate.json` edit**, only the annotated `ARG`. The annotation carries its own `datasource`,
   so the exact datasource for the NorduGrid/EL9 repos (an open point of IC-ADR-002) is chosen when
   the ARC Dockerfile lands, without touching the manager config.
3. **Foreground entrypoint** — daemons run under a plain entrypoint script or minimal supervisor,
   never systemd, and **log to stdout/stderr** so `docker compose logs` captures everything (a
   file-logging daemon redirects its log to `/dev/stdout`). The CI failure artifact is compose
   logs only; there is no mounted log-dir upload.
4. **Ephemeral credentials at start**: the entrypoint mints whatever the stack needs (ARC:
   `arcctl test-ca` CA + host cert + client credential; HTCondor(-CE): IDTOKENS) and writes
   client-side material world-readable into the host-readable `./credentials` bind mount
   (`credentials/<id>/…`, IC-ADR-002 §4). A TLS host certificate carries a SAN for the name the
   client dials (the compose hostname *and* `localhost` for host-run stacks); ARC on EL9 needs the
   crypto-policy set to `LEGACY` for the proxy chain.
5. **Version windows**: where a backend declares an anchor + leading edge (IC-ADR-002 §8), the
   image accepts the version as `ARG`/build-arg so the same Dockerfile serves every entry of the
   `versions:` axis; only the leading-edge `ARG` default is Renovate-tracked.
6. **Roles and intra-stack secrets** (multi-service stacks). A CE + LRMS or a Slurm
   controller + worker is one parameterized image, not several: a `case "$ROLE"` entrypoint
   selects the daemon per service rather than duplicating Dockerfiles. Secrets shared *between*
   services (a munge key, an internal CA) obey the same rule as client credentials — minted at
   container start into a stack-internal volume, **never baked into the image** (a GHCR-published
   image with a baked munge key is a leaked credential).

Images are built and pushed to `ghcr.io/diracgrid/intercede-testenv/<name>:<version>` by the
`build-images` workflow; until that lands, stacks may `build:` locally from these directories.
