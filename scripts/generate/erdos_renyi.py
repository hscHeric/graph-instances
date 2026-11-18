#!/usr/bin/env python3
"""Generate graphs for Erdős-Rényi G(n, p) in DIMACS .col format."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import networkx as nx


def generate_geometric_progression(
    start: int, factor: float, steps: int
) -> list[int]:
    """Generate a geometric progression of sizes without duplicates."""
    values = [round(start * factor**step) for step in range(steps)]
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
        description="Generate graphs for Erdős-Rényi G(n, p) in DIMACS .col format"
    )
    parser.add_argument(
        "--n", "--vertices",
        type=int,
        nargs="+",
        default=None,
        help="Vertex counts; overrides --doubling",
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
        "--avg-degree",
        type=float,
        nargs="+",
        default=[10.0],
        help="Target average degree used to compute p = degree/(n-1)",
    )
    parser.add_argument(
        "--p",
        type=float,
        nargs="+",
        default=None,
        help="Fixed probabilities; overrides --avg-degree when provided",
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
        default=Path("random_erdos_renyi"),
        help="Output directory (default: random_erdos_renyi)",
    )
    parser.add_argument("--remove-isolated", action="store_true",
                        help="Discard entire graphs containing an isolated vertex")
    return parser.parse_args()


def get_vertex_counts(args: argparse.Namespace) -> list[int]:
    if args.n is not None:
        vertex_counts = args.n
    else:
        start, factor, steps_float = args.doubling
        if not all(math.isfinite(value) for value in args.doubling):
            raise ValueError("Geometric progression parameters must be finite")
        if not start.is_integer() or not steps_float.is_integer():
            raise ValueError("START and STEPS for --doubling must be integers")
        if start < 1 or factor <= 0 or steps_float < 1:
            raise ValueError("START and STEPS must be at least 1; FACTOR must be positive")
        vertex_counts = generate_geometric_progression(
            int(start), factor, int(steps_float)
        )
        print(f"Generated geometric progression ({len(vertex_counts)} sizes): {vertex_counts}")
    return vertex_counts


def validate_arguments(args: argparse.Namespace, vertex_counts: list[int]) -> None:
    """Validate parameters before generating any files."""
    if args.reps < 1:
        raise ValueError("reps must be at least 1")

    for n in vertex_counts:
        if n < 2:
            raise ValueError(f"n must be greater than 1 (received n={n})")

    if args.p is not None:
        for probability in args.p:
            if not 0 < probability <= 1:
                raise ValueError(
                    f"p must be in the range (0, 1] (received {probability})"
                )
    for degree in args.avg_degree:
        if not math.isfinite(degree) or degree <= 0:
            raise ValueError("avg-degree must be finite and positive")


def main() -> None:
    args = parse_args()
    vertex_counts = get_vertex_counts(args)
    validate_arguments(args, vertex_counts)

    output_directory = args.out
    output_directory.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in vertex_counts:
        if args.p is not None:
            densities = [(f"p{p}", p, f"p={p}") for p in args.p]
        else:
            densities = [
                (
                    f"deg{degree}",
                    min(degree / (n - 1), 1.0),
                    f"target_average_degree={degree}",
                )
                for degree in args.avg_degree
            ]
        for tag, probability, density_comment in densities:
            for repetition in range(args.reps):
                graph = nx.erdos_renyi_graph(n, probability, seed=seed)
                name = f"er_n{n}_{tag}_{repetition}.col"
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
                        f"Erdős-Rényi n={n} {density_comment} "
                        f"calculated_p={probability:.8f} "
                        f"repetition={repetition} seed={seed}"
                    ),
                )
                print(
                    f"OK: {name}: {graph.number_of_nodes()} vertices, "
                    f"{graph.number_of_edges()} edges, "
                    f"actual average degree={2 * graph.number_of_edges() / n:.2f}"
                )
                total += 1
                seed += 1

    print(f"Completed: {total} graphs generated in {output_directory}")


if __name__ == "__main__":
    main()
