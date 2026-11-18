#!/usr/bin/env python3
"""Inspect DIMACS graphs and optionally delete graphs with isolated vertices.

Streams DIMACS ``.col`` files line by line using a compact 1-bit-per-vertex
``bytearray`` bitset (~12.5 MB per 100 million vertices) without loading edge
sets into memory. Reports graphs with one or more isolated vertices by default
(dry run) and deletes entire affected ``.col`` files when ``--remove-isolated``
is supplied.

Validation Contract:
    * Enforces a single ``p edge n m`` header with non-negative counts, valid
      ``1..n`` endpoint bounds, known record prefixes (``c``, ``p``, ``e``),
      and an exact edge-line count match.
    * Self-loops (``u == v``) count toward ``m`` but do not establish a
      neighbor, so loop-only vertices still count as isolated.
    * Malformed or unreadable files are reported and left untouched; exit
      status is ``1`` if any file raises an error.
    * Does not check for duplicate undirected edges or repair files.

Environment Variables:
    PROJECT_ROOT: Required only when no positional ``input`` path is provided
        (defaults to scanning ``datasets/*/processed`` and ``datasets/*/selected``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from metadata_utils import base_provenance, resolve_metadata_path, update_metadata


def count_isolated_vertices(path: Path) -> int:
    """Count vertices with no non-loop incident edge using one bit per vertex.

    Streams a DIMACS ``.col`` file line by line, validating header syntax,
    endpoint bounds (``1..n``), and declared edge-line count. Self-loops
    (``u == v``) count toward the edge-line total but do not mark a vertex as
    having a neighbor. This function does not detect duplicate undirected edges.

    Args:
        path: Path to the DIMACS ``.col`` file to inspect.

    Returns:
        Number of declared vertices (``1..n``) that appear in no non-loop edge.

    Raises:
        ValueError: If the file has a missing/duplicate header, negative counts,
            unknown records, out-of-range endpoints, or an edge-count mismatch.
        OSError: If reading ``path`` fails.
        MemoryError: If allocating the vertex bitset exceeds available memory.
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
                    # Allocate 1 bit per declared vertex (ceil(vertex_count / 8) bytes).
                    active = bytearray((vertex_count + 7) // 8)
                elif fields[0] == "e":
                    if vertex_count is None or len(fields) != 3:
                        raise ValueError("edge before header or malformed edge")
                    u, v = map(int, fields[1:])
                    if not (1 <= u <= vertex_count and 1 <= v <= vertex_count):
                        raise ValueError("vertex ID outside 1..n")
                    edge_count += 1
                    # Self-loops do not give a vertex a distinct neighbor.
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
    """Scan ``.col`` files and report or delete whole graphs with isolated vertices.

    Without a positional ``input``, scans ``datasets/*/processed`` and
    ``datasets/*/selected`` under ``PROJECT_ROOT``. Malformed or unreadable
    files are reported and left untouched.

    Raises:
        SystemExit: Exits with status ``2`` on invalid CLI arguments or missing
            ``PROJECT_ROOT``, or status ``1`` if one or more files raised an
            error during scanning.
    """
    parser = argparse.ArgumentParser(
        description="Inspect DIMACS graphs and optionally delete graphs with isolated vertices."
    )
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
    affected_records: list[dict[str, object]] = []
    error_records: list[dict[str, str]] = []
    for path in files:
        scanned += 1
        try:
            isolated = count_isolated_vertices(path)
            if isolated:
                if args.remove_isolated:
                    path.unlink()
                affected += 1
                action = "REMOVED" if args.remove_isolated else "WOULD REMOVE"
                affected_records.append(
                    {
                        "file": path.name,
                        "path": str(path),
                        "isolated_vertices": isolated,
                        "removed": args.remove_isolated,
                    }
                )
                print(f"{action}: {path}: {isolated} isolated vertices")
        except (OSError, ValueError, MemoryError) as error:
            errors += 1
            error_records.append({"file": str(path), "error": str(error)})
            print(f"ERROR (not removed): {path}: {error}")

    if args.input is not None and args.input.is_dir():
        metadata_path = update_metadata(
            resolve_metadata_path(args.input),
            "isolation_filter",
            {
                **base_provenance(),
                "target": str(args.input),
                "mode": "remove" if args.remove_isolated else "dry_run",
                "summary": {
                    "scanned": scanned,
                    "affected": affected,
                    "errors": errors,
                },
                "affected_graphs": affected_records,
                "errors": error_records,
            },
        )
        print(f"Metadata saved to {metadata_path}")

    print(f"Scanned: {scanned}; graphs with isolated vertices: {affected}; errors: {errors}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
