# Command and technical reference

[Back to the getting-started guide](../README.md).

This reference documents datasets, CLI options, defaults, output formats, and
current limitations. All workflows work with plain Python via
`python graph_instances.py ...` (which sets `PROJECT_ROOT` automatically);
`mise` tasks are optional shortcuts.

For direct `python scripts/...` calls, activate `.venv` and export
`PROJECT_ROOT="$PWD"` from the repository root first.

- [Downloaded datasets and converters](#downloaded-datasets-and-converters)
- [Synthetic generators](#synthetic-generators)
- [Isolated-vertex filter](#isolated-vertex-filter)
- [Representative selection](#representative-selection)
- [DIMACS `.col` format](#dimacs-col-format)
- [Known limitations](#known-limitations)

---

## Downloaded datasets and converters

| Dataset | Source | Raw format | Downloader env var (default) | Converter |
| --- | --- | --- | --- | --- |
| `harwell-boeing` | NIST Matrix Market | `.mtx.gz` -> `.mtx` | `HARWELL_BOEING_DOWNLOAD_WORKERS` (`15`) | `matrix_market.py` |
| `dimacs` | Network Repository | `.zip` -> `.mtx` | `DIMACS_DOWNLOAD_WORKERS` (`78`) | `matrix_market.py` |
| `bhoslib` | Network Repository | `.zip` -> `.mtx` | `BHOSLIB_DOWNLOAD_WORKERS` (`36`) | `matrix_market.py` |
| `snap` | Stanford SNAP | `.gz` / `.tar.gz` / `.zip` | `SNAP_DOWNLOAD_WORKERS` (URL count) | `snap.py` |

- **Harwell-Boeing**: Discovers all sets from NIST Matrix Market
  (`https://math.nist.gov/pub/MatrixMarket2/Harwell-Boeing/`), decompresses
  `.mtx.gz` files atomically into `datasets/harwell_boeing/raw/`, and skips
  existing `.mtx` files.
- **DIMACS & BHOSLIB**: Download checked-in instance catalogs from Network
  Repository, try original/lowercase/uppercase archive names, flatten files
  into `raw/`, discard `readme*`, and deduplicate identical files on reruns.
- **SNAP**: Excludes `com-Friendster` by default; pass `--include-friendster`
  (or `mise run download::snap::full`) to include it. Tracks completion markers
  in `.completed/` (also recognizing legacy `.concluidos/`).

### Matrix Market conversion (`scripts/convert/matrix_market.py`)

Accepts an optional `.mtx` file or directory (default: all `datasets/*/raw/`).
Repairs mismatched coordinate header entry counts in a temporary copy without
modifying sources, keeps only **square, numerically symmetric** matrices,
strips explicit zeros and diagonal self-loops, rejects filename stem collisions,
and writes 1-based sorted edges to `processed/<stem>.col`. Pass
`--remove-isolated` to discard entire graphs with any degree-0 vertex.

### SNAP conversion (`scripts/convert/snap.py`)

Supports whitespace edge lists (`.txt`, `.edge`, `.edges`, `.edgelist`), CSV/TSV
edge lists (with header detection), `mooc_actions.tsv` (bipartite `user:` /
`target:` namespaces), `wiki-RfA.txt`, `wikiElec.ElecBs3.txt`, and
`network<N>.csv` temporal matrices while skipping auxiliary metadata/feature
files. Uses a disposable on-disk SQLite database inside `--output` to
deduplicate undirected edges and assign contiguous `1..n` IDs (registering all
observed vertices before stripping self-loops and honoring `# Nodes:` headers).

- `--analyze`: Print `KEEP` / `IGNORE` file classification without converting.
- `--remove-isolated`: Discard entire graphs containing any isolated vertex.

---

## Synthetic generators

All four generators live under `scripts/generate/`, write DIMACS `.col` files
directly to `datasets/<model>/processed/` (via dispatcher/mise) or
`random_<model>/` (direct invocation without `--out`), and do not require
`PROJECT_ROOT`.

### Shared options

| Option | Default | Meaning |
| --- | --- | --- |
| `--vertices`, `--n N [N ...]` | `None` | Explicit vertex counts; overrides `--doubling`. |
| `--doubling START FACTOR STEPS` | `200 1.15 30` | Deduplicated geometric progression used when `--n` is omitted. |
| `--count`, `--reps K` | `1` | Graphs generated per parameter combination (`K >= 1`). |
| `--seed SEED` | `42` | Initial RNG seed; increments by 1 after every attempt (including skipped graphs). |
| `--out DIR` | Model-specific | Output directory. |
| `--remove-isolated` | Off | Discard entire graphs with any degree-0 vertex (not replaced). |

### Model-specific options

| Model | Option | Default | Constraints / notes |
| --- | --- | --- | --- |
| `cubic` | *(sizes only)* | — | Every `n` must be even and `> 3`; `--doubling` rounds odd sizes up to even. |
| `erdos-renyi` | `--avg-degree G [...]` | `[10.0]` | Computes `p = min(G / (n - 1), 1.0)`; requires `n >= 2` and `G > 0`. |
| `erdos-renyi` | `--p P [...]` | `None` | Explicit edge probability in `(0, 1]`; overrides `--avg-degree`. |
| `barabasi-albert` | `--m M [...]` | `[4]` | Edges per new vertex; requires `1 <= m < n`. |
| `watts-strogatz` | `--k K [...]` | `[6]` | Initial ring neighbors (`1 <= k < n`; prefer even `k`). |
| `watts-strogatz` | `--p-rewire P [...]` | `[0.1]` | Edge rewiring probability in `[0, 1]`. |

---

## Isolated-vertex filter

`scripts/filter/isolated_vertices.py` streams `.col` files line by line using a
1-bit-per-vertex bitset (~12.5 MB for $10^8$ vertices). It validates the single
`p edge n m` header, `1..n` endpoint bounds, record prefixes, and exact
edge-line count. Self-loops do not count as establishing a neighbor.

- **Dry run (default)**: Reports `WOULD REMOVE` without modifying files.
- **`--remove-isolated`**: Deletes the **entire `.col` file** whenever at least
  one vertex has no non-loop incident edge. Empty graphs (`n = 0`) have zero
  isolated vertices.
- **Default scope**: Without a positional path, scans `datasets/*/processed/`
  and `datasets/*/selected/` (never `raw/`). Malformed files are reported, left
  untouched, and cause exit status `1`.

---

## Representative selection

`scripts/select/representative_selection.py` extracts **16 structural features**
using compact NumPy/SciPy CSR arrays inside memory- and time-limited forked
worker subprocesses (**Unix only**), caches results in SQLite WAL
(`datasets/<dataset>/features.sqlite3`), clusters standardized vectors with
K-Means (greedy farthest-first fallback for empty clusters), copies the graph
nearest each centroid to `selected/` (removing stale `.col` files), and
optionally exports `--csv`.

**Features (16)**: `n_vertices`, `n_edges`, `density`, `minimum_degree`,
`maximum_degree`, `mean_degree`, `median_degree`, `degree_std`,
`degree_entropy`, `mean_clustering`, `transitivity`, `assortativity`,
`component_count`, `largest_component_fraction`, `approximate_diameter`,
`approximate_average_path_length`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--input-dir DIR` | Required | Directory of top-level `*.col` files (must differ from `--out`). |
| `--count`, `--n K` | Required | Target subset size (presets: HB 50, DIMACS 80, BHOSLIB 30, SNAP 20, synthetic 30). |
| `--out DIR` | Required | Destination directory for selected `.col` files. |
| `--seed SEED` | `42` | Seed for K-Means, BFS sampling, and triangle sampling. |
| `--bfs-samples S` | `10` | BFS source vertices sampled from the largest component. |
| `--exact-triangle-edges M` | `200000` | Exact Chiba-Nishizeki clustering/transitivity up to `M` edges; unbiased wedge/pair sampling above (`0` = always exact). |
| `--triangle-samples N` | `10000` | Monte Carlo samples for clustering and transitivity when `m > M`. |
| `--max-vertices N` | `5000000` | Skip graphs above `N` vertices (`0` disables). |
| `--memory-limit-mb MB` | `4096` | Per-worker virtual memory cap (`RLIMIT_AS`, `0` disables). |
| `--timeout-s SEC` | `21600` | Per-graph wall-clock timeout in seconds (`0` disables). |
| `--max-per-series K` | `0` (`2` for SNAP) | Condense repetitive temporal snapshot series (`as-733`, `as-caida`, `oregon*`, `network*`) to `K` representatives before global clustering. |
| `--no-log-scale` | Off | Disable `log1p` scaling of heavy-tailed size/degree features before `StandardScaler`. |
| `--skip-edgeless` | Off (On for SNAP) | Skip graphs whose header lacks `n > 0` and `m > 0`. |
| `--use-all-if-fewer` | Off (On in presets) | Select all eligible graphs with a warning when fewer than `--n` exist. |
| `--only-select` | Off | Cluster directly from existing SQLite rows without computing new features. |
| `--csv FILE` | `None` | Export features, cluster IDs, and selection flags to CSV. |
| `--db FILE` / `--db-name NAME` | `datasets/<ds>/features.sqlite3` | Explicit SQLite path (removes `PROJECT_ROOT` requirement) or dataset folder name. |

Cache rows are reused only when `(mtime_ns, size, bfs_samples, seed, feature_version=3, exact_triangle_edges, triangle_samples)` matches. Deleted input files are ignored even under `--only-select`. Legacy Portuguese SQLite column names are migrated to English automatically on open.

---

## DIMACS `.col` format

Plain text (UTF-8, LF) representing simple, undirected, unweighted graphs:

```text
c optional comment
p edge 3 2
e 1 2
e 2 3
```

Vertices are numbered `1..n` with no self-loops and no duplicate undirected
edges. Isolated vertices are declared by `p edge n m` even if absent from `e`
lines.

---

## Known limitations

- **Converters continue on per-file errors**: `matrix_market.py` and `snap.py`
  log individual read/skip errors and still exit `0`; always check the final
  converted summary line.
- **Matrix Market memory & header repair**: `matrix_market.py` loads each
  matrix in memory with SciPy and infers missing/mismatched entry counts from
  available lines (cannot distinguish a corrupted header from a truncated file).
  Asymmetric matrices are skipped, not symmetrized.
- **SNAP edge lists & disk usage**: Edge lists cannot reveal isolated vertices
  absent from both records and `# Nodes:` metadata. Temporary SQLite files
  created in `--output` can require substantial disk space on multi-million-edge
  networks.
- **Isolation filter scope**: `isolated_vertices.py` validates syntax, endpoint
  bounds, and edge-line counts, but does not detect duplicate undirected edges
  or repair malformed files.
- **Platform portability**: Representative selection uses Unix `fork` and
  `resource.RLIMIT_AS` (use WSL on Windows). Cache validation is metadata-based
  (`mtime_ns` and `size`), not a content hash.
- **Legacy `Makefile`**: Kept from an earlier C implementation (`src/graph/`
  was removed) and unused by the Python workflows.
