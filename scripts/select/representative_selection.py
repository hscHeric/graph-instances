#!/usr/bin/env python3
"""Select representative graphs with memory isolation and resumable processing.

Extracts 16 structural topological features from DIMACS ``.col`` graphs using a
compact Compressed Sparse Row (CSR) representation in NumPy/SciPy inside
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
       * Small/medium graphs (``m <= --exact-triangle-edges``) compute exact
         ``mean_clustering`` and ``transitivity`` in a single degree-oriented
         Chiba-Nishizeki pass.
       * Larger graphs use unbiased deterministic wedge sampling (Seshadhri
         et al.) for ``transitivity`` and uniform eligible-vertex neighbor-pair
         sampling for ``mean_clustering``, avoiding quadratic hub blowup while
         keeping full-graph coverage across millions of vertices and edges.
    3. Persist each result immediately to SQLite (``features``, ``failures``,
       and ``sources`` tables in WAL mode) using metadata fingerprints
       ``(mtime_ns, size, bfs_samples, seed, feature_version=3,
       exact_triangle_edges, triangle_samples)``. The SQLite connection is
       always closed before forking worker processes.
    4. Filter cached rows to currently present eligible inputs, optionally cap
       repetitive temporal snapshot series (``--max-per-series``) via within-
       series representative selection so snapshot floods do not dominate
       clustering, apply ``log1p`` to heavy-tailed scale features (unless
       ``--no-log-scale`` is set), standardize, run K-Means with farthest-first
       empty-cluster fallback, synchronize ``*.col`` files in ``--out``, and
       optionally save CSV.

Platform & Environment:
    * Requires Unix ``fork`` multiprocessing and ``resource.RLIMIT_AS``.
    * Requires ``PROJECT_ROOT`` unless ``--db`` is supplied explicitly.
"""

from __future__ import annotations

import argparse
import csv
from contextlib import closing
from dataclasses import dataclass
import math
import multiprocessing as mp
import os
import random
import re
import resource
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Limit BLAS/OpenMP thread pools before importing NumPy/SciPy/scikit-learn so
# forked workers do not reserve excessive virtual address space under RLIMIT_AS.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np
import scipy.sparse
from scipy.sparse.csgraph import connected_components, shortest_path
from scipy.stats import ks_2samp
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from metadata_utils import (
    base_provenance,
    resolve_metadata_path,
    sha256_file,
    update_metadata,
)


# Cache schema version; increment whenever feature computation semantics change.
FEATURE_VERSION = 3

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

# Non-negative heavy-tailed scale features transformed via log1p before
# StandardScaler so multi-order-of-magnitude graphs cluster evenly.
LOG_SCALED_FEATURE_INDICES = tuple(
    index
    for index, name in enumerate(FEATURES)
    if name in {
        "n_vertices",
        "n_edges",
        "minimum_degree",
        "maximum_degree",
        "mean_degree",
        "median_degree",
        "degree_std",
        "component_count",
        "approximate_diameter",
        "approximate_average_path_length",
    }
)

# Known multi-snapshot temporal series patterns (stem regex -> canonical series ID).
# Used when --max-per-series > 0 to prevent hundreds of near-identical snapshots
# (e.g. SNAP as-733 or as-caida) from dominating feature standardization and K-Means.
TEMPORAL_SERIES_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^as\d{8}$", re.IGNORECASE), "as-733"),
    (re.compile(r"^as-caida\d{8}$", re.IGNORECASE), "as-caida"),
    (re.compile(r"^oregon1_\d{6}$", re.IGNORECASE), "oregon1"),
    (re.compile(r"^oregon2_\d{6}$", re.IGNORECASE), "oregon2"),
    (re.compile(r"^network\d+$", re.IGNORECASE), "comm-f2f-resistance"),
)

# Maximum characters transmitted back from a worker on exception to prevent
# exceeding OS pipe buffers during error reporting.
MAX_ERROR_MESSAGE_CHARS = 2048


@dataclass(slots=True)
class CSRGraph:
    """Compact undirected simple graph stored in Compressed Sparse Row format.

    Attributes:
        n_vertices: Number of vertices (0-based internally, representing 1..n).
        n_edges: Number of undirected simple edges.
        matrix: Symmetric binary ``scipy.sparse.csr_matrix`` with sorted
            indices and zero diagonal.
    """

    n_vertices: int
    n_edges: int
    matrix: scipy.sparse.csr_matrix


@dataclass(slots=True)
class ClusteringResult:
    """Outputs of K-Means clustering and representative selection for plotting and metadata.

    Attributes:
        clusters: 1D array of cluster IDs assigned to each graph.
        selected: Ordered list of indices chosen as cluster representatives.
        selection_methods: Mapping from selected graph index to
            ``"cluster_centroid"`` or ``"farthest_first_fallback"``.
        standardized_matrix: 2D array after optional ``log1p`` and ``StandardScaler``.
        scaler: Fitted ``StandardScaler`` instance used on the clustered pool.
        centroids: 2D array of K-Means cluster centers in standardized space.
        distances_to_centroid: 1D array of Euclidean distances from each graph
            to its assigned cluster centroid.
        inertia: Sum of squared distances to cluster centers reported by K-Means.
        silhouette: Silhouette coefficient in standardized space, or ``None``
            when ``n_clusters`` is outside ``[2, n_graphs - 1]``.
    """

    clusters: np.ndarray
    selected: list[int]
    selection_methods: dict[int, str]
    standardized_matrix: np.ndarray
    scaler: StandardScaler
    centroids: np.ndarray
    distances_to_centroid: np.ndarray
    inertia: float
    silhouette: float | None


def format_feature_for_csv(feature: str, value: float) -> int | str:
    """Format a feature value for CSV export.

    Integer count features are emitted as ``int``; continuous features are
    formatted to six decimal places (normalizing ``-0.0`` to ``0.0``).

    Args:
        feature: Feature name from :data:`FEATURES`.
        value: Numeric feature measurement.

    Returns:
        Integer for count features or six-decimal string for continuous features.
    """
    if feature in INTEGER_FEATURES:
        return int(value)
    numeric = float(value)
    if numeric == 0.0:
        numeric = 0.0
    return f"{numeric:.6f}"


def read_dimacs_header(file_path: Path) -> tuple[int, int] | None:
    """Read ``(n, m)`` from the first valid ``p edge n m`` header line.

    Scans line by line without loading edges, skipping blank lines and ``c``
    comments.

    Args:
        file_path: Path to a DIMACS ``.col`` file.

    Returns:
        Tuple ``(n_vertices, n_edges)`` if a valid non-negative ``p edge n m``
        header is found; otherwise ``None``.
    """
    with file_path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            parts = line.strip().split()
            if not parts or parts[0] == "c":
                continue
            if parts[0] == "p":
                if len(parts) != 4 or parts[1] != "edge":
                    return None
                try:
                    n_vertices, n_edges = int(parts[2]), int(parts[3])
                except ValueError:
                    return None
                if n_vertices < 0 or n_edges < 0:
                    return None
                return n_vertices, n_edges
            return None
    return None


def read_dimacs_col(file_path: Path) -> CSRGraph:
    """Strictly parse a simple undirected DIMACS ``.col`` file into CSR format.

    Validates single ``p edge n m`` header, non-negative counts, valid ``1..n``
    endpoint bounds, absence of self-loops, absence of duplicate undirected
    edges, known record prefixes, and exact edge-line count match, while using
    compact NumPy/SciPy arrays (~8–24 bytes per undirected edge).

    Args:
        file_path: Path to the DIMACS ``.col`` file.

    Returns:
        Validated :class:`CSRGraph` with sorted neighbor lists and zero diagonal.

    Raises:
        ValueError: If the file violates the strict simple DIMACS ``.col`` format.
    """
    vertex_count: int | None = None
    expected_edges = 0
    edge_index = 0
    u_arr: np.ndarray | None = None
    v_arr: np.ndarray | None = None

    with file_path.open("r", encoding="utf-8", errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            parts = text.split()
            record = parts[0]
            if record == "c":
                continue
            try:
                if record == "p":
                    if vertex_count is not None or len(parts) != 4 or parts[1] != "edge":
                        raise ValueError("expected one 'p edge n m' header")
                    vertex_count, expected_edges = map(int, parts[2:])
                    if vertex_count < 0 or expected_edges < 0:
                        raise ValueError("negative graph size")
                    u_arr = np.empty(expected_edges, dtype=np.int32)
                    v_arr = np.empty(expected_edges, dtype=np.int32)
                elif record == "e":
                    if vertex_count is None or u_arr is None or v_arr is None or len(parts) != 3:
                        raise ValueError("edge before header or malformed edge")
                    u, v = int(parts[1]), int(parts[2])
                    if not (1 <= u <= vertex_count and 1 <= v <= vertex_count):
                        raise ValueError("vertex ID outside 1..n")
                    if u == v:
                        raise ValueError("self-loop edge")
                    if edge_index >= expected_edges:
                        raise ValueError("edge count exceeds header")
                    u_arr[edge_index] = u - 1
                    v_arr[edge_index] = v - 1
                    edge_index += 1
                else:
                    raise ValueError("unknown DIMACS record")
            except ValueError as error:
                snippet = text[:200]
                raise ValueError(
                    f"invalid DIMACS line at {file_path}:{line_number}: {snippet}"
                ) from error

    if vertex_count is None or u_arr is None or v_arr is None or edge_index != expected_edges:
        raise ValueError(f"{file_path}: missing header or edge count mismatch")

    if expected_edges == 0:
        matrix = scipy.sparse.csr_matrix((vertex_count, vertex_count), dtype=np.int8)
        return CSRGraph(n_vertices=vertex_count, n_edges=0, matrix=matrix)

    # Build a symmetric CSR matrix from both edge directions. Any duplicate
    # undirected edge merges during sum_duplicates(), strictly reducing nnz.
    rows = np.concatenate((u_arr, v_arr))
    cols = np.concatenate((v_arr, u_arr))
    del u_arr, v_arr
    data = np.ones(rows.size, dtype=np.int8)
    matrix = scipy.sparse.coo_matrix(
        (data, (rows, cols)), shape=(vertex_count, vertex_count)
    ).tocsr()
    del rows, cols, data
    matrix.sum_duplicates()
    if matrix.nnz != 2 * expected_edges:
        raise ValueError(f"{file_path}: duplicate undirected edge")
    matrix.sort_indices()
    return CSRGraph(n_vertices=vertex_count, n_edges=expected_edges, matrix=matrix)


def _has_edge(indptr: np.ndarray, indices: np.ndarray, u: int, v: int) -> bool:
    """Check edge membership in a sorted CSR graph via binary search.

    Always searches the shorter of the two adjacency lists in
    ``O(log min(deg(u), deg(v)))`` time.

    Args:
        indptr: CSR row pointer array.
        indices: Sorted CSR column index array.
        u: First 0-based vertex ID.
        v: Second 0-based vertex ID.

    Returns:
        ``True`` if ``(u, v)`` is present; ``False`` otherwise.
    """
    start_u, end_u = int(indptr[u]), int(indptr[u + 1])
    start_v, end_v = int(indptr[v]), int(indptr[v + 1])
    if (end_u - start_u) <= (end_v - start_v):
        neighbors = indices[start_u:end_u]
        target = v
    else:
        neighbors = indices[start_v:end_v]
        target = u
    position = int(np.searchsorted(neighbors, target))
    return position < neighbors.size and int(neighbors[position]) == target


def _exact_clustering_and_transitivity(
    indptr: np.ndarray, indices: np.ndarray, degrees: np.ndarray
) -> tuple[float, float]:
    """Compute exact ``mean_clustering`` and ``transitivity`` in one pass.

    Uses degree-oriented (Chiba-Nishizeki) triangle enumeration so each
    triangle ``{a, b, c}`` is discovered exactly once in strict
    ``(degree, vertex_id)`` order via C-speed Python set intersections.

    Args:
        indptr: CSR row pointer array.
        indices: Sorted CSR column index array.
        degrees: 1D ``int64`` vertex degree array of length ``n``.

    Returns:
        Tuple ``(mean_clustering, transitivity)``.
    """
    n = degrees.size
    if n == 0:
        return 0.0, 0.0
    wedges_per_node = degrees * (degrees - 1) // 2
    total_wedges = int(wedges_per_node.sum())
    if total_wedges == 0:
        return 0.0, 0.0

    # Orient each edge from lower to higher (degree, id); degree < 2 nodes
    # cannot participate in any triangle.
    adj_plus: list[set[int]] = [set() for _ in range(n)]
    for u in range(n):
        deg_u = int(degrees[u])
        if deg_u < 2:
            continue
        start, end = int(indptr[u]), int(indptr[u + 1])
        out = adj_plus[u]
        for v in indices[start:end].tolist():
            deg_v = int(degrees[v])
            if deg_v > deg_u or (deg_v == deg_u and v > u):
                out.add(v)

    triangles = np.zeros(n, dtype=np.int64)
    for u, su in enumerate(adj_plus):
        if len(su) < 2:
            continue
        for v in su:
            common = su & adj_plus[v]
            count = len(common)
            if count:
                triangles[u] += count
                triangles[v] += count
                for w in common:
                    triangles[w] += 1

    transitivity = float(triangles.sum()) / float(total_wedges)
    clustering = np.zeros(n, dtype=np.float64)
    mask = wedges_per_node > 0
    clustering[mask] = triangles[mask] / wedges_per_node[mask]
    return float(clustering.mean()), transitivity


def _sampled_clustering_and_transitivity(
    indptr: np.ndarray,
    indices: np.ndarray,
    degrees: np.ndarray,
    sample_count: int,
    seed: int,
) -> tuple[float, float]:
    """Estimate ``mean_clustering`` and ``transitivity`` via unbiased sampling.

    * ``transitivity`` uses wedge sampling (Seshadhri et al.): center vertices
      are sampled with exact probability proportional to ``d(v) * (d(v) - 1) / 2``
      and two distinct neighbors are tested for edge closure in ``O(log d)``.
    * ``mean_clustering`` samples eligible vertices (``d(v) >= 2``) uniformly,
      tests one uniform random neighbor pair per draw, and scales by
      ``|eligible| / n``; by the law of total expectation this estimator is
      strictly unbiased and immune to high-degree hubs.

    Args:
        indptr: CSR row pointer array.
        indices: Sorted CSR column index array.
        degrees: 1D ``int64`` vertex degree array of length ``n``.
        sample_count: Number of Monte Carlo samples for each estimator.
        seed: Deterministic random seed for NumPy's ``default_rng``.

    Returns:
        Tuple ``(estimated_mean_clustering, estimated_transitivity)``.
    """
    n = degrees.size
    if n == 0:
        return 0.0, 0.0
    wedges_per_node = degrees * (degrees - 1) // 2
    total_wedges = int(wedges_per_node.sum())
    if total_wedges == 0:
        return 0.0, 0.0

    rng = np.random.default_rng(seed)

    # 1. Exact integer inverse-CDF wedge sampling for global transitivity.
    cdf = np.cumsum(wedges_per_node)
    wedge_draws = rng.integers(0, total_wedges, size=sample_count)
    centers = np.searchsorted(cdf, wedge_draws, side="right")
    closed_wedges = 0
    for c in centers.tolist():
        deg_c = int(degrees[c])
        i = int(rng.integers(0, deg_c))
        j = int(rng.integers(0, deg_c - 1))
        if j >= i:
            j += 1
        base = int(indptr[c])
        if _has_edge(indptr, indices, int(indices[base + i]), int(indices[base + j])):
            closed_wedges += 1
    transitivity = closed_wedges / sample_count

    # 2. Unbiased mean local clustering via uniform eligible vertex + pair sampling.
    eligible = np.flatnonzero(degrees >= 2)
    chosen_vertices = eligible[rng.integers(0, eligible.size, size=sample_count)]
    closed_pairs = 0
    for c in chosen_vertices.tolist():
        deg_c = int(degrees[c])
        i = int(rng.integers(0, deg_c))
        j = int(rng.integers(0, deg_c - 1))
        if j >= i:
            j += 1
        base = int(indptr[c])
        if _has_edge(indptr, indices, int(indices[base + i]), int(indices[base + j])):
            closed_pairs += 1
    mean_clustering = (eligible.size / n) * (closed_pairs / sample_count)

    return float(mean_clustering), float(transitivity)


def degree_assortativity(
    matrix: scipy.sparse.csr_matrix, degrees: np.ndarray, n_edges: int
) -> float:
    """Compute exact Newman degree assortativity coefficient on a CSR graph.

    Uses the centered quadratic form ``(dc^T A dc) / sum_u (d_u * dc_u^2)``
    where ``dc_u = d_u - mu`` and ``mu = sum(d_u^2) / (2m)``, matching
    ``networkx.degree_assortativity_coefficient`` in ``O(n + m)`` time and
    ``O(n)`` auxiliary memory.

    Args:
        matrix: Symmetric binary CSR adjacency matrix with zero diagonal.
        degrees: 1D ``int64`` vertex degree array.
        n_edges: Number of undirected edges.

    Returns:
        Degree assortativity in ``[-1.0, 1.0]``, or ``0.0`` when undefined
        (e.g., edgeless or regular graphs).
    """
    if n_edges == 0:
        return 0.0
    deg_float = degrees.astype(np.float64)
    mean_endpoint_degree = float(np.dot(deg_float, deg_float) / (2.0 * n_edges))
    centered = deg_float - mean_endpoint_degree
    denominator = float(np.dot(deg_float, centered * centered))
    if denominator <= 0.0:
        return 0.0
    numerator = float(np.dot(centered, matrix @ centered))
    value = numerator / denominator
    if not math.isfinite(value):
        return 0.0
    return float(max(-1.0, min(1.0, value)))


def approximate_diameter_and_path_length(
    matrix: scipy.sparse.csr_matrix,
    largest_component_nodes: np.ndarray,
    sample_count: int,
    seed: int,
) -> tuple[float, float]:
    """Estimate diameter and average path length via sampled C-level BFS.

    Selects up to ``sample_count`` source vertices from the largest connected
    component via Algorithm R reservoir sampling and runs SciPy's Cython
    unweighted ``shortest_path`` one source at a time to keep peak memory
    bounded to ``O(n)``.

    Args:
        matrix: Symmetric CSR adjacency matrix.
        largest_component_nodes: Sorted 1D array of 0-based vertex IDs in the
            largest connected component.
        sample_count: Maximum number of BFS sources to sample.
        seed: Random seed controlling reservoir sampling.

    Returns:
        Tuple ``(approximate_diameter, approximate_average_path_length)``, or
        ``(0.0, 0.0)`` for an empty or single-vertex component.
    """
    if largest_component_nodes.size <= 1:
        return 0.0, 0.0

    rng = random.Random(seed)
    samples: list[int] = []
    for index, vertex in enumerate(largest_component_nodes.tolist()):
        if index < sample_count:
            samples.append(vertex)
            continue
        replacement = rng.randrange(index + 1)
        if replacement < sample_count:
            samples[replacement] = vertex

    eccentricities: list[float] = []
    average_path_lengths: list[float] = []
    for source in samples:
        distances = shortest_path(
            matrix, directed=False, unweighted=True, indices=source
        )
        reachable = distances[np.isfinite(distances)]
        count = reachable.size
        eccentricities.append(float(reachable.max()) if count else 0.0)
        # Divide by (count - 1) to exclude the trivial distance 0 from source to itself.
        average_path_lengths.append(
            float(reachable.sum() / (count - 1)) if count > 1 else 0.0
        )

    return float(max(eccentricities)), float(np.mean(average_path_lengths))


def degree_entropy(degrees: np.ndarray) -> float:
    """Compute base-2 Shannon entropy of a vertex degree sequence.

    Args:
        degrees: 1D array of vertex degrees.

    Returns:
        Shannon entropy in bits (normalized so regular graphs return ``0.0``
        rather than ``-0.0``).
    """
    if degrees.size == 0:
        return 0.0
    _, counts = np.unique(degrees, return_counts=True)
    probabilities = counts / counts.sum()
    entropy = float(-np.sum(probabilities * np.log2(probabilities)))
    return 0.0 if entropy == 0.0 else entropy


def extract_features(
    graph: CSRGraph,
    bfs_samples: int,
    seed: int,
    exact_triangle_edges: int = 200_000,
    triangle_samples: int = 10_000,
) -> dict[str, float]:
    """Compute all 16 structural topological features for a CSR graph.

    Uses exact Chiba-Nishizeki triangle counting when
    ``exact_triangle_edges == 0`` or ``graph.n_edges <= exact_triangle_edges``,
    and unbiased Monte Carlo sampling otherwise.

    Args:
        graph: Validated :class:`CSRGraph`.
        bfs_samples: Number of BFS source vertices for distance estimates.
        seed: Random seed for BFS and (when triggered) triangle sampling.
        exact_triangle_edges: Edge-count threshold for exact triangle counting;
            ``0`` forces exact computation regardless of size.
        triangle_samples: Sample budget used by sampled clustering/transitivity
            when ``graph.n_edges > exact_triangle_edges``.

    Returns:
        Dictionary mapping every name in :data:`FEATURES` to its numeric value.
    """
    n = graph.n_vertices
    m = graph.n_edges
    matrix = graph.matrix
    indptr = matrix.indptr
    indices = matrix.indices
    degrees = np.diff(indptr).astype(np.int64)

    density = (2.0 * m) / (n * (n - 1)) if n > 1 else 0.0
    assortativity = degree_assortativity(matrix, degrees, m)

    if n == 0:
        component_count = 0
        largest_component_size = 0
        diameter, average_path_length = 0.0, 0.0
        mean_clustering, transitivity = 0.0, 0.0
    else:
        component_count, labels = connected_components(
            matrix, directed=False, return_labels=True
        )
        comp_sizes = np.bincount(labels)
        largest_label = int(np.argmax(comp_sizes))
        largest_component_size = int(comp_sizes[largest_label])
        largest_nodes = np.flatnonzero(labels == largest_label)
        diameter, average_path_length = approximate_diameter_and_path_length(
            matrix, largest_nodes, bfs_samples, seed
        )
        if exact_triangle_edges == 0 or m <= exact_triangle_edges:
            mean_clustering, transitivity = _exact_clustering_and_transitivity(
                indptr, indices, degrees
            )
        else:
            mean_clustering, transitivity = _sampled_clustering_and_transitivity(
                indptr, indices, degrees, triangle_samples, seed
            )

    return {
        "n_vertices": n,
        "n_edges": m,
        "density": density,
        "minimum_degree": int(degrees.min()) if n else 0,
        "maximum_degree": int(degrees.max()) if n else 0,
        "mean_degree": float(degrees.mean()) if n else 0.0,
        "median_degree": float(np.median(degrees)) if n else 0.0,
        "degree_std": float(degrees.std()) if n else 0.0,
        "degree_entropy": degree_entropy(degrees),
        "mean_clustering": mean_clustering,
        "transitivity": transitivity,
        "assortativity": assortativity,
        "component_count": int(component_count),
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
    parser.add_argument(
        "--exact-triangle-edges",
        type=int,
        default=200_000,
        help=(
            "Compute exact clustering/transitivity up to this edge count and "
            "switch to unbiased sampling above it; use 0 for always exact "
            "(default: 200000)"
        ),
    )
    parser.add_argument(
        "--triangle-samples",
        type=int,
        default=10_000,
        help=(
            "Monte Carlo sample count for clustering and transitivity when "
            "m > --exact-triangle-edges (default: 10000)"
        ),
    )
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--db-name", help="Dataset name within datasets/")
    parser.add_argument("--db", type=Path, help="Explicit SQLite database path")
    parser.add_argument(
        "--max-vertices",
        type=int,
        default=5_000_000,
        help="Skip graphs above this vertex count; use 0 to disable (default: 5000000)",
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
    parser.add_argument(
        "--no-log-scale",
        action="store_true",
        help=(
            "Disable log1p scaling of heavy-tailed size/degree features "
            "before StandardScaler and K-Means"
        ),
    )
    parser.add_argument(
        "--max-per-series",
        type=int,
        default=0,
        help=(
            "Cap repetitive temporal snapshot series (e.g. as-733, as-caida) "
            "to at most this many representative snapshots before global "
            "clustering; 0 disables (default: 0)"
        ),
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


def resolve_csv_path(args: argparse.Namespace) -> Path:
    """Resolve the destination path for the plot-ready selection CSV.

    Uses ``args.csv`` if explicitly provided; otherwise writes
    ``selection.csv`` beside ``selected/`` (or inside ``args.out``).

    Args:
        args: Parsed CLI namespace.

    Returns:
        Destination CSV file path.
    """
    if args.csv is not None:
        return args.csv
    if args.out.name == "selected":
        return args.out.parent / "selection.csv"
    return args.out / "selection.csv"


def compute_pca_projection(
    standardized_matrix: np.ndarray, centroids: np.ndarray
) -> tuple[np.ndarray, np.ndarray, list[float], dict[str, dict[str, float]], PCA | None]:
    """Project standardized graphs and cluster centroids onto 2D PCA coordinates.

    Handles degenerate cases (single graph or zero total variance) without
    raising warnings, always returning 2-column ``float64`` coordinate arrays,
    explained variance ratios, and feature loadings for explaining axes in a
    paper.

    Args:
        standardized_matrix: 2D standardized feature array ``(n_graphs, n_features)``.
        centroids: 2D K-Means cluster centers ``(n_clusters, n_features)``.

    Returns:
        Tuple ``(points_2d, centroids_2d, explained_variance_ratio, loadings, pca_model)``.
    """
    n_graphs, n_features = standardized_matrix.shape
    n_clusters = centroids.shape[0]
    points_2d = np.zeros((n_graphs, 2), dtype=np.float64)
    centroids_2d = np.zeros((n_clusters, 2), dtype=np.float64)
    explained = [0.0, 0.0]
    loadings: dict[str, dict[str, float]] = {
        "pc1": {feature: 0.0 for feature in FEATURES},
        "pc2": {feature: 0.0 for feature in FEATURES},
    }

    n_components = min(2, n_graphs, n_features)
    if n_graphs < 2 or n_components < 1 or np.allclose(standardized_matrix, 0.0):
        return points_2d, centroids_2d, explained, loadings, None

    pca = PCA(n_components=n_components)
    projected_points = pca.fit_transform(standardized_matrix)
    projected_centroids = pca.transform(centroids)
    points_2d[:, :n_components] = projected_points
    centroids_2d[:, :n_components] = projected_centroids
    for idx, ratio in enumerate(pca.explained_variance_ratio_):
        explained[idx] = float(ratio) if math.isfinite(float(ratio)) else 0.0
    for idx in range(n_components):
        key = f"pc{idx + 1}"
        loadings[key] = {
            feature: round(float(pca.components_[idx, col]), 6)
            for col, feature in enumerate(FEATURES)
        }
    return points_2d, centroids_2d, explained, loadings, pca


def _distribution_stats(values: np.ndarray) -> dict[str, float]:
    """Compute descriptive summary statistics for a 1D feature vector.

    Args:
        values: 1D ``float64`` array of feature observations.

    Returns:
        Dictionary containing ``min``, ``q25``, ``median``, ``q75``, ``max``,
        ``mean``, and ``std``.
    """
    q25, median, q75 = np.quantile(values, (0.25, 0.5, 0.75))
    return {
        "min": round(float(values.min()), 6),
        "q25": round(float(q25), 6),
        "median": round(float(median), 6),
        "q75": round(float(q75), 6),
        "max": round(float(values.max()), 6),
        "mean": round(float(values.mean()), 6),
        "std": round(float(values.std()), 6),
    }


def compute_feature_coverage_summary(
    pool_matrix: np.ndarray, selected_indices: list[int]
) -> dict[str, dict[str, object]]:
    """Compare feature distributions between the clustered pool and selected subset.

    For each feature in :data:`FEATURES`, computes descriptive quantiles,
    range coverage ratio ``(sel_max - sel_min) / (pool_max - pool_min)``, and
    the two-sample Kolmogorov-Smirnov test statistic and p-value.

    Args:
        pool_matrix: 2D ``float64`` raw feature matrix for the clustered pool.
        selected_indices: Row indices selected as representatives.

    Returns:
        Mapping from feature name to pool/selected stats, coverage ratio, and
        KS test results.
    """
    selected_matrix = pool_matrix[selected_indices]
    summary: dict[str, dict[str, object]] = {}
    for col, feature in enumerate(FEATURES):
        pool_col = pool_matrix[:, col]
        sel_col = selected_matrix[:, col]
        pool_span = float(pool_col.max() - pool_col.min())
        sel_span = float(sel_col.max() - sel_col.min())
        coverage = (sel_span / pool_span) if pool_span > 0.0 else 1.0
        ks_result = ks_2samp(pool_col, sel_col)
        ks_stat = float(ks_result.statistic)
        ks_p = float(ks_result.pvalue)
        summary[feature] = {
            "pool": _distribution_stats(pool_col),
            "selected": _distribution_stats(sel_col),
            "range_coverage": round(coverage, 6),
            "ks_statistic": round(ks_stat, 6) if math.isfinite(ks_stat) else None,
            "ks_pvalue": round(ks_p, 6) if math.isfinite(ks_p) else None,
        }
    return summary


def save_csv(
    file_path: Path,
    rows: list[dict[str, object]],
) -> None:
    """Export plot-ready selection records (all eligible graphs) to CSV.

    Args:
        file_path: Destination CSV path (parent directories created as needed).
        rows: Pre-formatted row dictionaries matching the CSV schema.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "file",
                "series",
                "in_clustered_pool",
                "cluster",
                "selected",
                "selection_method",
                "triangle_mode",
                "distance_to_centroid",
                "pc1",
                "pc2",
                *FEATURES,
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _worker(
    file_path: str,
    bfs_samples: int,
    seed: int,
    exact_triangle_edges: int,
    triangle_samples: int,
    limit_bytes: int,
    sender,
) -> None:
    """Execute feature extraction in a memory-limited child process.

    Applies ``resource.RLIMIT_AS`` when ``limit_bytes > 0`` (ignoring platform
    errors), parses ``file_path`` into CSR, computes features, and sends
    ``(True, feature_dict)`` or ``(False, truncated_error_message)`` through
    ``sender``.

    Args:
        file_path: String path to the DIMACS ``.col`` file.
        bfs_samples: Number of BFS samples for distance estimation.
        seed: Random seed for BFS and triangle sampling.
        exact_triangle_edges: Edge-count threshold for exact triangle counting.
        triangle_samples: Sample budget when triangle sampling is triggered.
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
        features = extract_features(
            graph, bfs_samples, seed, exact_triangle_edges, triangle_samples
        )
        sender.send((True, features))
    except MemoryError:
        sender.send((False, "insufficient memory"))
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if len(message) > MAX_ERROR_MESSAGE_CHARS:
            message = message[:MAX_ERROR_MESSAGE_CHARS] + "..."
        sender.send((False, message))
    finally:
        sender.close()


def compute_features_in_subprocess(
    file_path: Path,
    bfs_samples: int,
    seed: int,
    exact_triangle_edges: int,
    triangle_samples: int,
    limit_bytes: int,
    timeout_s: float,
) -> tuple[bool, dict | str]:
    """Run :func:`_worker` in a forked subprocess with timeout and crash recovery.

    Polls the pipe before joining the child process so large payloads cannot
    deadlock OS pipe buffers, terminates (and after 5 seconds forcefully kills)
    workers exceeding ``timeout_s``, and translates OOM kills or ``EOFError``
    into a structured failure tuple.

    Args:
        file_path: Path to the DIMACS ``.col`` file.
        bfs_samples: Number of BFS source vertices to sample.
        seed: Random seed for BFS and triangle sampling.
        exact_triangle_edges: Edge-count threshold for exact triangle counting.
        triangle_samples: Sample budget when triangle sampling is triggered.
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
        args=(
            str(file_path),
            bfs_samples,
            seed,
            exact_triangle_edges,
            triangle_samples,
            limit_bytes,
            sender,
        ),
    )
    process.start()
    sender.close()

    try:
        deadline = time.monotonic() + timeout_s if timeout_s > 0 else None
        got_result = False
        result: tuple[bool, dict | str] | None = None

        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                wait = min(1.0, remaining)
            else:
                wait = 1.0

            if receiver.poll(wait):
                try:
                    result = receiver.recv()
                    got_result = True
                except EOFError:
                    pass
                break

            if not process.is_alive():
                if receiver.poll(0):
                    try:
                        result = receiver.recv()
                        got_result = True
                    except EOFError:
                        pass
                break

        if got_result and result is not None:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            return result

        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
            return False, f"timeout after {timeout_s:.0f}s"

        process.join()
        return (
            False,
            f"process exited without a result (exitcode={process.exitcode}; probable OOM)",
        )
    finally:
        receiver.close()


def prepare_database(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite feature cache, migrate legacy columns, and ensure schema.

    Configures ``WAL`` journal mode and ``synchronous=NORMAL``, renames any
    legacy Portuguese column names in ``features`` and ``failures`` to English,
    creates the ``features``, ``failures``, and ``sources`` tables if absent,
    and adds ``exact_triangle_edges`` and ``triangle_samples`` columns to older
    ``sources`` tables.

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
        "bfs_samples INTEGER, seed INTEGER, feature_version INTEGER, "
        "exact_triangle_edges INTEGER, triangle_samples INTEGER)"
    )
    source_columns = {
        row[1] for row in connection.execute('PRAGMA table_info("sources")')
    }
    for column in ("exact_triangle_edges", "triangle_samples"):
        if column not in source_columns:
            connection.execute(f'ALTER TABLE sources ADD COLUMN "{column}" INTEGER')
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
    exact_triangle_edges: int,
    triangle_samples: int,
    db_path: Path,
    max_vertices: int,
    memory_limit_mb: int,
    timeout_s: float,
) -> None:
    """Extract and persist features for pending, modified, or previously failed files.

    Reuses cached entries only when
    ``(mtime_ns, size, bfs_samples, seed, FEATURE_VERSION, exact_triangle_edges, triangle_samples)``
    matches the ``sources`` table joined with ``features``. Closes the SQLite
    connection before every ``fork()`` so worker subprocesses never inherit
    open WAL descriptors, and commits each graph immediately for resumability.

    Args:
        files: Candidate ``.col`` files to process.
        bfs_samples: Number of BFS samples per graph.
        seed: Random seed for BFS and triangle sampling.
        exact_triangle_edges: Edge-count threshold for exact triangle counting.
        triangle_samples: Sample budget when triangle sampling is triggered.
        db_path: Path to the persistent SQLite cache.
        max_vertices: Maximum allowed declared vertex count (``0`` disables).
        memory_limit_mb: Per-worker virtual memory cap in MiB (``0`` disables).
        timeout_s: Per-worker timeout in seconds (``0`` disables).
    """
    with closing(prepare_database(db_path)) as connection:
        cached_sources = {
            record[0]: tuple(record[1:])
            for record in connection.execute(
                "SELECT s.file, s.mtime_ns, s.size, s.bfs_samples, s.seed, "
                "s.feature_version, s.exact_triangle_edges, s.triangle_samples "
                "FROM sources s JOIN features f USING (file)"
            )
        }

    fingerprints: dict[str, tuple[int, int, int, int, int, int, int]] = {}
    for path in files:
        stat = path.stat()
        fingerprints[path.name] = (
            stat.st_mtime_ns,
            stat.st_size,
            bfs_samples,
            seed,
            FEATURE_VERSION,
            exact_triangle_edges,
            triangle_samples,
        )
    pending = [
        file_path
        for file_path in sort_by_size(files)
        if cached_sources.get(file_path.name) != fingerprints[file_path.name]
    ]
    print(
        f"Persistent database: {db_path} | cached: {len(files) - len(pending)} | "
        f"pending: {len(pending)}"
    )

    feature_columns = ", ".join(f'"{feature}"' for feature in FEATURES)
    placeholders = ", ".join("?" for _ in range(1 + len(FEATURES)))
    limit_bytes = memory_limit_mb * 1024 * 1024

    for index, file_path in enumerate(pending, start=1):
        header = read_dimacs_header(file_path)
        if max_vertices > 0 and header and header[0] > max_vertices:
            reason = f"n={header[0]} exceeds max-vertices={max_vertices}"
            with closing(prepare_database(db_path)) as connection:
                connection.execute("DELETE FROM features WHERE file = ?", (file_path.name,))
                connection.execute("DELETE FROM sources WHERE file = ?", (file_path.name,))
                connection.execute(
                    "INSERT OR REPLACE INTO failures (file, reason) VALUES (?, ?)",
                    (file_path.name, reason),
                )
                connection.commit()
            print(f"[{index}/{len(pending)}] {file_path.name}: SKIPPED ({reason})")
            continue

        # No SQLite connection is open across fork().
        success, result = compute_features_in_subprocess(
            file_path,
            bfs_samples,
            seed,
            exact_triangle_edges,
            triangle_samples,
            limit_bytes,
            timeout_s,
        )
        with closing(prepare_database(db_path)) as connection:
            connection.execute("DELETE FROM features WHERE file = ?", (file_path.name,))
            connection.execute("DELETE FROM failures WHERE file = ?", (file_path.name,))
            connection.execute("DELETE FROM sources WHERE file = ?", (file_path.name,))
            if success and isinstance(result, dict):
                connection.execute(
                    f"INSERT OR REPLACE INTO features (file, {feature_columns}) "
                    f"VALUES ({placeholders})",
                    (file_path.name, *(result[feature] for feature in FEATURES)),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO sources "
                    "(file, mtime_ns, size, bfs_samples, seed, feature_version, "
                    "exact_triangle_edges, triangle_samples) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
                    "INSERT OR REPLACE INTO failures (file, reason) VALUES (?, ?)",
                    (file_path.name, str(result)),
                )
                connection.commit()
                print(f"[{index}/{len(pending)}] {file_path.name}: FAILED ({result})")


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


def apply_log_scale(matrix: np.ndarray, log_scale: bool) -> np.ndarray:
    """Apply optional ``log1p`` scaling to heavy-tailed feature columns.

    Args:
        matrix: 2D ``float64`` raw feature matrix.
        log_scale: If ``True``, apply ``np.log1p`` to :data:`LOG_SCALED_FEATURE_INDICES`.

    Returns:
        Transformed 2D ``float64`` copy.
    """
    transformed = matrix.astype(np.float64, copy=True)
    if log_scale:
        for column in LOG_SCALED_FEATURE_INDICES:
            transformed[:, column] = np.log1p(np.maximum(transformed[:, column], 0.0))
    return transformed


def transform_features_for_clustering(
    matrix: np.ndarray, log_scale: bool
) -> tuple[np.ndarray, StandardScaler]:
    """Prepare the feature matrix for distance-based clustering.

    Optionally applies ``log1p`` to non-negative heavy-tailed scale features
    (sizes, degrees, component count, path distances) before zero-mean /
    unit-variance standardization.

    Args:
        matrix: 2D ``float64`` feature matrix of shape ``(n_graphs, len(FEATURES))``.
        log_scale: If ``True``, apply ``np.log1p`` to scale features first.

    Returns:
        Tuple ``(standardized_matrix, fitted_scaler)``.
    """
    transformed = apply_log_scale(matrix, log_scale)
    scaler = StandardScaler()
    return scaler.fit_transform(transformed), scaler


def select_representatives_kmeans(
    matrix: np.ndarray, target_count: int, seed: int, log_scale: bool
) -> ClusteringResult:
    """Cluster standardized feature vectors with K-Means and pick representatives.

    Chooses the graph nearest each non-empty cluster centroid in Euclidean
    distance. If empty clusters leave fewer than ``target_count`` graphs, fills
    remaining slots via greedy farthest-first traversal so every added graph
    maximizes its minimum distance to the already-selected set.

    Args:
        matrix: 2D ``float64`` feature matrix of shape ``(n_graphs, len(FEATURES))``.
        target_count: Number of representatives to choose (``1 <= target_count <= n_graphs``).
        seed: Random seed for ``KMeans``.
        log_scale: Forwarded to :func:`transform_features_for_clustering`.

    Returns:
        :class:`ClusteringResult` containing labels, selected indices,
        selection methods, standardized matrix, fitted scaler, centroids,
        centroid distances, inertia, and silhouette score.
    """
    n_graphs = matrix.shape[0]
    standardized_matrix, scaler = transform_features_for_clustering(matrix, log_scale)
    kmeans = KMeans(n_clusters=target_count, random_state=seed, n_init=10)
    clusters = kmeans.fit_predict(standardized_matrix)
    centroids = kmeans.cluster_centers_
    distances_to_centroid = np.linalg.norm(
        standardized_matrix - centroids[clusters], axis=1
    )

    selected: list[int] = []
    selection_methods: dict[int, str] = {}
    for cluster in range(target_count):
        indices = np.where(clusters == cluster)[0]
        if not len(indices):
            continue
        distances = distances_to_centroid[indices]
        chosen_idx = int(indices[np.argmin(distances)])
        selected.append(chosen_idx)
        selection_methods[chosen_idx] = "cluster_centroid"

    # Greedy farthest-first fallback when duplicate points produce empty clusters.
    if len(selected) < target_count:
        remaining = [index for index in range(n_graphs) if index not in selected]
        while len(selected) < target_count and remaining:
            chosen = standardized_matrix[selected]
            min_distances = [
                float(np.min(np.linalg.norm(chosen - standardized_matrix[index], axis=1)))
                for index in remaining
            ]
            best_pos = int(np.argmax(min_distances))
            chosen_idx = remaining.pop(best_pos)
            selected.append(chosen_idx)
            selection_methods[chosen_idx] = "farthest_first_fallback"

    unique_clusters = np.unique(clusters).size
    silhouette: float | None = None
    if 2 <= unique_clusters < n_graphs:
        raw_silhouette = float(silhouette_score(standardized_matrix, clusters))
        if math.isfinite(raw_silhouette):
            silhouette = raw_silhouette

    return ClusteringResult(
        clusters=clusters,
        selected=selected,
        selection_methods=selection_methods,
        standardized_matrix=standardized_matrix,
        scaler=scaler,
        centroids=centroids,
        distances_to_centroid=distances_to_centroid,
        inertia=float(kmeans.inertia_),
        silhouette=silhouette,
    )


def temporal_series_key(filename: str) -> str | None:
    """Return a canonical series identifier for known snapshot floods, or ``None``.

    Args:
        filename: Graph filename (e.g. ``as19971108.col``).

    Returns:
        Canonical series name if the stem matches a known temporal snapshot
        pattern; otherwise ``None``.
    """
    stem = Path(filename).stem
    for pattern, series_name in TEMPORAL_SERIES_PATTERNS:
        if pattern.fullmatch(stem):
            return series_name
    return None


def collapse_temporal_series(
    names: list[str],
    matrix: np.ndarray,
    max_per_series: int,
    seed: int,
    log_scale: bool,
) -> tuple[list[str], np.ndarray, dict[str, dict[str, int]]]:
    """Reduce oversized temporal snapshot series before global clustering.

    Any recognized snapshot series containing more than ``max_per_series``
    graphs is condensed to its ``max_per_series`` most structurally
    representative snapshots via :func:`select_representatives_kmeans`. Graphs
    outside recognized series (or series already within the limit) are kept
    unchanged.

    Args:
        names: Ordered list of eligible graph filenames.
        matrix: Matching 2D ``float64`` feature array.
        max_per_series: Maximum snapshots retained per recognized series.
        seed: Random seed forwarded to within-series K-Means.
        log_scale: Forwarded to within-series K-Means.

    Returns:
        Tuple ``(filtered_names, filtered_matrix, collapsed_report)`` preserving
        deterministic ordering and recording ``{"before": N, "after": K}`` for
        each capped series.
    """
    series_groups: dict[str, list[int]] = {}
    kept_indices: list[int] = []
    collapsed_report: dict[str, dict[str, int]] = {}
    for index, name in enumerate(names):
        series = temporal_series_key(name)
        if series is None:
            kept_indices.append(index)
        else:
            series_groups.setdefault(series, []).append(index)

    for series_name in sorted(series_groups):
        group = series_groups[series_name]
        if len(group) <= max_per_series:
            kept_indices.extend(group)
            continue
        series_result = select_representatives_kmeans(
            matrix[group], max_per_series, seed, log_scale
        )
        chosen = [group[pos] for pos in series_result.selected]
        kept_indices.extend(chosen)
        collapsed_report[series_name] = {
            "before": len(group),
            "after": len(chosen),
        }
        print(
            f"Temporal series '{series_name}': kept {len(chosen)} of {len(group)} "
            f"representative snapshots"
        )

    kept_indices.sort()
    return [names[index] for index in kept_indices], matrix[kept_indices], collapsed_report


def main() -> None:
    """Run feature extraction, K-Means representative selection, and file export.

    Validates CLI parameters, optionally updates the SQLite feature cache,
    filters cached rows to currently present eligible inputs, optionally caps
    repetitive temporal snapshot series, clusters standardized feature vectors
    with K-Means, synchronizes ``*.col`` files in ``--out``, and optionally
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
    if args.exact_triangle_edges < 0:
        raise ValueError("exact-triangle-edges must be at least 0")
    if args.triangle_samples < 1:
        raise ValueError("triangle-samples must be at least 1")
    if args.max_vertices < 0:
        raise ValueError("max-vertices must be at least 0")
    if args.memory_limit_mb < 0:
        raise ValueError("memory-limit-mb must be at least 0")
    if not math.isfinite(args.timeout_s) or args.timeout_s < 0:
        raise ValueError("timeout-s must be finite and at least 0")
    if args.max_per_series < 0:
        raise ValueError("max-per-series must be at least 0")
    if args.input_dir.resolve() == args.out.resolve():
        raise ValueError("input-dir and out must be different directories")

    database_path = resolve_database_path(args)
    if not args.only_select:
        files = sorted(args.input_dir.glob("*.col"))
        if args.skip_edgeless:
            invalid = [
                file_path
                for file_path in files
                if not has_vertices_and_edges(file_path)
            ]
            invalid_set = set(invalid)
            files = [file_path for file_path in files if file_path not in invalid_set]
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
            args.exact_triangle_edges,
            args.triangle_samples,
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
        index
        for index, name in enumerate(names)
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

    log_scale = not args.no_log_scale
    all_eligible_names = list(names)
    all_eligible_matrix = matrix.copy()
    eligible_before_series_cap = len(all_eligible_names)
    collapsed_series: dict[str, dict[str, int]] = {}
    if args.max_per_series > 0:
        names, matrix, collapsed_series = collapse_temporal_series(
            names, matrix, args.max_per_series, args.seed, log_scale
        )
        if len(names) < eligible_before_series_cap:
            print(
                f"After temporal series cap (--max-per-series {args.max_per_series}): "
                f"{len(names)} of {eligible_before_series_cap} graphs enter global clustering"
            )

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

    clustering = select_representatives_kmeans(
        matrix, target_count, args.seed, log_scale
    )
    clusters = clustering.clusters
    selected = clustering.selected
    points_2d, centroids_2d, pca_variance_ratio, pca_loadings, pca_model = (
        compute_pca_projection(clustering.standardized_matrix, clustering.centroids)
    )

    args.out.mkdir(parents=True, exist_ok=True)
    selected_names = {names[index] for index in selected}
    for existing in args.out.glob("*.col"):
        if existing.name not in selected_names:
            existing.unlink()

    selected_manifest: list[dict[str, object]] = []
    cluster_representatives: dict[int, str] = {}
    print(f"Selected {len(selected)} graphs:")
    for index in selected:
        name = names[index]
        cluster_id = int(clusters[index])
        cluster_representatives.setdefault(cluster_id, name)
        values = {
            feature: matrix[index, column]
            for column, feature in enumerate(FEATURES)
        }
        src_path = args.input_dir / name
        dst_path = args.out / name
        shutil.copy2(src_path, dst_path)
        n_edges_int = int(values["n_edges"])
        tri_mode = (
            "exact"
            if args.exact_triangle_edges == 0 or n_edges_int <= args.exact_triangle_edges
            else "sampled"
        )
        selected_manifest.append(
            {
                "file": name,
                "series": temporal_series_key(name),
                "cluster": cluster_id,
                "selection_method": clustering.selection_methods[index],
                "triangle_mode": tri_mode,
                "distance_to_centroid": round(float(clustering.distances_to_centroid[index]), 6),
                "pc1": round(float(points_2d[index, 0]), 6),
                "pc2": round(float(points_2d[index, 1]), 6),
                "n_vertices": int(values["n_vertices"]),
                "n_edges": n_edges_int,
                "density": round(float(values["density"]), 6),
                "mean_degree": round(float(values["mean_degree"]), 6),
                "file_size_bytes": dst_path.stat().st_size,
                "sha256": sha256_file(dst_path),
            }
        )
        print(
            f"  {name}: n={int(values['n_vertices'])}, "
            f"m={n_edges_int}, "
            f"density={values['density']:.6f}, "
            f"mean degree={values['mean_degree']:.6f}"
        )

    # Project all eligible graphs (including any snapshots capped by --max-per-series)
    # into the same standardized + PCA coordinate system for complete paper plots.
    pool_index_by_name = {name: idx for idx, name in enumerate(names)}
    all_scaled = clustering.scaler.transform(apply_log_scale(all_eligible_matrix, log_scale))
    all_points_2d = np.zeros((len(all_eligible_names), 2), dtype=np.float64)
    if pca_model is not None:
        projected_all = pca_model.transform(all_scaled)
        all_points_2d[:, : projected_all.shape[1]] = projected_all

    csv_rows: list[dict[str, object]] = []
    for row_idx, name in enumerate(all_eligible_names):
        pool_idx = pool_index_by_name.get(name)
        in_pool = pool_idx is not None
        is_selected = in_pool and pool_idx in clustering.selection_methods
        n_edges_int = int(all_eligible_matrix[row_idx, 1])
        tri_mode = (
            "exact"
            if args.exact_triangle_edges == 0 or n_edges_int <= args.exact_triangle_edges
            else "sampled"
        )
        if is_selected and pool_idx is not None:
            method = clustering.selection_methods[pool_idx]
        elif in_pool:
            method = "not_selected"
        else:
            method = "series_capped"

        if in_pool and pool_idx is not None:
            cluster_val = int(clusters[pool_idx])
            dist = float(clustering.distances_to_centroid[pool_idx])
            dist_str = f"{0.0 if dist == 0.0 else dist:.6f}"
            pc1 = float(points_2d[pool_idx, 0])
            pc2 = float(points_2d[pool_idx, 1])
        else:
            cluster_val = -1
            dist_str = ""
            pc1 = float(all_points_2d[row_idx, 0])
            pc2 = float(all_points_2d[row_idx, 1])

        csv_rows.append(
            {
                "file": name,
                "series": temporal_series_key(name) or "",
                "in_clustered_pool": in_pool,
                "cluster": cluster_val,
                "selected": is_selected,
                "selection_method": method,
                "triangle_mode": tri_mode,
                "distance_to_centroid": dist_str,
                "pc1": f"{0.0 if pc1 == 0.0 else pc1:.6f}",
                "pc2": f"{0.0 if pc2 == 0.0 else pc2:.6f}",
                **{
                    feature: format_feature_for_csv(
                        feature, all_eligible_matrix[row_idx, column]
                    )
                    for column, feature in enumerate(FEATURES)
                },
            }
        )

    csv_path = resolve_csv_path(args)
    save_csv(csv_path, csv_rows)
    print(f"Features saved to {csv_path}")

    centroids_pca = [
        {
            "cluster": cluster_id,
            "pc1": round(float(centroids_2d[cluster_id, 0]), 6),
            "pc2": round(float(centroids_2d[cluster_id, 1]), 6),
            "size": int(np.sum(clusters == cluster_id)),
            "representative": cluster_representatives.get(cluster_id),
        }
        for cluster_id in range(target_count)
    ]
    scaler_stats = {
        feature: {
            "mean": round(float(clustering.scaler.mean_[col]), 6),
            "std": round(float(clustering.scaler.scale_[col]), 6),
        }
        for col, feature in enumerate(FEATURES)
    }
    feature_summary = compute_feature_coverage_summary(matrix, selected)
    metadata_path = update_metadata(
        resolve_metadata_path(args.out),
        "selection",
        {
            **base_provenance(("numpy", "scipy", "scikit-learn")),
            "input_dir": str(args.input_dir),
            "output_dir": str(args.out),
            "database_path": str(database_path),
            "csv_path": str(csv_path),
            "parameters": {
                "requested_count": args.n,
                "selected_count": len(selected),
                "seed": args.seed,
                "bfs_samples": args.bfs_samples,
                "exact_triangle_edges": args.exact_triangle_edges,
                "triangle_samples": args.triangle_samples,
                "max_vertices": args.max_vertices,
                "memory_limit_mb": args.memory_limit_mb,
                "timeout_s": args.timeout_s,
                "skip_edgeless": args.skip_edgeless,
                "use_all_if_fewer": args.use_all_if_fewer,
                "only_select": args.only_select,
                "log_scale": log_scale,
                "max_per_series": args.max_per_series,
                "feature_version": FEATURE_VERSION,
            },
            "features": {
                "all": list(FEATURES),
                "log_scaled": [FEATURES[i] for i in LOG_SCALED_FEATURE_INDICES] if log_scale else [],
            },
            "summary": {
                "eligible_before_series_cap": eligible_before_series_cap,
                "clustered_pool": len(names),
                "selected_count": len(selected),
                "failed_or_skipped_count": len(failures),
                "temporal_series_collapsed": collapsed_series,
                "kmeans_inertia": round(clustering.inertia, 6),
                "silhouette_score": (
                    round(clustering.silhouette, 6)
                    if clustering.silhouette is not None
                    else None
                ),
                "pca_explained_variance_ratio": [
                    round(pca_variance_ratio[0], 6),
                    round(pca_variance_ratio[1], 6),
                ],
            },
            "pca_loadings": pca_loadings,
            "scaler_stats": scaler_stats,
            "feature_summary": feature_summary,
            "centroids_pca": centroids_pca,
            "selected_graphs": selected_manifest,
            "failures": [{"file": name, "reason": reason} for name, reason in failures],
        },
    )
    print(f"Metadata saved to {metadata_path}")
    print(f"Graphs copied to {args.out}")


if __name__ == "__main__":
    main()
