"""Validate stacks.yml — the single source of truth for the CI matrix (IC-ADR-002 §1).

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
    compose_stack,
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


def test_stack_aliases_resolve():
    ids = {entry["id"] for entry in load_manifest()}
    for entry in load_manifest():
        alias = entry.get("stack")
        if alias is not None:
            assert alias in ids, (
                f"stack {entry['id']}: alias {alias!r} is not a known stack id"
            )


def test_matrix_expansion():
    """generate_matrix expands the versions axis and resolves stack aliases."""
    by_stack: dict[str, list[dict[str, Any]]] = {}
    for job in build_matrix(load_manifest()):
        by_stack.setdefault(job["stack"], []).append(job)
        assert {
            "stack",
            "compose_stack",
            "config",
            "version",
            "markers",
            "exec_in_container",
        } <= set(job)
    assert {job["version"] for job in by_stack["htcondor"]} == {"lts", "latest"}
    assert all(job["compose_stack"] == "ssh-slurm" for job in by_stack["local-slurm"])


def test_harmonized_stacks_follow_the_layout():
    """A stack whose directory has landed must follow the stack contract.

    ``compose.yml`` lives with the *compose* stack (the ``stack:`` alias target when set); the
    client config lives with the entry's *own* id, so an aliased entry (``local-slurm``) still ships
    its own ``intercede-client.toml`` even though it borrows another stack's compose environment.
    The contract is documented in docs/dev/reference/integration-stacks.md.
    """
    for entry in load_manifest():
        own_dir = STACKS_DIR / entry["id"]
        if not own_dir.is_dir():
            continue  # seeded in the manifest, not harmonized yet — files land with the stack PR
        compose_dir = STACKS_DIR / compose_stack(entry)
        assert (compose_dir / "compose.yml").is_file(), (
            f"{compose_dir} is missing compose.yml"
        )
        for config in entry["configs"]:
            client_toml = own_dir / "config" / config / "intercede-client.toml"
            assert client_toml.is_file(), (
                f"{own_dir} is missing config/{config}/intercede-client.toml"
            )
