"""Generate the integration CI matrix from stacks.yml (IC-ADR-002 §5).

Run it locally to see exactly which {stack x config x version} jobs CI would run:

    pixi run -e py313 python tests/integration/generate_matrix.py

The integration workflow (.github/workflows/integration.yml) calls it with --github-output to
append the matrix to $GITHUB_OUTPUT. Only *harmonized* stacks — those whose stacks/<id>/compose.yml
has landed — become jobs; stacks that are merely seeded in the manifest are announced and skipped
here (rather than by a per-job step), so an unharmonized stack never turns a matrix job red.

This module is also the single home of the manifest helpers (``load_manifest``, ``compose_stack``):
conftest.py and test_manifest.py import them from here. It must therefore stay import-light — the
workflow runs it with the runner's *system* python3, so it may not import pytest. See
docs/dev/reference/integration-stacks.md for the manifest schema.
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
    """Return the list of stack entries from stacks.yml (the CI-matrix source of truth)."""
    with STACKS_FILE.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "stacks" not in data:
        raise SystemExit(f"{STACKS_FILE}: expected a top-level `stacks:` list")
    return data["stacks"]


def compose_stack(entry: dict[str, Any]) -> str:
    """Return the compose environment a stack runs in: its own id, or the `stack:` alias."""
    return entry.get("stack", entry["id"])


def is_harmonized(entry: dict[str, Any]) -> bool:
    """Report whether the stack's compose.yml has landed (vs. merely seeded in the manifest)."""
    return (STACKS_DIR / compose_stack(entry) / "compose.yml").is_file()


def build_matrix(stacks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand manifest entries into the flat {stack x config x version} job list."""
    include = []
    for entry in stacks:
        stack_id = entry.get("id")
        for key in REQUIRED_KEYS:
            if key not in entry:
                # Name the offending stack instead of dying with a bare KeyError deep in a loop.
                raise SystemExit(
                    f"stack {stack_id or entry!r}: missing required key {key!r}"
                )
        for config in entry["configs"]:
            for version in entry.get("versions", ["latest"]):
                include.append(
                    {
                        "stack": stack_id,
                        "compose_stack": compose_stack(entry),
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

    harmonized: list[dict[str, Any]] = []
    seeded: list[dict[str, Any]] = []
    for entry in load_manifest():
        (harmonized if is_harmonized(entry) else seeded).append(entry)
    matrix = build_matrix(harmonized)

    # Seeded-but-not-yet-harmonized stacks are not jobs. Announce them — as a CI annotation under
    # --github-output, else on stderr so the JSON on stdout stays clean — so the manifest's intent
    # stays visible without a skipped-job placeholder in the check UI.
    for entry in seeded:
        note = (
            f"stack {entry.get('id')!r} is seeded in stacks.yml but has no compose.yml yet"
            " - skipping"
        )
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
