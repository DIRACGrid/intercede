# Run the integration tests locally

CI runs the same commands as below, so you can reproduce any CI job on your machine.

## Run the tests for one stack

1. Start the stack. The command returns when all healthchecks pass, so the backend is ready:

   ```sh
   docker compose -f tests/integration/stacks/<compose>/compose.yml up --wait
   ```

   `<compose>` is the stack's `compose` value in `tests/integration/stacks.yml`, or its `id` if it
   has none.

2. Run the tests. Copy `<markers>` from the stack's entry in `stacks.yml`:

   ```sh
   pixi run -e py313 pytest tests/integration -m "<markers>" --stack=<id> --config=basic
   ```

For stacks with `exec_in_container: true` (the `local-*` stacks), run the tests inside the
container instead:

```sh
docker compose -f tests/integration/stacks/<compose>/compose.yml exec --user <test-user> backend \
  pytest tests/integration -m "<markers>" --stack=<id> --config=basic
```

This does not work yet (#13, #14): the image must first contain pytest and the tests, and have a
normal (not root) user. `--stack` is the stack `id` (for example `local-slurm`), not the compose
folder (`slurm`).

You can also leave a stack running and use it to develop against a real backend: point your client
config at `localhost`.

## See which CI jobs will run

```sh
pixi run -e py313 python tests/integration/generate_matrix.py
```

This prints the jobs that CI will create from `stacks.yml`.

## Check `stacks.yml` and the stack folders

No Docker needed:

```sh
pixi run -e py313 pytest tests/integration/test_manifest.py
```
