"""Check stacks.yml and the layout of every stack that has landed."""

from __future__ import annotations

from tests.integration.generate_matrix import STACKS_DIR, compose_env, load_manifest


def test_entries_have_required_keys():
    for entry in load_manifest():
        assert "id" in entry, f"manifest entry without an id: {entry}"
        assert entry.get("configs"), f"stack {entry['id']}: `configs` is required"
        assert entry.get("markers"), f"stack {entry['id']}: `markers` is required"


def test_stack_ids_are_unique():
    ids = [entry["id"] for entry in load_manifest()]
    assert len(ids) == len(set(ids)), f"duplicate stack ids in stacks.yml: {ids}"


def test_landed_stacks_follow_the_layout():
    for entry in load_manifest():
        own_dir = STACKS_DIR / entry["id"]
        if not own_dir.is_dir():
            continue
        compose_dir = STACKS_DIR / compose_env(entry)
        assert (compose_dir / "compose.yml").is_file(), (
            f"{compose_dir} is missing compose.yml"
        )
        for config in entry["configs"]:
            client_toml = own_dir / "config" / config / "intercede-client.toml"
            assert client_toml.is_file(), f"{client_toml} is missing"
