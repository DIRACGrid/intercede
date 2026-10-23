# Integration tests

Tests that run against real backends (ARC, HTCondor, Slurm) in containers. Each backend setup is
called a *stack*.

```sh
docker compose -f tests/integration/stacks/<id>/compose.yml up --wait
pixi run -e py313 pytest tests/integration -m "<markers>" --stack=<id> --config=basic
```

Documentation:

- [Run the tests locally](../../docs/dev/how-to/run-integration-tests-locally.md)
- [Add a stack](../../docs/dev/how-to/add-an-integration-stack.md)
- [Integration stacks: files, `stacks.yml` fields, rules](../../docs/dev/reference/integration-stacks.md)
- [Image rules](../../docs/dev/reference/integration-images.md)
- [Why we test against real backends](../../docs/dev/explanations/integration-testing.md)
