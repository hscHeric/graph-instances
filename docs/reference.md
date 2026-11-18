# Command and parameter reference

[Back to the getting-started guide](../README.md).

This page covers dataset formats, model parameters, selection settings,
filtering behavior, and migration from older command names. Mise examples
are optional: the main guide shows the equivalent Python commands.

For direct `python scripts/...` examples on this page, activate the virtual
environment and set the project root from the repository directory first:

```bash
source .venv/bin/activate
export PROJECT_ROOT="$PWD"
```

The simpler `python graph_instances.py ...` entry point sets `PROJECT_ROOT`
automatically. No Python workflow requires mise.

- [Downloaded datasets](#downloaded-datasets)
- [Generator parameters](#generator-parameters)
- [Representative selection](#representative-selection)
- [Migration from older names](#english-naming)
- [Isolated-vertex filtering](#excluding-graphs-with-isolated-vertices)
- [DIMACS format](#dimacs-col-format)

## Downloaded datasets

### Harwell-Boeing

A classic sparse matrix collection available through SuiteSparse. Square,
symmetric matrices can represent undirected graphs. The scripts download
Matrix Market (`.mtx`) files and convert them to `.col`.

```bash
# With mise
mise run download::harwell_boeing
mise run convert::harwell_boeing

# Without mise
python scripts/download/download_harwell_boeing.py
python scripts/convert/matrix_market.py datasets/harwell_boeing/raw
```

### DIMACS

Instances used in graph coloring and maximum clique challenges. Files are
downloaded from Network Repository in Matrix Market format and converted to `.col`.

```bash
# With mise
mise run download::dimacs
mise run convert::dimacs

# Without mise
python scripts/download/download_dimacs.py
python scripts/convert/matrix_market.py datasets/dimacs/raw
```

### BHOSLIB

Dense, difficult graphs designed for maximum clique, independent set,
vertex cover, and graph coloring algorithms.

```bash
# With mise
mise run download::bhoslib
mise run convert::bhoslib

# Without mise
python scripts/download/download_bhoslib.py
python scripts/convert/matrix_market.py datasets/bhoslib/raw
```

### SNAP

Real-world networks from the Stanford Large Network Dataset Collection,
including social, citation, collaboration, web, road, autonomous system,
and temporal networks.

```bash
# With mise
mise run download::snap
mise run convert::snap

# Without mise
python scripts/download/download_snap.py
python scripts/convert/snap.py
```

The default download excludes the exceptionally large `com-Friendster` graph.
To include it:

```bash
mise run download::snap::full
python scripts/download/download_snap.py --include-friendster
```

The converter accepts `.txt`, `.csv`, `.tsv`, `.edge`, `.edges`, and `.edgelist`
edge lists, plus `wiki-RfA`, Wikipedia election records, MOOC, and
`comm-f2f-Resistance` temporal matrices.
Output goes to `datasets/snap/processed/`. Conversion ignores direction,
weights, signs, and timestamps, removes loops and duplicate edges, and
relabels vertices to `1..n`. Auxiliary attributes, labels, features, scripts,
readmes, and metadata are skipped. Conversion leaves source files unchanged.

Inspect file classification without converting:

```bash
mise run convert::snap -- --analyze
python scripts/convert/snap.py --analyze
```

The regular task also prints this classification before conversion.

### All datasets

```bash
mise run download::all
mise run convert::all
```

Without mise, run the desired downloaders above, then:

```bash
python scripts/convert/matrix_market.py
python scripts/convert/snap.py
```

### Download parameters

Downloaders require no positional arguments. They use `PROJECT_ROOT` to
locate `datasets/` and environment variables to control concurrency:

| Dataset | Environment variable | Default |
| --- | --- | ---: |
| Harwell-Boeing | `HARWELL_BOEING_DOWNLOAD_WORKERS` | 15 |
| DIMACS | `DIMACS_DOWNLOAD_WORKERS` | 78 |
| BHOSLIB | `BHOSLIB_DOWNLOAD_WORKERS` | 36 |
| SNAP | `SNAP_DOWNLOAD_WORKERS` | Number of download URLs |

```bash
SNAP_DOWNLOAD_WORKERS=20 mise run download::snap
SNAP_DOWNLOAD_WORKERS=20 python scripts/download/download_snap.py
```

The Matrix Market converter optionally accepts an `.mtx` file or directory.
Without an argument it searches all `datasets/*/raw/` directories:

```bash
python scripts/convert/matrix_market.py path/to/graph.mtx
python scripts/convert/matrix_market.py path/to/directory
python scripts/convert/matrix_market.py
```

## Synthetic graphs

Mise tasks provide presets and save results in `datasets/<model>/processed/`.
Override settings by passing arguments after `--`. Scripts also provide
defaults when run directly.

### Erdős-Rényi

Each pair of vertices receives an edge with probability `p`. By default,
`p = degree/(n-1)` is computed from a target average degree and capped at 1,
keeping graphs comparably sparse as size increases. Use `--p` for fixed probabilities.

```bash
mise run generate::erdos_renyi -- --n 100 250 500 --p 0.05 0.1 --reps 3 --seed 42 --out datasets/erdos_renyi/processed
python scripts/generate/erdos_renyi.py --n 100 250 500 --p 0.05 0.1 --reps 3 --seed 42 --out datasets/erdos_renyi/processed
```

### Barabási-Albert

A scale-free model based on preferential attachment. New vertices tend to
connect to vertices that already have high degree, forming hubs.

```bash
mise run generate::barabasi_albert -- --n 100 250 500 --m 2 4 --reps 3 --seed 42 --out datasets/barabasi_albert/processed
python scripts/generate/barabasi_albert.py --n 100 250 500 --m 2 4 --reps 3 --seed 42 --out datasets/barabasi_albert/processed
```

### Watts-Strogatz

A small-world model that starts from a regular ring and rewires edges with
probability `p-rewire`, combining local clustering with short paths.

```bash
mise run generate::watts_strogatz -- --n 100 250 500 --k 4 8 --p-rewire 0.1 --reps 3 --seed 42 --out datasets/watts_strogatz/processed
python scripts/generate/watts_strogatz.py --n 100 250 500 --k 4 8 --p-rewire 0.1 --reps 3 --seed 42 --out datasets/watts_strogatz/processed
```

### Cubic graphs

Random 3-regular graphs: every vertex has degree exactly 3. Vertex counts
must be even and greater than 3. Explicit sizes and geometric progressions
are both supported:

```bash
mise run generate::cubic -- --n 100 200 400 --reps 5 --seed 42 --out datasets/cubic/processed
python scripts/generate/cubic.py --n 100 200 400 --reps 5 --seed 42 --out datasets/cubic/processed
mise run generate::cubic -- --doubling 100 2 5 --reps 5 --seed 42 --out datasets/cubic/processed
python scripts/generate/cubic.py --doubling 100 2 5 --reps 5 --seed 42 --out datasets/cubic/processed
```

### Generator parameters

| Shared parameter | Meaning |
| --- | --- |
| `--vertices N [N ...]`, `--n N [N ...]` | Vertex counts; overrides `--doubling`. |
| `--doubling START FACTOR STEPS` | Geometric progression used when `--n` is omitted. |
| `--count REPETITIONS`, `--reps REPETITIONS` | Graphs per parameter combination. |
| `--seed SEED` | Initial seed, incremented after each graph. |
| `--out DIRECTORY` | Output directory for `.col` files. |
| `--remove-isolated` | Discard entire graphs containing any isolated vertex. |
| `-h`, `--help` | Show help without generating files. |

| Generator | Parameter | Meaning |
| --- | --- | --- |
| Erdős-Rényi | `--avg-degree G [G ...]` | Target average degree; computes `p = G/(n-1)`. |
| Erdős-Rényi | `--p P [P ...]` | Edge probability for each vertex pair. |
| Barabási-Albert | `--m M [M ...]` | Edges per new vertex; positive and less than `n`. |
| Watts-Strogatz | `--k K [K ...]` | Neighbors per vertex in the initial ring; positive and less than `n`. |
| Watts-Strogatz | `--p-rewire P [P ...]` | Rewiring probability between 0 and 1. |
| Cubic | `--n N [N ...]` | Even values greater than 3. |

For example, show help with:

```bash
mise run generate::erdos_renyi -- --help
python scripts/generate/erdos_renyi.py --help
```

### Generate all models with presets

```bash
mise run generate::all
```

| Script | Preset | Result |
| --- | --- | --- |
| `cubic.py` | `--doubling 200 1.15 30 --reps 1` | 30 graphs, from 200 to 11,516 vertices |
| `erdos_renyi.py` | `--doubling 200 1.15 30 --avg-degree 10.0 --reps 1` | 30 graphs with average degree near 10 |
| `barabasi_albert.py` | `--doubling 200 1.15 30 --m 4 --reps 1` | 30 graphs with average degree near 8 |
| `watts_strogatz.py` | `--doubling 200 1.15 30 --k 6 --p-rewire 0.1 --reps 1` | 30 graphs |

All presets use seed 42 and save to `datasets/<model>/processed/`. Without
`--out`, direct invocations use `random_cubic_graphs/`, `random_erdos_renyi/`,
`random_barabasi_albert/`, and `random_watts_strogatz/`, respectively.

## Representative selection

The selector clusters graphs by structural features and chooses the graph
closest to each cluster centroid. Its 16 topological features cover size,
density, degree statistics and entropy, clustering, transitivity,
assortativity, connected components, approximate diameter, and average path
length. No domination-specific heuristic participates in selection.

Each graph runs in an isolated subprocess with a memory limit and timeout.
Features are immediately stored in `datasets/<dataset>/features.sqlite3`,
allowing later runs to resume pending files. A graph's crash, out-of-memory
failure, or timeout does not terminate the main process. Only the small
feature matrix is loaded for k-means. Calculations use `float64`; continuous
features in CSV output and messages use six decimal places.

```bash
mise run select::snap                 # 20
mise run select::bhoslib              # 30
mise run select::harwell_boeing       # 50
mise run select::dimacs               # 80
mise run select::erdos_renyi          # 30
mise run select::barabasi_albert      # 30
mise run select::watts_strogatz       # 30
mise run select::cubic               # 30
mise run select::all
```

Override the preset size with `mise run select::snap -- --n 10`. Each task
reads `datasets/<dataset>/processed/` and replaces `.col` files in
`datasets/<dataset>/selected/` with the new selection. Feature calculation
reuses cached records only when the file's modification time and size, BFS
settings, seed, and feature version match. Failed records are retried on the
next normal run. Selection and requested CSV export run again even when the
output directory already contains the requested number of files. Deleted
input files are excluded from selection, including `--only-select` runs.

Without mise:

```bash
python scripts/select/representative_selection.py \
  --input-dir datasets/snap/processed \
  --n 30 \
  --out datasets/snap/selected
```

| Additional parameter | Meaning |
| --- | --- |
| `--seed SEED` | K-means and BFS sampling seed; default 42. |
| `--bfs-samples N` | BFS sources for diameter and path estimates; default 10. |
| `--csv FILE` | Save features, clusters, and selection indicators. |
| `--db FILE` | Explicit persistent database path. |
| `--db-name DATASET` | Save to `datasets/DATASET/features.sqlite3`. |
| `--max-vertices N` | Skip graphs above this limit; default 500,000; zero disables it. |
| `--memory-limit-mb MB` | Maximum memory per subprocess; default 4,096 MB. |
| `--timeout-s SECONDS` | Time limit per graph; default 21,600 seconds (6 hours); zero disables it. |
| `--only-select` | Select from the current database without computing features. |
| `--skip-edgeless` | Skip empty graphs and graphs with only isolated vertices. |
| `--use-all-if-fewer` | Use all available graphs when fewer than `--n` are available. |

Clustering and transitivity can consume substantial memory and time on
graphs with millions of vertices. Reducing `--bfs-samples` only reduces BFS costs.
The SNAP task enables `--skip-edgeless`. All preset selection tasks enable
`--use-all-if-fewer`: with 78 available DIMACS graphs, its task warns and
selects all 78; with at least 80, it selects exactly 80.

## English naming

Filenames, functions, variables, command-line options, help text, messages,
and CSV feature names use English.

| Previous option | English option |
| --- | --- |
| `--incluir-friendster` | `--include-friendster` |
| `--analisar` | `--analyze` |
| `--ignorar-sem-arestas` | `--skip-edgeless` |
| `--usar-todos-se-faltar` | `--use-all-if-fewer` |
| `--limite-memoria-mb` | `--memory-limit-mb` |
| `--so-selecionar` | `--only-select` |

Update existing commands to use the English options. Cubic tasks now use
`datasets/cubic/`; rename an existing `datasets/cubicos/` directory before
resuming those tasks. Existing feature databases are upgraded to English
column names when opened, preserving stored features and failures. Existing
CSV exports are unchanged; new exports use English headers. SNAP recognizes
old download completion markers and writes new markers under `.completed/`.
Normal selection recalculates legacy features without a source fingerprint.
`--only-select` intentionally uses stored values; run normal selection once
after upgrading to refresh older measurements.

## Excluding graphs with isolated vertices

An isolated vertex has no neighbor other than itself: a self-loop does not
make it eligible. The filter discards the **entire graph file**, preserving
the original graph semantics instead of deleting individual vertices.

```bash
# Filter while converting or generating
mise run convert -- snap --remove-isolated
mise run convert -- harwell-boeing --remove-isolated
mise run generate -- erdos-renyi --vertices 100 500 --remove-isolated

# Preview existing files, then delete affected .col files
mise run filter -- snap
mise run filter -- snap --remove-isolated
mise run filter -- all --remove-isolated

# Inspect a specific directory without mise
python scripts/filter/isolated_vertices.py datasets/snap/processed
python scripts/filter/isolated_vertices.py datasets/snap/processed --remove-isolated
```

The standalone filter reads one file at a time, line by line, with one bit
per declared vertex (roughly 12.5 MB for 100 million vertices). It counts
vertices appearing in non-loop edges and includes vertices declared by the
header that never appear in an edge. It checks endpoint bounds and the number
of edge lines before deleting anything. Malformed files are reported and
left untouched. It does not detect duplicate edges or repair existing graphs.

The entry point's `filter` command scans `processed/` and `selected/`, leaving
`raw/` and feature databases intact. Cached records for removed files are
ignored during selection. Directly passing a directory to the standalone
script scans all `.col` files under that directory.

SNAP conversion records vertices before removing loops, assigns contiguous
IDs, and uses SQLite for edges and vertex mappings. Matrix Market conversion
and generators filter before writing the result. Without the option, isolated
vertices are preserved. The option can delete an existing output with the
same name when that graph is rejected. It does not clean unrelated older
outputs; use `filter` to inspect an entire collection.

An edge list cannot reveal isolated vertices absent from both its records
and metadata. SNAP uses declared vertex counts when available, loop endpoints,
and temporal-matrix headers; it cannot recover vertices never supplied by
the source. Old SNAP conversions should be regenerated: the previous ID
allocation could emit endpoints outside the declared `1..n` range.

See [the code review](correctness-review.md) for corrected issues and
remaining limitations, including the scope of verification.

## DIMACS `.col` format

Files contain optional comments, a header with vertex and edge counts, and
one line per undirected edge. Vertices are numbered `1..n`; isolated vertices
are represented by the header even when they appear in no edge line.

```text
c graph description
p edge 3 2
e 1 2
e 2 3
```
