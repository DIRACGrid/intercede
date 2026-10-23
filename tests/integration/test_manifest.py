"""Validate stacks.yml — the single source of truth for the CI matrix.

These tests need no running stack: they keep the manifest schema honest and enforce the stack
contract layout (docs/dev/reference/integration-stacks.md) for every stack that has landed. They
run in the plain unit-test job. The manifest helpers come from generate_matrix (kept pytest-free so
the workflow can run it with the system python3).
"""

from __future__ import annotations

from typing import Any

from tests.integration.generate_matrix import (
    STACKS_DIR,
    build_matrix,
    compose_env,
    load_manifest,
)


def test_entries_have_required_keys():
    for entry in load_manifest():
        assert "id" in entry, f"manifest entry without an id: {entry}"
        assert entry.get("configs"), (
            f"stack {entry.get('id')}: `configs` must be a non-empty list"
        )
        assert entry.get("markers"), (
            f"stack {entry.get('id')}: `markers` expression is required"
        )


def test_stack_ids_are_unique():
    ids = [entry["id"] for entry in load_manifest()]
    assert len(ids) == len(set(ids)), f"duplicate stack ids in stacks.yml: {ids}"


def test_compose_names_a_stacks_directory():
    """`compose:` is a plain directory name under stacks/, not a path or a reserved _ directory."""
    for entry in load_manifest():
        compose = entry.get("compose")
        if compose is not None:
            assert (
                isinstance(compose, str)
                and compose
                and "/" not in compose
                and not compose.startswith((".", "_"))
            ), (
                f"stack {entry['id']}: `compose: {compose!r}` must be a directory name under stacks/"
            )


def test_matrix_expansion():
    """generate_matrix expands the versions axis and resolves `compose:` directories."""
    by_stack: dict[str, list[dict[str, Any]]] = {}
    for job in build_matrix(load_manifest()):
        by_stack.setdefault(job["stack"], []).append(job)
        assert {
            "stack",
            "compose_env",
            "config",
            "version",
            "markers",
            "exec_in_container",
        } <= set(job)
    assert {job["version"] for job in by_stack["htcondor"]} == {"lts", "latest"}
    for stack_id in ("ssh-slurm", "local-slurm"):
        assert all(job["compose_env"] == "slurm" for job in by_stack[stack_id])


def test_harmonized_stacks_follow_the_layout():
    """A stack whose directory has landed must follow the stack contract.

    ``compose.yml`` lives in the compose environment (``stacks/<compose>/`` when ``compose:`` is
    set); the client config lives under the entry's *own* id, so ``ssh-slurm`` and ``local-slurm``
    each ship their own ``intercede-client.toml`` next to the shared ``stacks/slurm/``.
    The contract is documented in docs/dev/reference/integration-stacks.md.
    """
    for entry in load_manifest():
        own_dir = STACKS_DIR / entry["id"]
        if not own_dir.is_dir():
            continue  # seeded in the manifest, not harmonized yet — files land with the stack PR
        compose_dir = STACKS_DIR / compose_env(entry)
        assert (compose_dir / "compose.yml").is_file(), (
            f"{compose_dir} is missing compose.yml"
        )
        for config in entry["configs"]:
            client_toml = own_dir / "config" / config / "intercede-client.toml"
            assert client_toml.is_file(), (
                f"{own_dir} is missing config/{config}/intercede-client.toml"
            )
