#!/usr/bin/env python3
"""Inspect DIMACS graphs and optionally delete graphs with isolated vertices."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def count_isolated_vertices(path: Path) -> int:
    """Scan edges with one bit per vertex; loops do not establish a neighbor.

    This checks header syntax, endpoint bounds, and edge-line counts. It is
    an isolation filter, not a duplicate-edge validator. Malformed files raise
    ValueError and must not be deleted based on an incomplete scan.
    """
    vertex_count: int | None = None
    expected_edges = 0
    edge_count = 0
    active_count = 0
    active = bytearray()
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            fields = line.split()
            if not fields or fields[0] == "c":
                continue
            try:
                if fields[0] == "p":
                    if vertex_count is not None or len(fields) != 4 or fields[1] != "edge":
                        raise ValueError("expected one 'p edge n m' header")
                    vertex_count, expected_edges = map(int, fields[2:])
                    if vertex_count < 0 or expected_edges < 0:
                        raise ValueError("negative vertex or edge count")
                    active = bytearray((vertex_count + 7) // 8)
                elif fields[0] == "e":
                    if vertex_count is None or len(fields) != 3:
                        raise ValueError("edge before header or malformed edge")
                    u, v = map(int, fields[1:])
                    if not (1 <= u <= vertex_count and 1 <= v <= vertex_count):
                        raise ValueError("vertex ID outside 1..n")
                    edge_count += 1
                    if u == v:
                        continue
                    for vertex in (u, v):
                        byte, bit = divmod(vertex - 1, 8)
                        mask = 1 << bit
                        if not active[byte] & mask:
                            active[byte] |= mask
                            active_count += 1
                else:
                    raise ValueError("unknown DIMACS record")
            except ValueError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
    if vertex_count is None or edge_count != expected_edges:
        raise ValueError(f"{path}: missing header or edge count mismatch")
    return vertex_count - active_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="A .col file or a directory to scan recursively")
    parser.add_argument(
        "--remove-isolated", action="store_true",
        help="Delete entire .col files with isolated vertices; otherwise only report",
    )
    args = parser.parse_args()
    if args.input is None:
        project_root = os.environ.get("PROJECT_ROOT")
        if not project_root:
            parser.error("set PROJECT_ROOT or provide an input path")
        datasets = Path(project_root) / "datasets"
        files = (
            path for stage in ("processed", "selected")
            for directory in sorted(datasets.glob(f"*/{stage}"))
            for path in directory.rglob("*.col") if path.is_file()
        )
    elif args.input.is_file() and args.input.suffix.lower() == ".col":
        files = iter((args.input,))
    elif args.input.is_dir():
        files = (path for path in args.input.rglob("*.col") if path.is_file())
    else:
        parser.error("input must be an existing .col file or directory")

    scanned = affected = errors = 0
    for path in files:
        scanned += 1
        try:
            isolated = count_isolated_vertices(path)
            if isolated:
                if args.remove_isolated:
                    path.unlink()
                affected += 1
                action = "REMOVED" if args.remove_isolated else "WOULD REMOVE"
                print(f"{action}: {path}: {isolated} isolated vertices")
        except (OSError, ValueError, MemoryError) as error:
            errors += 1
            print(f"ERROR (not removed): {path}: {error}")
    print(f"Scanned: {scanned}; graphs with isolated vertices: {affected}; errors: {errors}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
