#!/usr/bin/env python3
"""Select representative graphs with memory isolation and resumable processing.

Extracts 16 structural topological features from DIMACS ``.col`` graphs inside
memory- and time-limited forked worker subprocesses, persists measurements and
failure records in a resumable SQLite database, clusters standardized feature
vectors using K-Means, synchronizes the selected ``.col`` files into ``--out``,
and optionally exports a CSV report.

Pipeline:
    1. Discover top-level ``*.col`` files in ``--input-dir`` and optionally
       skip edgeless graphs (``--skip-edgeless``).
    2. Sort pending/modified/previously-failed files by ascending declared
       vertex count and compute features in disposable ``fork`` workers capped
       by ``RLIMIT_AS`` (``--memory-limit-mb``) and wall-clock ``--timeout-s``.
    3. Persist each result immediately to SQLite (``features``, ``failures``,
       and ``sources`` tables in WAL mode) using metadata fingerprints
       ``(mtime_ns, size, bfs_samples, seed, feature_version=2)``.
    4. Filter cached rows to currently present eligible inputs, standardize to
       zero mean / unit variance, run K-Means, pick the graph closest to each
       cluster centroid (with farthest-point fallback for empty clusters),
       replace stale ``*.col`` files in ``--out``, and optionally save CSV.

Platform & Environment:
    * Requires Unix ``fork`` multiprocessing and ``resource.RLIMIT_AS``.
    * Requires ``PROJECT_ROOT`` unless ``--db`` is supplied explicitly.
"""

from __future__ import annotations

import argparse
import csv
from contextlib import closing
import math
import multiprocessing as mp
import os
import random
import resource
import shutil
import sqlite3
import warnings
from pathlib import Path

import networkx as nx
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


# Ordered list of the 16 structural topological features stored in SQLite,
# clustered by K-Means, and exported to CSV.
FEATURES = [
    "n_vertices",
    "n_edges",
    "density",
    "minimum_degree",
    "maximum_degree",
    "mean_degree",
    "median_degree",
    "degree_std",
    "degree_entropy",
    "mean_clustering",
    "transitivity",
    "assortativity",
    "component_count",
    "largest_component_fraction",
    "approximate_diameter",
    "approximate_average_path_length",
]

# Subset of FEATURES formatted as exact integers (rather than 6-decimal floats) in CSV output.
INTEGER_FEATURES = {
    "n_vertices",
    "n_edges",
    "minimum_degree",
    "maximum_degree",
    "component_count",
}


def format_feature_for_csv(feature: str, value: float) -> int | str:
    """Format a feature value for CSV export.

    Integer count features are emitted as ``int``; continuous features are
    formatted to six decimal places.

    Args:
        feature: Feature name from :data:`FEATURES`.
        value: Numeric feature measurement.

    Returns:
        Integer for count features or six-decimal string for continuous features.
    """
    if feature in INTEGER_FEATURES:
        return int(value)
    return f"{float(value):.6f}"


def read_dimacs_header(file_path: Path) -> tuple[int, int] | None:
    """Read ``(n, m)`` from the first ``p`` header line without loading edges.

    Args:
        file_path: Path to a DIMACS ``.col`` file.

    Returns:
        Tuple ``(n_vertices, n_edges)`` if a valid ``p`` line is found;
        otherwise ``None``.
    """
    with file_path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            if line.startswith("p "):
                parts = line.split()
                if len(parts) < 4:
                    return None
                try:
                    return int(parts[2]), int(parts[3])
                except ValueError:
                    return None
    return None


def read_dimacs_col(file_path: Path) -> nx.Graph:
    """Strictly parse a simple undirected graph from a DIMACS ``.col`` file.

    Pre-populates vertices ``1..n`` from the header so isolated vertices are
    preserved, and rejects missing/repeated headers, negative sizes, out-of-
    range endpoints, self-loops, duplicate undirected edges, unknown records,
    and edge-count mismatches.

    Args:
        file_path: Path to the DIMACS ``.col`` file.

    Returns:
        Parsed simple undirected ``nx.Graph`` with integer vertex IDs ``1..n``.

    Raises:
        ValueError: If the file violates the strict simple DIMACS ``.col`` format.
    """
    graph = nx.Graph()
    vertex_count: int | None = None
    edge_count = 0
    with file_path.open("r", encoding="utf-8", errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text or text.split()[0] == "c":
                continue
            parts = text.split()
            try:
                if parts[0] == "p":
                    if vertex_count is not None or len(parts) != 4 or parts[1] != "edge":
                        raise ValueError("expected one 'p edge n m' header")
                    vertex_count, edge_count = map(int, parts[2:])
                    if vertex_count < 0 or edge_count < 0:
                        raise ValueError("negative graph size")
                    graph.add_nodes_from(range(1, vertex_count + 1))
                elif parts[0] == "e":
                    if vertex_count is None or len(parts) != 3:
                        raise ValueError("edge before header or malformed edge")
                    u, v = map(int, parts[1:])
                    if not (1 <= u <= vertex_count and 1 <= v <= vertex_count):
                        raise ValueError("vertex ID outside 1..n")
                    if u == v or graph.has_edge(u, v):
                        raise ValueError("loop or duplicate undirected edge")
                    graph.add_edge(u, v)
                else:
                    raise ValueError("unknown DIMACS record")
            except (IndexError, ValueError) as error:
                raise ValueError(
                    f"invalid DIMACS line at {file_path}:{line_number}: {text}"
                ) from error
    if vertex_count is None or graph.number_of_edges() != edge_count:
        raise ValueError(f"{file_path}: missing header or edge count mismatch")
    return graph


def approximate_diameter_and_path_length(
    graph: nx.Graph, sample_count: int, seed: int
) -> tuple[float, float]:
    """Estimate diameter and average path length via sampled BFS.

    Uses reservoir sampling with ``seed`` to select up to ``sample_count``
    source vertices from the largest connected component, runs single-source
    BFS from each source, and averages shortest-path lengths to all *other*
    vertices (excluding self-distance 0).

    Args:
        graph: Simple undirected graph.
        sample_count: Maximum number of BFS sources to sample.
        seed: Random seed controlling reservoir sampling.

    Returns:
        Tuple ``(approximate_diameter, approximate_average_path_length)``, or
        ``(0.0, 0.0)`` for an empty graph.
    """
    if graph.number_of_nodes() == 0:
        return 0.0, 0.0

    largest_component = max(nx.connected_components(graph), key=len)
    subgraph = graph.subgraph(largest_component)
    rng = random.Random(seed)
    # Algorithm R reservoir sampling over the largest connected component so we
    # never materialize an extra list of all vertices in memory.
    samples: list[int] = []
    for index, vertex in enumerate(subgraph):
        if index < sample_count:
            samples.append(vertex)
            continue
        replacement = rng.randrange(index + 1)
        if replacement < sample_count:
            samples[replacement] = vertex

    eccentricities: list[int] = []
    average_path_lengths: list[float] = []
    for source in samples:
        maximum_distance = 0
        distance_sum = 0
        count = 0
        for distance in nx.single_source_shortest_path_length(subgraph, source).values():
            maximum_distance = max(maximum_distance, distance)
            distance_sum += distance
            count += 1
        eccentricities.append(maximum_distance)
        # Divide by (count - 1) to exclude the trivial distance 0 from source to itself.
        average_path_lengths.append(distance_sum / (count - 1) if count > 1 else 0.0)

    return float(max(eccentricities)), float(np.mean(average_path_lengths))


def degree_entropy(degrees: list[int]) -> float:
    """Compute base-2 Shannon entropy of a vertex degree sequence.

    Args:
        degrees: List of vertex degrees.

    Returns:
        Shannon entropy in bits, or ``0.0`` when ``degrees`` is empty.
    """
    if not degrees:
        return 0.0
    _, counts = np.unique(degrees, return_counts=True)
    probabilities = counts / counts.sum()
    return float(-np.sum(probabilities * np.log2(probabilities)))


def extract_features(graph: nx.Graph, bfs_samples: int, seed: int) -> dict[str, float]:
    """Compute all 16 structural topological features for a graph.

    Args:
        graph: Simple undirected graph.
        bfs_samples: Number of BFS source vertices for distance estimates.
        seed: Random seed passed to :func:`approximate_diameter_and_path_length`.

    Returns:
        Dictionary mapping every name in :data:`FEATURES` to its numeric value.
    """
    n = graph.number_of_nodes()
    m = graph.number_of_edges()
    degrees = [degree for _, degree in graph.degree()]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            assortativity = nx.degree_assortativity_coefficient(graph)
        if assortativity is None or math.isnan(assortativity):
            assortativity = 0.0
    except Exception:
        assortativity = 0.0

    component_count = 0
    largest_component_size = 0
    for component in nx.connected_components(graph):
        component_count += 1
        largest_component_size = max(largest_component_size, len(component))
    diameter, average_path_length = approximate_diameter_and_path_length(
        graph, bfs_samples, seed
    )
    return {
        "n_vertices": n,
        "n_edges": m,
        "density": nx.density(graph),
        "minimum_degree": min(degrees, default=0),
        "maximum_degree": max(degrees, default=0),
        "mean_degree": float(np.mean(degrees)) if degrees else 0.0,
        "median_degree": float(np.median(degrees)) if degrees else 0.0,
        "degree_std": float(np.std(degrees)) if degrees else 0.0,
        "degree_entropy": degree_entropy(degrees),
        "mean_clustering": nx.average_clustering(graph) if n else 0.0,
        "transitivity": nx.transitivity(graph),
        "assortativity": float(assortativity),
        "component_count": component_count,
        "largest_component_fraction": largest_component_size / n if n else 0.0,
        "approximate_diameter": diameter,
        "approximate_average_path_length": average_path_length,
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line options for representative graph selection.

    Returns:
        Parsed CLI namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Select representative DIMACS graphs by clustering "
            "structural features, with memory isolation and resumable processing"
        )
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--n", "--count", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bfs-samples", type=int, default=10)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--db-name", help="Dataset name within datasets/")
    parser.add_argument("--db", type=Path, help="Explicit SQLite database path")
    parser.add_argument(
        "--max-vertices",
        type=int,
        default=500_000,
        help="Skip graphs above this vertex count; use 0 to disable",
    )
    parser.add_argument(
        "--memory-limit-mb",
        dest="memory_limit_mb",
        type=int,
        default=4096,
        help="Memory limit per subprocess in MB; default: 4096",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=21_600,
        help="Time limit per graph; default: 21600 seconds (6 hours)",
    )
    parser.add_argument(
        "--skip-edgeless",
        action="store_true",
        help=(
            "Skip graphs with zero vertices, zero edges, or only "
            "isolated vertices"
        ),
    )
    parser.add_argument(
        "--use-all-if-fewer",
        action="store_true",
        help="Select all available graphs when the dataset contains fewer than --n",
    )
    parser.add_argument(
        "--only-select",
        dest="only_select",
        action="store_true",
        help="Use stored database features without processing new graphs",
    )
    return parser.parse_args()


def resolve_database_path(args: argparse.Namespace) -> Path:
    """Resolve and prepare the parent directory of the SQLite feature cache.

    Uses ``args.db`` when explicitly provided; otherwise resolves
    ``$PROJECT_ROOT/datasets/<dataset_name>/features.sqlite3`` where
    ``dataset_name`` defaults to ``args.db_name`` or ``args.input_dir.parent.name``.

    Args:
        args: Parsed CLI namespace.

    Returns:
        Path to the persistent SQLite database file.

    Raises:
        RuntimeError: If ``--db`` is omitted and ``PROJECT_ROOT`` is unset.
    """
    if args.db is not None:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        return args.db

    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set; run through mise or pass --db"
        )
    dataset_name = args.db_name or args.input_dir.parent.name
    destination = Path(project_root) / "datasets" / dataset_name
    destination.mkdir(parents=True, exist_ok=True)
    return destination / "features.sqlite3"


def has_vertices_and_edges(file_path: Path) -> bool:
    """Check whether a DIMACS header declares at least one vertex and one edge.

    Args:
        file_path: Path to a DIMACS ``.col`` file.

    Returns:
        ``True`` if the header declares ``n > 0`` and ``m > 0``; ``False`` otherwise.
    """
    header = read_dimacs_header(file_path)
    return bool(header and header[0] > 0 and header[1] > 0)


def save_csv(
    file_path: Path,
    names: list[str],
    matrix: np.ndarray,
    clusters: np.ndarray,
    selected: list[int],
) -> None:
    """Export features, cluster assignments, and selection flags to CSV.

    Args:
        file_path: Destination CSV path (parent directories created as needed).
        names: Ordered list of graph filenames matching rows of ``matrix``.
        matrix: 2D ``float64`` feature array of shape ``(len(names), len(FEATURES))``.
        clusters: 1D array of K-Means cluster indices for each graph.
        selected: Indices of graphs chosen as cluster representatives.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=["file", "cluster", "selected", *FEATURES]
        )
        writer.writeheader()
        for index, name in enumerate(names):
            writer.writerow(
                {
                    "file": name,
                    "cluster": int(clusters[index]),
                    "selected": index in selected,
                    **{
                        feature: format_feature_for_csv(
                            feature, matrix[index, column]
                        )
                        for column, feature in enumerate(FEATURES)
                    },
                }
            )


def _worker(
    file_path: str,
    bfs_samples: int,
    seed: int,
    limit_bytes: int,
    sender,
) -> None:
    """Execute feature extraction in a memory-limited child process.

    Applies ``resource.RLIMIT_AS`` when ``limit_bytes > 0`` (ignoring platform
    errors), parses ``file_path``, computes features, and sends
    ``(True, feature_dict)`` or ``(False, error_message)`` through ``sender``.

    Args:
        file_path: String path to the DIMACS ``.col`` file.
        bfs_samples: Number of BFS samples for distance estimation.
        seed: Random seed for BFS sampling.
        limit_bytes: Virtual address space cap in bytes (``0`` disables).
        sender: Write end of a multiprocessing ``Pipe``.
    """
    try:
        if limit_bytes > 0:
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ValueError, OSError):
        pass

    try:
        graph = read_dimacs_col(Path(file_path))
        sender.send((True, extract_features(graph, bfs_samples, seed)))
    except MemoryError:
        sender.send((False, "insufficient memory"))
    except Exception as error:
        sender.send((False, f"{type(error).__name__}: {error}"))
    finally:
        sender.close()


def compute_features_in_subprocess(
    file_path: Path,
    bfs_samples: int,
    seed: int,
    limit_bytes: int,
    timeout_s: float,
) -> tuple[bool, dict | str]:
    """Run :func:`_worker` in a forked subprocess with timeout and crash recovery.

    Terminates (and after 5 seconds forcefully kills) workers exceeding
    ``timeout_s`` and translates OOM kills or pipe ``EOFError`` into a structured
    failure tuple.

    Args:
        file_path: Path to the DIMACS ``.col`` file.
        bfs_samples: Number of BFS source vertices to sample.
        seed: Random seed for BFS sampling.
        limit_bytes: Virtual memory limit in bytes (``0`` disables).
        timeout_s: Wall-clock timeout in seconds (``0`` disables).

    Returns:
        ``(True, features_dict)`` on success, or ``(False, reason_str)`` on
        failure, timeout, or worker crash.
    """
    context = mp.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker,
        args=(str(file_path), bfs_samples, seed, limit_bytes, sender),
    )
    process.start()
    sender.close()
    process.join(timeout_s if timeout_s > 0 else None)

    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        receiver.close()
        return False, f"timeout after {timeout_s:.0f}s"

    if receiver.poll(1):
        try:
            return receiver.recv()
        except EOFError:
            pass
        finally:
            receiver.close()
    receiver.close()
    return (
        False,
        f"process exited without a result (exitcode={process.exitcode}; probable OOM)",
    )


def prepare_database(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite feature cache, migrate legacy columns, and ensure schema.

    Configures ``WAL`` journal mode and ``synchronous=NORMAL``, renames any
    legacy Portuguese column names in ``features`` and ``failures`` to English,
    and creates the ``features``, ``failures``, and ``sources`` tables if absent.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Open ``sqlite3.Connection`` ready for reads and writes.
    """
    columns = ", ".join(f'"{feature}" REAL NOT NULL' for feature in FEATURES)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    # Upgrade databases created before the English naming convention.
    legacy_columns = {
        "arquivo": "file",
        "motivo": "reason",
        "n_arestas": "n_edges",
        "densidade": "density",
        "grau_minimo": "minimum_degree",
        "grau_maximo": "maximum_degree",
        "grau_medio": "mean_degree",
        "grau_mediana": "median_degree",
        "grau_desvio": "degree_std",
        "entropia_grau": "degree_entropy",
        "clustering_medio": "mean_clustering",
        "transitividade": "transitivity",
        "assortatividade": "assortativity",
        "num_componentes": "component_count",
        "frac_maior_componente": "largest_component_fraction",
        "diametro_aprox": "approximate_diameter",
        "caminho_medio_aprox": "approximate_average_path_length",
    }
    with connection:
        for table in ("features", "failures"):
            existing_columns = {
                row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            for old, new in legacy_columns.items():
                if old in existing_columns:
                    connection.execute(
                        f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"'
                    )
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS features "
        f"(file TEXT PRIMARY KEY, {columns})"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS failures "
        "(file TEXT PRIMARY KEY, reason TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS sources "
        "(file TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, "
        "bfs_samples INTEGER, seed INTEGER, feature_version INTEGER)"
    )
    connection.commit()
    return connection


def sort_by_size(files: list[Path]) -> list[Path]:
    """Sort ``.col`` paths in ascending order of declared header vertex count.

    Files without a parseable header default to vertex count ``0``.

    Args:
        files: List of DIMACS ``.col`` file paths.

    Returns:
        New list sorted from smallest to largest declared vertex count.
    """
    return sorted(
        files,
        key=lambda file_path: (read_dimacs_header(file_path) or (0, 0))[0],
    )


def compute_and_store_features(
    files: list[Path],
    bfs_samples: int,
    seed: int,
    db_path: Path,
    max_vertices: int,
    memory_limit_mb: int,
    timeout_s: float,
) -> None:
    """Extract and persist features for pending, modified, or previously failed files.

    Reuses cached entries only when ``(mtime_ns, size, bfs_samples, seed, 2)``
    matches the ``sources`` table joined with ``features``. Processes pending
    graphs in ascending size order, committing each success or failure
    immediately so interrupted runs can resume.

    Args:
        files: Candidate ``.col`` files to process.
        bfs_samples: Number of BFS samples per graph.
        seed: Random seed for BFS sampling.
        db_path: Path to the persistent SQLite cache.
        max_vertices: Maximum allowed declared vertex count (``0`` disables).
        memory_limit_mb: Per-worker virtual memory cap in MiB (``0`` disables).
        timeout_s: Per-worker timeout in seconds (``0`` disables).
    """
    connection = prepare_database(db_path)
    cached_sources = {
        record[0]: tuple(record[1:])
        for record in connection.execute(
            "SELECT sources.* FROM sources JOIN features USING (file)"
        )
    }
    fingerprints = {}
    for path in files:
        stat = path.stat()
        fingerprints[path.name] = (stat.st_mtime_ns, stat.st_size, bfs_samples, seed, 2)
    pending = [
        file_path for file_path in sort_by_size(files)
        if cached_sources.get(file_path.name) != fingerprints[file_path.name]
    ]
    print(
        f"Persistent database: {db_path} | cached: {len(files) - len(pending)} | "
        f"pending: {len(pending)}"
    )

    placeholders = ", ".join("?" for _ in range(1 + len(FEATURES)))
    limit_bytes = memory_limit_mb * 1024 * 1024
    for index, file_path in enumerate(pending, start=1):
        connection.execute("DELETE FROM features WHERE file = ?", (file_path.name,))
        connection.execute("DELETE FROM failures WHERE file = ?", (file_path.name,))
        connection.execute("DELETE FROM sources WHERE file = ?", (file_path.name,))
        header = read_dimacs_header(file_path)
        if max_vertices > 0 and header and header[0] > max_vertices:
            reason = f"n={header[0]} exceeds max-vertices={max_vertices}"
            connection.execute(
                "INSERT OR REPLACE INTO failures VALUES (?, ?)",
                (file_path.name, reason),
            )
            connection.commit()
            print(f"[{index}/{len(pending)}] {file_path.name}: SKIPPED ({reason})")
            continue

        success, result = compute_features_in_subprocess(
            file_path, bfs_samples, seed, limit_bytes, timeout_s
        )
        if success:
            connection.execute(
                f"INSERT OR REPLACE INTO features VALUES ({placeholders})",
                (file_path.name, *(result[feature] for feature in FEATURES)),
            )
            connection.execute(
                "INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                (file_path.name, *fingerprints[file_path.name]),
            )
            connection.commit()
            print(
                f"[{index}/{len(pending)}] {file_path.name}: "
                f"n={result['n_vertices']}, m={result['n_edges']}, "
                f"density={result['density']:.6f} [OK]"
            )
        else:
            connection.execute(
                "INSERT OR REPLACE INTO failures VALUES (?, ?)",
                (file_path.name, str(result)),
            )
            connection.commit()
            print(f"[{index}/{len(pending)}] {file_path.name}: FAILED ({result})")
    connection.close()


def load_features(
    database_path: Path,
) -> tuple[list[str], np.ndarray, list[tuple[str, str]]]:
    """Load cached feature vectors and failure summaries from SQLite.

    Args:
        database_path: Path to the SQLite feature database.

    Returns:
        Tuple ``(names, matrix, failures)`` containing sorted filenames, a 2D
        ``float64`` feature array matching :data:`FEATURES`, and ``(file, reason)``
        failure pairs.
    """
    columns = ", ".join(f'"{feature}"' for feature in FEATURES)
    with closing(prepare_database(database_path)) as connection:
        records = connection.execute(
            f"SELECT file, {columns} FROM features ORDER BY file"
        ).fetchall()
        failures = connection.execute(
            "SELECT file, reason FROM failures ORDER BY file"
        ).fetchall()
    names = [record[0] for record in records]
    matrix = np.asarray([record[1:] for record in records], dtype=np.float64)
    return names, matrix, failures


def main() -> None:
    """Run feature extraction, K-Means representative selection, and file export.

    Validates CLI parameters, optionally updates the SQLite feature cache,
    filters cached rows to currently present eligible inputs, clusters
    standardized feature vectors with K-Means (with farthest-point fallback for
    empty clusters), synchronizes ``*.col`` files in ``--out``, and optionally
    writes a CSV report.

    Raises:
        ValueError: If CLI options are out of range, ``--input-dir`` equals
            ``--out``, or fewer than ``--n`` eligible graphs exist without
            ``--use-all-if-fewer``.
        FileNotFoundError: If no ``.col`` files exist in ``--input-dir`` or the
            database is missing under ``--only-select``.
        RuntimeError: If ``PROJECT_ROOT`` is needed but unset, or zero eligible
            graphs were processed successfully.
    """
    args = parse_args()
    if args.n < 1:
        raise ValueError("n must be at least 1")
    if args.bfs_samples < 1:
        raise ValueError("bfs-samples must be at least 1")
    if args.max_vertices < 0:
        raise ValueError("max-vertices must be at least 0")
    if args.memory_limit_mb < 0:
        raise ValueError("memory-limit-mb must be at least 0")
    if not math.isfinite(args.timeout_s) or args.timeout_s < 0:
        raise ValueError("timeout-s must be finite and at least 0")
    if args.input_dir.resolve() == args.out.resolve():
        raise ValueError("input-dir and out must be different directories")

    database_path = resolve_database_path(args)
    if not args.only_select:
        files = sorted(args.input_dir.glob("*.col"))
        if args.skip_edgeless:
            invalid = [
                file_path for file_path in files
                if not has_vertices_and_edges(file_path)
            ]
            files = [file_path for file_path in files if file_path not in invalid]
            for file_path in invalid:
                print(f"IGNORED [no edges]: {file_path}")
        if not files:
            raise FileNotFoundError(
                f"no .col files found in {args.input_dir}"
            )
        compute_and_store_features(
            files,
            args.bfs_samples,
            args.seed,
            database_path,
            args.max_vertices,
            args.memory_limit_mb,
            args.timeout_s,
        )
    elif not database_path.exists():
        raise FileNotFoundError(f"feature database not found: {database_path}")

    names, matrix, failures = load_features(database_path)
    # A cleanup may have removed files since their features were cached.
    available = [
        index for index, name in enumerate(names)
        if (args.input_dir / name).is_file()
        and (not args.skip_edgeless or has_vertices_and_edges(args.input_dir / name))
        and (args.max_vertices == 0 or matrix[index, 0] <= args.max_vertices)
    ]
    names = [names[index] for index in available]
    matrix = matrix[available]
    if failures:
        print(f"\n{len(failures)} file(s) skipped or failed:")
        for name, reason in failures:
            print(f"  {name}: {reason}")
    if not names:
        raise RuntimeError("no graph was processed successfully")

    target_count = args.n
    if target_count > len(names):
        if not args.use_all_if_fewer:
            raise ValueError(
                f"requested {target_count} graphs, but only "
                f"{len(names)} were processed successfully"
            )
        print(
            f"WARNING: requested {target_count}, available {len(names)}; "
            "selecting all"
        )
        target_count = len(names)

    standardized_matrix = StandardScaler().fit_transform(matrix)
    kmeans = KMeans(n_clusters=target_count, random_state=args.seed, n_init=10)
    clusters = kmeans.fit_predict(standardized_matrix)

    selected: list[int] = []
    for cluster in range(target_count):
        indices = np.where(clusters == cluster)[0]
        if not len(indices):
            continue
        points = standardized_matrix[indices]
        distances = np.linalg.norm(points - kmeans.cluster_centers_[cluster], axis=1)
        selected.append(int(indices[np.argmin(distances)]))

    if len(selected) < target_count:
        # Empty K-Means clusters can leave fewer representatives than requested;
        # fill remaining slots with points farthest from the chosen set.
        remaining = [
            index for index in range(len(names)) if index not in selected
        ]
        chosen = standardized_matrix[selected]
        distances = [
            np.min(np.linalg.norm(chosen - standardized_matrix[index], axis=1))
            for index in remaining
        ]
        order = np.argsort(distances)[::-1]
        selected.extend(
            remaining[index] for index in order[: target_count - len(selected)]
        )

    args.out.mkdir(parents=True, exist_ok=True)
    selected_names = {names[index] for index in selected}
    for existing in args.out.glob("*.col"):
        if existing.name not in selected_names:
            existing.unlink()

    print(f"Selected {len(selected)} graphs:")
    for index in selected:
        name = names[index]
        values = {
            feature: matrix[index, column]
            for column, feature in enumerate(FEATURES)
        }
        shutil.copy2(args.input_dir / name, args.out / name)
        print(
            f"  {name}: n={int(values['n_vertices'])}, "
            f"m={int(values['n_edges'])}, "
            f"density={values['density']:.6f}, "
            f"mean degree={values['mean_degree']:.6f}"
        )

    if args.csv:
        save_csv(args.csv, names, matrix, clusters, selected)
        print(f"Features saved to {args.csv}")
    print(f"Graphs copied to {args.out}")


if __name__ == "__main__":
    main()
