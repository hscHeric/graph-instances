#!/usr/bin/env python3
"""Generate graphs for Barabási-Albert in DIMACS .col format.

Constructs scale-free preferential-attachment graphs via
``networkx.barabasi_albert_graph`` across explicit vertex counts
(``--n``/``--vertices``) or a geometric progression (``--doubling``), attaching
``--m`` edges per new vertex and writing ``ba_n<n>_m<m>_<rep>.col``.

Notes:
    * Requires ``1 <= m < n`` for every ``(n, m)`` combination.
    * Does not require ``PROJECT_ROOT``; default output directory is
      ``random_barabasi_albert/`` when invoked directly.
    * ``--seed`` increments by 1 after each generation attempt.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import networkx as nx


def generate_geometric_progression(start: int, factor: float, steps: int) -> list[int]:
    """Generate a deduplicated geometric progression of rounded integer sizes.

    Args:
        start: Initial vertex count.
        factor: Positive multiplicative growth factor per step.
        steps: Number of progression steps.

    Returns:
        Ordered list of unique integer vertex counts ``round(start * factor**step)``.
    """
    return list(dict.fromkeys(round(start * factor**step) for step in range(steps)))


def save_dimacs(graph: nx.Graph, path: Path, comment: str = "") -> None:
    """Write a simple NetworkX graph in DIMACS ``.col`` format (1-based IDs).

    Args:
        graph: Simple undirected graph to serialize.
        path: Destination ``.col`` file path.
        comment: Optional single-line comment written with a ``c `` prefix.
    """
    mapping = {node: index for index, node in enumerate(graph.nodes(), start=1)}
    edges = sorted(
        tuple(sorted((mapping[u], mapping[v]))) for u, v in graph.edges()
    )

    with path.open("w", encoding="utf-8", newline="\n") as file:
        if comment:
            file.write(f"c {comment}\n")
        file.write(f"p edge {graph.number_of_nodes()} {len(edges)}\n")
        for u, v in edges:
            file.write(f"e {u} {v}\n")


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the Barabási-Albert generator.

    Returns:
        Parsed CLI namespace.
    """
    parser = argparse.ArgumentParser(
        description="Generate graphs for Barabási-Albert in DIMACS .col format"
    )
    parser.add_argument(
        "--n", "--vertices",
        type=int,
        nargs="+",
        default=None,
        help="Vertex counts; overrides --doubling",
    )
    parser.add_argument(
        "--doubling", type=float, nargs=3, default=[200, 1.15, 30],
        metavar=("START", "FACTOR", "STEPS"),
        help="Geometric progression (default: 200 1.15 30)",
    )
    parser.add_argument(
        "--m",
        type=int,
        nargs="+",
        default=[4],
        help="Edges per new vertex (default: 4)",
    )
    parser.add_argument(
        "--reps", "--count",
        type=int,
        default=1,
        help="Repetitions per combination (default: 1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Initial seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("random_barabasi_albert"),
        help="Output directory (default: random_barabasi_albert)",
    )
    parser.add_argument("--remove-isolated", action="store_true",
                        help="Discard entire graphs containing an isolated vertex")
    return parser.parse_args()


def get_vertex_counts(args: argparse.Namespace) -> list[int]:
    """Return explicit vertex counts or expand and validate ``--doubling``.

    Args:
        args: Parsed CLI namespace.

    Returns:
        List of vertex counts to generate.

    Raises:
        ValueError: If ``--doubling`` parameters are invalid.
    """
    if args.n is not None:
        return args.n
    start, factor, steps = args.doubling
    if not all(math.isfinite(value) for value in args.doubling):
        raise ValueError("Geometric progression parameters must be finite")
    if not start.is_integer() or not steps.is_integer():
        raise ValueError("START and STEPS for --doubling must be integers")
    if start < 1 or factor <= 0 or steps < 1:
        raise ValueError("START and STEPS must be at least 1; FACTOR must be positive")
    values = generate_geometric_progression(int(start), factor, int(steps))
    print(f"Generated geometric progression ({len(values)} sizes): {values}")
    return values


def validate_arguments(args: argparse.Namespace, vertex_counts: list[int]) -> None:
    """Validate CLI parameters before generating any files.

    Args:
        args: Parsed CLI namespace.
        vertex_counts: Resolved list of vertex counts.

    Raises:
        ValueError: If ``reps < 1``, any ``n < 1``, or any ``m`` fails
            ``1 <= m < n``.
    """
    if args.reps < 1:
        raise ValueError("reps must be at least 1")

    for n in vertex_counts:
        if n < 1:
            raise ValueError(f"n must be positive (received n={n})")
        for m in args.m:
            if m < 1 or m >= n:
                raise ValueError(
                    f"m must be positive and less than n (received n={n}, m={m})"
                )


def main() -> None:
    """Generate Barabási-Albert graphs and save them as DIMACS ``.col`` files.

    Raises:
        ValueError: If any CLI argument fails validation.
    """
    args = parse_args()
    vertex_counts = get_vertex_counts(args)
    validate_arguments(args, vertex_counts)

    output_directory = args.out
    output_directory.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in vertex_counts:
        for m in args.m:
            for repetition in range(args.reps):
                graph = nx.barabasi_albert_graph(n, m, seed=seed)
                name = f"ba_n{n}_m{m}_{repetition}.col"
                file_path = output_directory / name
                if args.remove_isolated and any(degree == 0 for _, degree in graph.degree()):
                    file_path.unlink(missing_ok=True)
                    print(f"REMOVED/SKIPPED: {name}: isolated vertices")
                    seed += 1
                    continue
                save_dimacs(
                    graph,
                    file_path,
                    comment=(
                        f"Barabási-Albert n={n} m={m} "
                        f"repetition={repetition} seed={seed}"
                    ),
                )
                print(
                    f"OK: {name}: {graph.number_of_nodes()} vertices, "
                    f"{graph.number_of_edges()} edges"
                )
                total += 1
                seed += 1

    print(f"Completed: {total} graphs generated in {output_directory}")


if __name__ == "__main__":
    main()
