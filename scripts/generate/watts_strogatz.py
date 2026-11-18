#!/usr/bin/env python3
"""Generate graphs for Watts-Strogatz in DIMACS .col format.

Constructs small-world graphs via ``networkx.watts_strogatz_graph`` from an
initial ``--k``-nearest-neighbor ring lattice with rewiring probability
``--p-rewire``, writing files named ``ws_n<n>_k<k>_p<p_rewire>_<rep>.col``.

Notes:
    * Requires ``1 <= k < n`` and ``0 <= p-rewire <= 1``. Odd ``k`` values emit
      a warning because NetworkX rounds odd ``k`` down to ``k - 1`` (making
      ``k = 1`` edgeless).
    * With ``--remove-isolated``, graphs left with any degree-0 vertex after
      rewiring are discarded (and any pre-existing matching file is unlinked)
      without replacement, while ``--seed`` still advances by 1.
    * Does not require ``PROJECT_ROOT``; default output directory is
      ``random_watts_strogatz/`` when invoked directly.
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
    """Parse command-line options for the Watts-Strogatz generator.

    Returns:
        Parsed CLI namespace.
    """
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
        default=None,
        help="Initial seed for reproducibility (default: random)",
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

    Emits a warning if any ``k`` is odd (NetworkX rounds odd ``k`` down to
    ``k - 1``).

    Args:
        args: Parsed CLI namespace.
        vertex_counts: Resolved list of vertex counts.

    Raises:
        ValueError: If ``reps < 1``, any ``n < 1``, any ``k`` fails
            ``1 <= k < n``, or any ``p_rewire`` is outside ``[0, 1]``.
    """
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
    """Generate Watts-Strogatz graphs and save them as DIMACS ``.col`` files.

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
        for k in args.k:
            for p_rewire in args.p_rewire:
                for repetition in range(args.reps):
                    attempted += 1
                    graph = nx.watts_strogatz_graph(n, k, p_rewire, seed=seed)
                    name = f"ws_n{n}_k{k}_p{p_rewire}_{repetition}.col"
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
                            f"Watts-Strogatz n={n} k={k} "
                            f"p_rewire={p_rewire} repetition={repetition} seed={seed}"
                        ),
                    )
                    generated_graphs.append(
                        {
                            "file": name,
                            "n_vertices": graph.number_of_nodes(),
                            "n_edges": graph.number_of_edges(),
                            "k": k,
                            "p_rewire": p_rewire,
                            "repetition": repetition,
                            "seed": seed,
                        }
                    )
                    print(
                        f"OK: {name}: {graph.number_of_nodes()} vertices, "
                        f"{graph.number_of_edges()} edges"
                    )
                    total += 1
                    seed += 1

    metadata_path = update_metadata(
        resolve_metadata_path(output_directory),
        "generation",
        {
            **base_provenance(("networkx",)),
            "model": "watts-strogatz",
            "output_directory": str(output_directory),
            "initial_seed": initial_seed,
            "seed_source": seed_source,
            "parameters": {
                "vertex_counts": vertex_counts,
                "doubling": None if args.n is not None else list(args.doubling),
                "k": list(args.k),
                "p_rewire": list(args.p_rewire),
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
