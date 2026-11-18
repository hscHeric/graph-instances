# graph-instances

Prepare graph files for your experiments: generate synthetic graphs or download
existing datasets, convert them to DIMACS `.col`, and optionally choose a
smaller representative collection.

The output represents **simple, undirected, unweighted graphs**. Use
`--remove-isolated` when your algorithm cannot accept isolated vertices.

**Mise is optional.** You can use every workflow with Python alone. Mise tasks
are shortcuts for the same scripts.

- [Install](#install)
- [Generate your first graphs](#generate-your-first-graphs)
- [Use an existing dataset](#use-an-existing-dataset)
- [Remove graphs with isolated vertices](#remove-graphs-with-isolated-vertices)
- [Choose a smaller collection](#choose-a-smaller-collection)
- [Use mise shortcuts](#use-mise-shortcuts)
- [Find your output files](#find-your-output-files)
- [Get help](#get-help)

## Install

Open a terminal in the `graph-instances` directory. If this repository is a
submodule inside another project, enter it first:

```bash
cd external/graph-instances
```

Choose **one** installation method. The shell examples below use Bash on Linux
or macOS. Representative selection currently requires Unix process features;
on Windows, use WSL for that workflow.

### Python only

You need Python with `pip` and `venv` available. Create a local environment
and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

In a new terminal, return to this directory and run
`source .venv/bin/activate` again. Use `python graph_instances.py ...` for
the commands below. The entry point sets project paths automatically;
you do not need to install mise or export `PROJECT_ROOT`.

### With mise

If you already use mise:

```bash
mise install
mise exec -- python -m pip install -r requirements.txt
```

Use the [mise shortcuts](#use-mise-shortcuts) for the same workflows.
`mise exec -- python graph_instances.py ...` also runs any Python example
below inside the configured environment.

## Generate your first graphs

Start with a small, explicit collection:

```bash
python graph_instances.py generate cubic --vertices 100 200 --count 3 --seed 42
```

This produces **six `.col` files**: three with 100 vertices and three with
200 vertices. Find them in `datasets/cubic/processed/`. Every vertex in a
cubic graph has exactly three neighbors. You can give these files directly
to your program; no conversion or selection step is required.

| Option | What it means |
| --- | --- |
| `--vertices 100 200` | Generate graphs at both sizes. |
| `--count 3` | Generate three graphs for each parameter combination. |
| `--seed 42` | Start from a fixed random seed for repeatable generation with the same dependencies. |
| `--remove-isolated` | Discard a graph if any vertex has no neighbor other than itself. |
| `--out PATH` | Override the default output directory. |

### Choose a model

| Model name | Graph structure | Main setting |
| --- | --- | --- |
| `cubic` | Every vertex has degree 3. | Use even vertex counts greater than 3. |
| `erdos-renyi` | Random edges with a target average degree or fixed probability. | `--avg-degree 10` or `--p 0.1` |
| `barabasi-albert` | Preferential attachment; some vertices become hubs. | `--m 4`: edges added per new vertex |
| `watts-strogatz` | A regular ring with randomly rewired edges. | `--k 6 --p-rewire 0.1` |

For example, generate sparse random graphs suitable for an algorithm that
does not accept isolated vertices:

```bash
python graph_instances.py generate erdos-renyi --vertices 100 500 --count 3 --avg-degree 10 --remove-isolated
```

This attempts six graphs. A rejected graph is **not replaced**, so the output
can contain fewer than six files. The command reports skipped graphs.

To generate every model with the same sizes:

```bash
python graph_instances.py generate all --vertices 100 200 --count 3 --remove-isolated
```

With `all`, use options shared by every model. Model-specific settings such as
`--m` belong in a command for that model. Omitting `--vertices` uses the larger
preset size progression; see the [parameter reference](docs/reference.md#generator-parameters).

## Use an existing dataset

Downloaded data needs two steps before it can be used: **download**, then
**convert**. Here is the complete workflow for SNAP:

```bash
python graph_instances.py download snap
python graph_instances.py convert snap --remove-isolated
```

The first command saves original files under `datasets/snap/raw/`. The second
writes `.col` files under `datasets/snap/processed/`, excluding graphs with
isolated vertices. Use the files in `processed/` as input to your program.

| Dataset name | What it contains |
| --- | --- |
| `snap` | Real-world social, web, road, citation, and other networks. Some are very large. |
| `dimacs` | Benchmark instances used in graph-coloring and clique challenges. |
| `bhoslib` | Difficult, dense benchmark graphs. |
| `harwell-boeing` | Sparse matrices; square, symmetric matrices are converted into graphs. |

Replace `snap` in both commands with another dataset name. Hyphens and
underscores are accepted in names, for example `harwell-boeing` and
`harwell_boeing`. Output folders use underscores.

For SNAP, conversion ignores direction, weights, signs, and timestamps,
removes loops and repeated undirected edges, and renumbers vertices to `1..n`.
Conversion leaves the original files unchanged. Read the final converted
count: unsupported files and conversion failures may be skipped.

SNAP excludes Friendster by default. Large collections can require substantial
download time and disk space. [Advanced download options](docs/reference.md#download-parameters)
cover concurrency and [the SNAP reference](docs/reference.md#snap) explains
how to include Friendster or preview file classification.

## Remove graphs with isolated vertices

There are two ways to do this:

| When | Command |
| --- | --- |
| While generating or converting | Add `--remove-isolated` to that command. |
| For `.col` files you already have | Use `filter`, then add `--remove-isolated` to apply deletion. |

First preview the affected SNAP files:

```bash
python graph_instances.py filter snap
```

To delete them:

```bash
python graph_instances.py filter snap --remove-isolated
```

Replace `snap` with `all` to cover every dataset and generated model.

**The entire graph file is deleted, not just the isolated vertices.** A vertex
with only self-loops counts as isolated because it has no other neighbor.
Vertices declared in the header but absent from the edge list also count.

The filter scans `processed/` and `selected/`; it leaves `raw/` intact. It
reads one file at a time and uses one bit per vertex, without loading the
edge set into memory. Malformed files are reported and left untouched.
This filter does not repair files or check for duplicate edges.

Filtering during generation/conversion only handles the outputs of that run.
Use `filter` to inspect older files too. See [filtering details](docs/reference.md#excluding-graphs-with-isolated-vertices)
for custom directories and source-data limitations.

## Choose a smaller collection

Selection is optional. Use it when you want a subset of graphs with varied
structural properties rather than the entire collection:

```bash
python graph_instances.py select snap --count 20
```

This reads `datasets/snap/processed/` and copies the chosen graphs to
`datasets/snap/selected/`. It leaves the processed collection intact, but
replaces the previous selection in `selected/`. If fewer than 20 eligible
graphs are available, it reports that and selects all available graphs.

To also save a table of features and selected graphs:

```bash
python graph_instances.py select snap --count 20 --csv datasets/snap/selection.csv
```

Feature calculations are cached for later runs. Selection is more expensive
than filtering: it uses NetworkX and, by default, skips graphs above 500,000
vertices. Its defaults also limit each worker to 4,096 MB and six hours.
See [selection options](docs/reference.md#representative-selection) before
changing these limits for large SNAP graphs.

## Use mise shortcuts

All Python workflows above also have optional mise shortcuts:

| Python | Mise |
| --- | --- |
| `python graph_instances.py generate cubic ...` | `mise run generate -- cubic ...` |
| `python graph_instances.py download snap` | `mise run download -- snap` |
| `python graph_instances.py convert snap ...` | `mise run convert -- snap ...` |
| `python graph_instances.py filter snap ...` | `mise run filter -- snap ...` |
| `python graph_instances.py select snap ...` | `mise run select -- snap ...` |

The `...` above stands for your options; do not type it. For a complete example:

```bash
mise run generate -- cubic --vertices 100 200 --count 3
```

Dataset-specific tasks are still available:

```bash
mise run generate::cubic -- --vertices 100 200 --count 3
mise run convert::snap -- --remove-isolated
mise run select::snap -- --count 20
mise run filter::isolated -- --remove-isolated
```

The first `--` belongs to mise: everything after it is passed to the script.
Use `mise tasks` to browse shortcuts. `generate::all`, `convert::all`, and
`select::all` apply shared options to all their datasets/models.

## Find your output files

```text
datasets/
  snap/
    raw/              Original downloaded files
    processed/        Converted .col files ready for your program
    selected/         Optional representative subset
    features.sqlite3  Cached measurements used by selection
  cubic/
    processed/        Generated .col files ready for your program
```

Generated graphs go straight to `processed/`. Downloaded graphs pass through
`raw/` first. The `datasets/` directory is excluded from Git.

Running generation again with the same sizes/settings can overwrite files
with the same names. Use a different `--out` directory to keep separate runs.
Generation and conversion do not clear unrelated files from previous runs.

The `.col` format is plain text. For example:

```text
c a path with three vertices
p edge 3 2
e 1 2
e 2 3
```

`p edge 3 2` declares three vertices and two edges. Each `e` line is one
undirected edge; vertices are numbered from 1 to the declared count.

## Get help

```bash
python graph_instances.py --help
python graph_instances.py generate erdos-renyi --help
python graph_instances.py select snap --help
```

| Situation | What to do |
| --- | --- |
| `ModuleNotFoundError` | Activate `.venv` and install `requirements.txt` using that environment's Python. |
| `PROJECT_ROOT is not set` | Use `python graph_instances.py ...`; it sets this automatically. Direct `scripts/...` commands may require `export PROJECT_ROOT="$PWD"`. |
| No processed directory or no `.col` files | Generate graphs first, or run both download and conversion. Check the reported output counts. |
| Fewer graphs than requested | Check for isolated-vertex exclusions, conversion errors, or selection resource limits. |
| An existing SNAP file has an ID outside `1..n` | Regenerate old conversions; an earlier converter could write invalid vertex IDs. |
| Selection is slow | Use fewer/smaller inputs or inspect the selection limits in the reference. Filtering alone does not calculate structural features. |

For complete options and older command aliases, see the
[command reference](docs/reference.md). For known limitations and the scope
of the code review, see the [correctness review](docs/correctness-review.md).
