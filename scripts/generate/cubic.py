#!/usr/bin/env python3
"""Generate random cubic graphs in DIMACS .col format."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import networkx as nx


DEGREE = 3


def generate_geometric_progression(
    start: int, factor: float, steps: int
) -> list[int]:
    """Generate a geometric progression rounded to even integers."""
    values: list[int] = []
    current = float(start)
    for _ in range(steps):
        n = round(current)
        if n % 2 != 0:
            n += 1
        values.append(n)
        current *= factor

    return list(dict.fromkeys(values))


def save_dimacs(graph: nx.Graph, path: Path, comment: str = "") -> None:
    """Save a simple graph in DIMACS .col format with vertices 1..n."""
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
    parser = argparse.ArgumentParser(
        description="Generate random cubic graphs in DIMACS .col format"
    )
    parser.add_argument(
        "--n", "--vertices",
        type=int,
        nargs="+",
        default=None,
        help="Explicit vertex counts; overrides --doubling",
    )
    parser.add_argument(
        "--doubling",
        type=float,
        nargs=3,
        default=[200, 1.15, 30],
        metavar=("START", "FACTOR", "STEPS"),
        help="Geometric progression (default: 200 1.15 30)",
    )
    parser.add_argument(
        "--reps", "--count",
        type=int,
        default=1,
        help="Repetitions per size (default: 1)",
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
        default=Path("random_cubic_graphs"),
        help="Output directory (default: random_cubic_graphs)",
    )
    parser.add_argument("--remove-isolated", action="store_true",
                        help="Discard entire graphs containing an isolated vertex")
    return parser.parse_args()


def get_vertex_counts(args: argparse.Namespace) -> list[int]:
    """Get and validate the requested sizes."""
    if args.n is not None:
        vertex_counts = args.n
    else:
        start, factor, steps_float = args.doubling
        if not all(math.isfinite(value) for value in args.doubling):
            raise ValueError("Geometric progression parameters must be finite")
        if not start.is_integer() or not steps_float.is_integer():
            raise ValueError("START and STEPS for --doubling must be integers")
        if factor <= 0:
            raise ValueError("FACTOR for --doubling must be positive")
        if steps_float < 1:
            raise ValueError("STEPS for --doubling must be at least 1")
        vertex_counts = generate_geometric_progression(
            int(start), factor, int(steps_float)
        )
        print(f"Generated geometric progression: {vertex_counts}")

    for n in vertex_counts:
        if n <= DEGREE:
            raise ValueError(f"n must be greater than {DEGREE} (received n={n})")
        if n % 2 != 0:
            raise ValueError(
                f"no 3-regular graph exists with n={n}: n must be even"
            )
    return vertex_counts


def main() -> None:
    args = parse_args()
    if args.reps < 1:
        raise ValueError("reps must be at least 1")
    vertex_counts = get_vertex_counts(args)

    output_directory = args.out
    output_directory.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in vertex_counts:
        for repetition in range(args.reps):
            graph = nx.random_regular_graph(DEGREE, n, seed=seed)
            if any(degree != DEGREE for _, degree in graph.degree()):
                raise RuntimeError("NetworkX generated a graph that is not 3-regular")

            name = f"cubic_n{n}_{repetition}.col"
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
                    f"Cubic (3-regular) n={n} "
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
