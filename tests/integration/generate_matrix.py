"""Generate the integration CI matrix from stacks.yml.

Preview locally with ``python tests/integration/generate_matrix.py``. Runs on the CI runner's
system python3, so it must not import pytest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

STACKS_FILE = Path(__file__).parent / "stacks.yml"
STACKS_DIR = Path(__file__).parent / "stacks"

REQUIRED_KEYS = ("id", "configs", "markers")


def load_manifest() -> list[dict[str, Any]]:
    """Return the stack entries from stacks.yml."""
    with STACKS_FILE.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "stacks" not in data:
        raise SystemExit(f"{STACKS_FILE}: expected a top-level `stacks:` list")
    return data["stacks"]


def compose_env(entry: dict[str, Any]) -> str:
    """Return the directory under stacks/ holding the stack's compose.yml."""
    return entry.get("compose", entry["id"])


def is_ready(entry: dict[str, Any]) -> bool:
    """Return whether both the stack's compose.yml and its own config/ have landed."""
    return (STACKS_DIR / compose_env(entry) / "compose.yml").is_file() and (
        STACKS_DIR / entry["id"] / "config"
    ).is_dir()


def build_matrix(stacks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand manifest entries into {stack x config x version} jobs."""
    include = []
    for entry in stacks:
        stack_id = entry.get("id")
        for key in REQUIRED_KEYS:
            if key not in entry:
                raise SystemExit(
                    f"stack {stack_id or entry!r}: missing required key {key!r}"
                )
        for config in entry["configs"]:
            for version in entry.get("versions", ["latest"]):
                include.append(
                    {
                        "stack": stack_id,
                        "compose_env": compose_env(entry),
                        "config": config,
                        "version": version,
                        "markers": entry["markers"],
                        "exec_in_container": entry.get("exec_in_container", False),
                    }
                )
    return include


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="append matrix=<json> to $GITHUB_OUTPUT instead of pretty-printing",
    )
    args = parser.parse_args()

    ready: list[dict[str, Any]] = []
    not_ready: list[dict[str, Any]] = []
    for entry in load_manifest():
        (ready if is_ready(entry) else not_ready).append(entry)
    matrix = build_matrix(ready)

    for entry in not_ready:
        note = f"stack {entry.get('id')!r} has no compose.yml or config/ yet - skipping"
        if args.github_output:
            print(f"::notice::{note}")
        else:
            print(note, file=sys.stderr)

    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write(f"matrix={json.dumps(matrix)}\n")
    else:
        json.dump(matrix, sys.stdout, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
