# Integration images

Dockerfiles for the stacks are in `tests/integration/stacks/_images/<name>/`, for example
`arc/`, `slurm/` or `htcondor-ce/`. They follow these rules.

1. **EL9 base.** Use `almalinux:9` and install the backend from pinned RPM versions.
2. **Each version is an `ARG` with a `# renovate:` comment.** Renovate reads the comment and
   opens a pull request when a new version is released:

   ```dockerfile
   FROM almalinux:9

   # renovate: datasource=repology depName=fedora_epel_9/nordugrid-arc
   ARG ARC_VERSION=7.1.1

   RUN dnf install -y epel-release \
       && dnf install -y "nordugrid-arc-arex-${ARC_VERSION}" \
       && dnf clean all
   ```

   You do not need to change `renovate.json` for a new image. The comment gives Renovate
   everything it needs. See [Renovate](renovate.md).
3. **No systemd, logs to stdout/stderr.** See rules 3 and 4 in
   [Rules for a stack](integration-stacks.md#rules-for-a-stack).
4. **Credentials are created when the container starts**, by the entrypoint. For example ARC
   creates a test CA, a host certificate and a client certificate with `arcctl test-ca`, and
   HTCondor creates IDTOKENS. See rules 6 and 9 in
   [Rules for a stack](integration-stacks.md#rules-for-a-stack).
5. **The version is a build argument**, so one Dockerfile can build every version listed in
   `versions`. Renovate updates only the default value of the `ARG`.
6. **One image per backend, even with several services.** For example a Slurm controller and a
   Slurm worker use the same image. The entrypoint chooses the daemon from a `ROLE` variable
   (`case "$ROLE"`).
7. **Secrets shared between services are created at start too.** For example the Slurm munge key
   or an internal CA. Write them to a volume inside the stack. Never put them in the image: the
   images will be published, so a secret in an image is public.

Later, CI will build the images and publish them as
`ghcr.io/diracgrid/intercede-testenv/<name>:<version>` (#14). Until then, stacks build them
locally with `build:`.
