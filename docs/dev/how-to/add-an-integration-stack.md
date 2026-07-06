# Add an integration stack

A *stack* is a named, self-contained backend environment (one `docker compose` file plus
versioned configuration) that the integration suite runs against. Adding one requires **no
workflow edits** — the CI matrix is generated from the manifest.

Before starting, read the [stack contract](../reference/integration-stacks.md) — the rules below
reference it.

## Steps

1. **Copy the template**: `tests/integration/stacks/_template/` →
   `tests/integration/stacks/<id>/`. Fill in `compose.yml` (real healthcheck, host-readable
   `./credentials` bind mount) and `config/basic/` (backend config + `intercede-client.toml`,
   which change together as one atomic configuration). An **aliased** stack (`stack:` reusing
   another's compose) ships only `config/` under its own id — no `compose.yml` of its own — and its
   `markers` exclude the borrowed transport (e.g. `and not ssh`).
2. **Add the image** under `tests/integration/stacks/_images/<name>/` following the
   [image conventions](../reference/integration-images.md) (EL9, pinned Renovate-annotated
   `ARG`s, foreground daemons, ephemeral credentials at start).
3. **Add the manifest entry** in `tests/integration/stacks.yml` (`id`, `configs`, `markers`,
   optionally `versions` / `stack:` alias / `exec_in_container`).
4. **Verify locally**:

   ```sh
   docker compose -f tests/integration/stacks/<id>/compose.yml up --wait
   pixi run -e py313 pytest tests/integration -m "<markers>" --stack=<id> --config=basic
   pixi run -e py313 pytest tests/integration/test_manifest.py   # layout checks
   ```

## Harmonization checklist

For a stack PR (including the in-flight prototype stacks converging onto this layout):

- [ ] Files under `stacks/<id>/` following `_template/` (compose.yml + `config/basic/`)
- [ ] `docker compose … up --wait` succeeds locally; healthchecks probe real readiness
- [ ] Daemons foregrounded (no systemd), logging to stdout/stderr; Docker (not Podman) on
      EL9-based images
- [ ] Dockerfile under `stacks/_images/<name>/` with pinned, `# renovate:`-annotated `ARG`s
- [ ] Credentials generated at start, world-readable, into the host-readable `./credentials` bind
      mount under `credentials/<id>/…` (gitignored)
- [ ] `intercede-client.toml` filled in (own `id`, endpoint, credential type/path, queue); a
      host-run stack publishes its port on localhost with a matching Test-CA SAN
- [ ] `stacks.yml` entry present; `pytest tests/integration/test_manifest.py` green
- [ ] At least one marker-selected Phase-0 smoke test ships with the stack (so its job isn't just
      "no tests ran" — the workflow tolerates that, but a smoke test makes the job meaningful)
- [ ] Bespoke per-PR workflow removed: the manifest-driven `integration.yml` now runs
      automatically on any PR touching `tests/integration/**`, so the stack self-tests. It stays
      **non-blocking** until issue #14 (GHCR image pulls + `ci.yml` invocation + required check),
      so keep a bespoke workflow only if you need a *gating* check in the interim
- [ ] `docker compose logs` captures the daemons' output on failure (daemons log to stdout; a
      file-logging daemon redirects to `/dev/stdout`)
