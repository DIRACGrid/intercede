# Integration tests

Backend-agnostic tests that run against real, containerised backend daemons ("stacks"), per
[IC-ADR-002](../../docs/adr/IC-ADR-002_integration_tests.md).

Quickstart:

```sh
docker compose -f tests/integration/stacks/<id>/compose.yml up --wait
pixi run -e py313 pytest tests/integration -m "<markers>" --stack=<id> --config=basic
```

Documentation lives in [`docs/dev/`](../../docs/dev/index.md):

- How-to: [run the tests locally](../../docs/dev/how-to/run-integration-tests-locally.md) ·
  [add a stack](../../docs/dev/how-to/add-an-integration-stack.md) (incl. the harmonization
  checklist)
- Reference: [layout, manifest schema, stack contract](../../docs/dev/reference/integration-stacks.md) ·
  [image conventions](../../docs/dev/reference/integration-images.md)
- Explanation: [why integration stacks](../../docs/dev/explanations/integration-testing.md)
