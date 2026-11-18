# Correctness and usability review

## Scope and verification

Reviewed the four downloaders, two converters, four generators, representative
selector, mise tasks, README, new command dispatcher, and isolation filter.
The final changes received static code review only, as requested. Small local
checks were performed earlier, before the instruction to stop testing; those
checks do not validate the final implementation. No full collection was
downloaded, generated, converted, filtered, or selected during this review.

## Format decision

DIMACS `.col` is suitable for simple, undirected, unweighted graphs. A header
`p edge n m` declares vertices `1..n`, including isolated vertices, followed
by one `e u v` record per undirected edge. The `.col` extension does not
restrict the file to graph-coloring algorithms. The format is used by the
[DIMACS graph-coloring instance collection](https://mat.tepper.cmu.edu/COLOR/instances.html).

The required invariants are: no self-loops, no repeated undirected edges,
valid endpoint IDs, and a correct edge count. Excluding isolated vertices is
an additional application requirement, not part of the definition of a
simple graph. Removing an entire unsuitable instance preserves the meaning
of the remaining instances; removing its isolated vertices would change it.

## Corrections made

| Area | Problem and change |
| --- | --- |
| SNAP vertex IDs | `AUTOINCREMENT` combined with ignored duplicate insertions left ID gaps. The converter now inserts a union of unique vertex names once, producing contiguous IDs. |
| SNAP loop-only vertices | Loop endpoints disappeared before vertex registration. Vertices are now registered before loops are deleted. Declared vertex counts and temporal matrix participants are preserved. |
| SNAP election data | `wikiElec.ElecBs3.txt` was interpreted as ordinary edge pairs. A dedicated reader now maps voters to candidates. The [official record format](https://snap.stanford.edu/data/wiki-Elec.html) distinguishes elections, candidates, nominators, and votes. |
| Isolation filtering | The old SNAP cleanup deleted edgeless graphs unconditionally and did not detect graphs containing both edges and isolated vertices. It was replaced with optional whole-graph exclusion in converters/generators and a streaming filter for existing `.col` files. |
| Output collisions | Converters now reject multiple inputs with the same output filename instead of silently overwriting them. |
| DIMACS selection input | The selector now rejects loops, duplicate undirected edges, out-of-range IDs, missing or repeated headers, unknown records, and edge-count mismatches. |
| Distance features | Mean distance previously included each source's zero distance to itself in its divisor. It now averages distances to other vertices in the largest component. |
| Empty graphs | Feature extraction no longer divides by zero while computing mean clustering for an empty graph. |
| Worker crashes | An EOF from a crashed worker is handled as a graph failure instead of escaping into the main process. |
| Selection cache | Cache reuse now checks modification time, size, BFS settings, seed, and feature version. Failed files are retried; removed files cannot be selected from stale cached rows. |
| Selection output | Existing file counts no longer bypass selection or requested CSV exports. Input and output directories must differ. |
| Generator parameters | Geometric progressions reject nonfinite, fractional start/step counts, and invalid ranges instead of silently truncating or producing zero steps. |
| SNAP extraction | Gzip output is published only after successful extraction. Existing output without a completion marker no longer proves completion. |
| Download reruns | Identical DIMACS/BHOSLIB files are reused when flattening repeated downloads; conflicting files still raise an error. Download failures return a nonzero exit status. |
| English naming | Identifiers, CLI options, messages, and documentation are in English. Legacy database columns are migrated, and old SNAP completion markers remain recognizable. |

## Large graphs and remaining limitations

- The standalone isolation filter uses one bit per declared vertex and reads
  one edge line at a time. It does not build a NetworkX graph or keep an edge
  set in memory. It validates syntax, endpoint bounds, and edge-line counts;
  duplicate-edge detection and repair are outside this filter's scope.
- SNAP conversion stores deduplicated edges and vertex mappings in SQLite,
  with bounded Python batches and disk-backed temporary storage. Disk usage
  and conversion time can still be large. It is not a constant-disk operation.
- A source edge list cannot identify vertices absent from both its records
  and metadata. The converter preserves known vertices but cannot infer
  unknown isolated vertices. Filename-based SNAP classification is still a
  heuristic; this review does not certify every downloaded archive/member.
- Matrix Market conversion still loads matrices with SciPy and builds a
  Python edge set. It is not a streaming converter for arbitrarily large
  matrices. It accepts square, numerically symmetric matrices; asymmetric
  matrices are skipped rather than automatically symmetrized.
- Matrix Market header repair infers the entry count from the available
  records. It cannot distinguish incorrect metadata from a truncated source.
  Source checksums/provenance must be checked separately when this matters.
- Converters still report some per-file errors and skips while continuing the
  batch; their final converted count must be checked. A zero command exit
  status alone does not certify successful conversion of every input.
- Generators and feature extraction still use NetworkX in memory. Selection
  defaults to at most 500,000 vertices per graph, a 4,096 MB subprocess limit,
  and a six-hour timeout. Large SNAP instances can be converted/filtered
  without being eligible for default representative selection.
- Cache fingerprints are metadata-based, not content hashes. An external
  rewrite preserving both file size and modification time can evade refresh.
  `--only-select` deliberately trusts stored feature values; use a normal
  selection run after upgrading or changing source graphs.
- Existing SNAP `.col` files are not repaired automatically. Regenerate them
  to replace invalid legacy vertex IDs before filtering or selection. The
  filter leaves malformed files untouched and reports them.
- Download availability, all remote file variants, platform portability,
  package installation on every Python version, and peak memory usage were
  not validated by execution. The selector's `fork`/`resource` isolation
  remains Unix-specific.

## Usability changes

`graph_instances.py` provides `download`, `convert`, `generate`, `select`, and
`filter` commands with consistent dataset names and automatic default paths.
`--vertices` and `--count` clarify generator parameters. Existing mise tasks
remain, with shorter `mise run generate -- MODEL ...`-style shortcuts added.
Aggregate generation, conversion, and selection tasks forward options to
every child script. Filtering existing collections previews by default and
deletes only when `--remove-isolated` is present. The README starts with a
short end-to-end guide and explains when graphs are skipped rather than replaced.
