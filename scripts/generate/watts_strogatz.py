#!/usr/bin/env python3
"""Generate graphs for Watts-Strogatz in DIMACS .col format."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import networkx as nx


def generate_geometric_progression(start: int, factor: float, steps: int) -> list[int]:
    """Generate a geometric progression of sizes without duplicates."""
    return list(dict.fromkeys(round(start * factor**step) for step in range(steps)))


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
        description="Generate graphs for Watts-Strogatz in DIMACS .col format"
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
        "--k",
        type=int,
        nargs="+",
        default=[6],
        help="Neighbors per vertex in the initial ring (default: 6)",
    )
    parser.add_argument(
        "--p-rewire",
        type=float,
        nargs="+",
        default=[0.1],
        help="Rewiring probabilities (default: 0.1)",
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
        default=Path("random_watts_strogatz"),
        help="Output directory (default: random_watts_strogatz)",
    )
    parser.add_argument("--remove-isolated", action="store_true",
                        help="Discard entire graphs containing an isolated vertex")
    return parser.parse_args()


def get_vertex_counts(args: argparse.Namespace) -> list[int]:
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
    """Validate parameters before generating any files."""
    if args.reps < 1:
        raise ValueError("reps must be at least 1")

    for n in vertex_counts:
        if n < 1:
            raise ValueError(f"n must be positive (received n={n})")
        for k in args.k:
            if k < 1 or k >= n:
                raise ValueError(
                    f"k must be positive and less than n (received n={n}, k={k})"
                )
            if k % 2 != 0:
                print(f"WARNING: k={k} is odd; prefer even values.")

    for probability in args.p_rewire:
        if not 0 <= probability <= 1:
            raise ValueError(
                "p-rewire must be between 0 and 1 "
                f"(received {probability})"
            )


def main() -> None:
    args = parse_args()
    vertex_counts = get_vertex_counts(args)
    validate_arguments(args, vertex_counts)

    output_directory = args.out
    output_directory.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in vertex_counts:
        for k in args.k:
            for p_rewire in args.p_rewire:
                for repetition in range(args.reps):
                    graph = nx.watts_strogatz_graph(n, k, p_rewire, seed=seed)
                    name = f"ws_n{n}_k{k}_p{p_rewire}_{repetition}.col"
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
                            f"Watts-Strogatz n={n} k={k} "
                            f"p_rewire={p_rewire} repetition={repetition} seed={seed}"
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
