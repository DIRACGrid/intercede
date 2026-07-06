# Run the integration tests locally

Everything CI does is reproducible on a laptop — a design rule of
[IC-ADR-002](../../adr/IC-ADR-002_integration_tests.md): CI does nothing a developer cannot do
locally.

## Run one stack

1. Start the stack; healthchecks gate readiness, so when `up --wait` returns the backend is
   usable:

   ```sh
   docker compose -f tests/integration/stacks/<id>/compose.yml up --wait
   ```

2. Run the applicable test subset — the stack's `markers:` expression is in
   `tests/integration/stacks.yml`:

   ```sh
   pixi run -e py313 pytest tests/integration -m "<markers>" --stack=<id> --config=basic
   ```

For `local-*` stacks (`exec_in_container: true`) the tests run *inside* the batch container
instead:

```sh
docker compose -f tests/integration/stacks/<compose-stack>/compose.yml exec --user <test-user> backend \
  pytest tests/integration -m "<markers>" --stack=<id> --config=basic
```

Two conditions this recipe depends on (both open TODOs, #13/#14): the image must ship the suite
and pytest (mounted or baked in), and it must run as an **unprivileged** user — HTCondor and Slurm
refuse to run jobs as root, hence `--user <test-user>` (or a compose `user:`). Note `--stack`
still names the aliased entry (e.g. `local-slurm`) so its own `intercede-client.toml` is used.

Stacks double as development environments: leave the stack up and point your client config at
`localhost` to develop against a real backend.

## Preview the CI matrix

The CI matrix is generated from `stacks.yml` by a plain script — run it locally to see exactly
which `{stack × config × version}` jobs CI would create:

```sh
pixi run -e py313 python tests/integration/generate_matrix.py
```

(The workflow calls the same script with `--github-output`.)

## Manifest-only checks (no Docker needed)

The manifest schema and stack-layout checks run as ordinary unit tests:

```sh
pixi run -e py313 pytest tests/integration/test_manifest.py
```
