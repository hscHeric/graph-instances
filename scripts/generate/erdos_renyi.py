#!/usr/bin/env python3
"""Generate graphs for Erdős-Rényi G(n, p) in DIMACS .col format.

Constructs simple undirected random graphs via ``networkx.erdos_renyi_graph``
across explicit vertex counts (``--n``/``--vertices``) or a geometric
progression (``--doubling``). Edge probability ``p`` is derived either from
target average degrees (``p = min(avg_degree / (n - 1), 1.0)``) or from
explicit probabilities (``--p``), writing files named
``er_n<n>_deg<g>_<rep>.col`` or ``er_n<n>_p<p>_<rep>.col``.

Notes:
    * Does not require ``PROJECT_ROOT``; default output directory is
      ``random_erdos_renyi/`` when invoked directly.
    * With ``--remove-isolated``, graphs containing any degree-0 vertex are
      discarded (and any pre-existing matching file is unlinked) without being
      replaced, while ``--seed`` still advances by 1.
    * ``nx.erdos_renyi_graph`` evaluates all O(n^2) pairs in memory.
"""

from __future__ import annotations

import argparse
import math
import secrets
import sys
from pathlib import Path

import networkx as nx

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from metadata_utils import base_provenance, resolve_metadata_path, update_metadata


def generate_geometric_progression(
    start: int, factor: float, steps: int
) -> list[int]:
    """Generate a deduplicated geometric progression of rounded integer sizes.

    Args:
        start: Initial vertex count.
        factor: Positive multiplicative growth factor per step.
        steps: Number of progression steps.

    Returns:
        Ordered list of unique integer vertex counts ``round(start * factor**step)``.
    """
    values = [round(start * factor**step) for step in range(steps)]
    return list(dict.fromkeys(values))


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
    """Parse command-line options for the Erdős-Rényi generator.

    Returns:
        Parsed CLI namespace.
    """
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
        default=None,
        help="Initial seed for reproducibility (default: random)",
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
    """Return explicit vertex counts or expand and validate ``--doubling``.

    Args:
        args: Parsed CLI namespace.

    Returns:
        List of vertex counts to generate.

    Raises:
        ValueError: If ``--doubling`` parameters are non-finite, non-integer
            for ``START``/``STEPS``, or out of range.
    """
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
    """Validate CLI parameters before generating any files.

    Args:
        args: Parsed CLI namespace.
        vertex_counts: Resolved list of vertex counts.

    Raises:
        ValueError: If ``reps < 1``, any ``n < 2``, any ``p`` is outside
            ``(0, 1]``, or any ``avg_degree`` is non-finite or ``<= 0``.
    """
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
    """Generate Erdős-Rényi graphs and save them as DIMACS ``.col`` files.

    Raises:
        ValueError: If any CLI argument fails validation.
    """
    args = parse_args()
    vertex_counts = get_vertex_counts(args)
    validate_arguments(args, vertex_counts)

    output_directory = args.out
    output_directory.mkdir(parents=True, exist_ok=True)

    total = 0
    attempted = 0
    skipped_isolated = 0
    generated_graphs: list[dict[str, object]] = []
    if args.seed is None:
        initial_seed = secrets.randbelow(2**31)
        seed_source = "random"
        print(f"Using random initial seed: {initial_seed}")
    else:
        initial_seed = args.seed
        seed_source = "cli"
    seed = initial_seed
    for n in vertex_counts:
        if args.p is not None:
            densities = [(f"p{p}", p, f"p={p}", None) for p in args.p]
        else:
            densities = [
                (
                    f"deg{degree}",
                    min(degree / (n - 1), 1.0),
                    f"target_average_degree={degree}",
                    degree,
                )
                for degree in args.avg_degree
            ]
        for tag, probability, density_comment, target_degree in densities:
            for repetition in range(args.reps):
                attempted += 1
                graph = nx.erdos_renyi_graph(n, probability, seed=seed)
                name = f"er_n{n}_{tag}_{repetition}.col"
                file_path = output_directory / name
                if args.remove_isolated and any(degree == 0 for _, degree in graph.degree()):
                    file_path.unlink(missing_ok=True)
                    print(f"REMOVED/SKIPPED: {name}: isolated vertices")
                    skipped_isolated += 1
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
                entry: dict[str, object] = {
                    "file": name,
                    "n_vertices": graph.number_of_nodes(),
                    "n_edges": graph.number_of_edges(),
                    "calculated_p": round(probability, 8),
                    "repetition": repetition,
                    "seed": seed,
                }
                if target_degree is not None:
                    entry["target_avg_degree"] = target_degree
                generated_graphs.append(entry)
                print(
                    f"OK: {name}: {graph.number_of_nodes()} vertices, "
                    f"{graph.number_of_edges()} edges, "
                    f"actual average degree={2 * graph.number_of_edges() / n:.2f}"
                )
                total += 1
                seed += 1

    metadata_path = update_metadata(
        resolve_metadata_path(output_directory),
        "generation",
        {
            **base_provenance(("networkx",)),
            "model": "erdos-renyi",
            "output_directory": str(output_directory),
            "initial_seed": initial_seed,
            "seed_source": seed_source,
            "parameters": {
                "vertex_counts": vertex_counts,
                "doubling": None if args.n is not None else list(args.doubling),
                "avg_degree": None if args.p is not None else list(args.avg_degree),
                "p": None if args.p is None else list(args.p),
                "reps": args.reps,
                "remove_isolated": args.remove_isolated,
            },
            "summary": {
                "attempted": attempted,
                "generated": total,
                "skipped_isolated": skipped_isolated,
            },
            "graphs": generated_graphs,
        },
    )
    print(f"Completed: {total} graphs generated in {output_directory}")
    print(f"Metadata saved to {metadata_path}")


if __name__ == "__main__":
    main()
