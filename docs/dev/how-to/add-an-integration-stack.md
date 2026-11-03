# Add an integration stack

Words like *stack*, *compose environment* and *configuration* are explained in
[Integration stacks](../reference/integration-stacks.md#words-used-here).

You do not need to change any CI workflow. CI finds the stack in `stacks.yml`.

## Steps

1. **Copy the template.** Copy `tests/integration/stacks/_template/` to
   `tests/integration/stacks/<id>/`. Then fill in:
   - `compose.yml`: a real healthcheck, and the `./credentials` bind mount;
   - `config/basic/`: the backend config files and `intercede-client.toml`.

   If your stack uses a backend that another stack already starts (for example a second way to
   connect to Slurm), do not copy `compose.yml`. Set `compose:` to the existing folder, and add
   only `config/basic/intercede-client.toml`. See
   [Sharing a compose environment](../reference/integration-stacks.md#sharing-a-compose-environment).
2. **Add the image** in `tests/integration/stacks/_images/<name>/`. Follow the
   [image rules](../reference/integration-images.md).
3. **Add the stack to `tests/integration/stacks.yml`**: `id`, `configs`, `markers`, and if needed
   `compose`, `versions`, `exec_in_container`.
4. **Test it locally:**

   ```sh
   docker compose -f tests/integration/stacks/<id>/compose.yml up --wait
   pixi run -e py313 pytest tests/integration -n 4 -m "<markers>" --stack=<id> --config=basic
   pixi run -e py313 pytest tests/integration/test_manifest.py
   ```

## Checklist for the pull request

- [ ] The stack follows the [rules for a stack](../reference/integration-stacks.md#rules-for-a-stack).
- [ ] `docker compose ... up --wait` works on your machine.
- [ ] The images use Docker, not Podman.
- [ ] `intercede-client.toml` is filled in: `id`, endpoint, queue, credential type and path.
- [ ] `pytest tests/integration/test_manifest.py` passes.
- [ ] The shared suite passes on this stack (`pytest tests/integration -m "<markers>" --stack=<id>`).
      You do not write tests for a new backend: the stack runs the `contract` tests, skipping
      those for capabilities the backend does not have.
- [ ] If your pull request had its own workflow for this stack, remove it. `integration.yml` runs
      on every pull request that changes `tests/integration/`. It does not block merging yet
      (#14), so keep your own workflow only if you need a check that blocks merging.
