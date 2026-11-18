#!/usr/bin/env python3
"""Convert SNAP edge lists to simple DIMACS .col graphs.

Scans a SNAP raw directory, classifies each file into one of seven supported
network formats (ignoring auxiliary metadata, labels, features, readmes, and
hidden bookkeeping files), normalizes endpoints to contiguous 1-based vertex
IDs via a temporary on-disk SQLite database, removes self-loops and duplicate
undirected edges, and writes simple unweighted DIMACS ``.col`` graphs.

Supported Formats:
    * ``whitespace``: Space/tab-separated ``.txt``, ``.edge``, ``.edges``,
      ``.edgelist`` files (ignoring ``#`` and ``%`` comments).
    * ``csv`` / ``tsv``: Delimited edge lists with automatic header detection.
    * ``mooc``: ``mooc_actions.tsv`` bipartite interactions (namespaced as
      ``user:<id>`` and ``target:<id>``).
    * ``wiki_rfa``: ``wiki-RfA.txt`` key-value ``SRC:``/``TGT:`` records.
    * ``wiki_elections``: ``wikiElec.ElecBs3.txt`` election/candidate/vote logs.
    * ``temporal_matrix``: ``network<N>.csv`` ``P<i>_TO_P<j>`` interaction matrices.

Environment Variables:
    PROJECT_ROOT: Required path to the repository root containing ``datasets/``
        (validated even when ``--input`` and ``--output`` are passed).

Notes:
    * Direction, weights, signs, and timestamps are discarded.
    * Temporary ``snap_*.sqlite3`` files are created inside ``--output`` and
      removed in ``finally``; large networks require substantial temporary disk.
    * Per-file conversion errors remove partial outputs and continue the batch
      without exiting nonzero; inspect the final summary line.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from metadata_utils import base_provenance, resolve_metadata_path, update_metadata


# Substrings identifying non-graph metadata, feature, label, or path files in SNAP archives.
AUXILIARY_TERMS = (
    "readme",
    "target",
    "feature",
    "label",
    "citing",
    "description",
    "article",
    "category",
    "page-name",
    "username",
    "network_list",
    "shortest-path-distance-matrix",
    "path_finished",
    "path_unfinished",
)


def classify_file(path: Path) -> str | None:
    """Identify the parser for a SNAP file or return ``None`` to ignore it.

    Evaluates hidden paths, auxiliary filename patterns, excluded extensions,
    special SNAP dataset filenames, and generic edge-list extensions in strict
    precedence order.

    Args:
        path: Candidate file path inside the SNAP input directory.

    Returns:
        One of ``"whitespace"``, ``"csv"``, ``"tsv"``, ``"mooc"``,
        ``"wiki_rfa"``, ``"wiki_elections"``, or ``"temporal_matrix"``; or
        ``None`` if the file should be ignored as auxiliary.
    """
    name = path.name.lower()
    if any(part.startswith(".") for part in path.parts):
        return None
    if name.startswith((".", "._")) or any(term in name for term in AUXILIARY_TERMS):
        return None
    if path.suffix.lower() in {".json", ".py", ".md"}:
        return None
    if name == "wiki-rfa.txt":
        return "wiki_rfa"
    if name == "wikielec.elecbs3.txt":
        return "wiki_elections"
    if re.fullmatch(r"network\d+\.csv", name):
        return "temporal_matrix"
    if name.endswith("_weighted.csv"):
        return None
    if name == "mooc_actions.tsv":
        return "mooc"
    if path.suffix.lower() in {".edge", ".edges", ".edgelist"}:
        return "whitespace"
    if path.suffix.lower() == ".csv":
        if "edge" in name or name.startswith("soc-sign-bitcoin"):
            return "csv"
        return None
    if path.suffix.lower() == ".tsv":
        if name == "links.tsv" or name.startswith("soc-reddithyperlinks"):
            return "tsv"
        return None
    if path.suffix.lower() == ".txt":
        return "whitespace"
    return None


def read_whitespace(path: Path) -> Iterator[tuple[str, str]]:
    """Yield endpoint pairs from a whitespace-delimited edge list.

    Skips blank lines and lines starting with ``#`` or ``%``, taking the first
    two whitespace-separated fields from every remaining line.

    Args:
        path: Input edge-list file path.

    Yields:
        ``(source, target)`` vertex identifier strings.
    """
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            text = line.strip()
            if not text or text.startswith(("#", "%")):
                continue
            fields = text.split()
            if len(fields) >= 2:
                yield fields[0], fields[1]


def read_delimited(path: Path, delimiter: str) -> Iterator[tuple[str, str]]:
    """Yield endpoint pairs from a CSV or TSV edge list with header detection.

    Skips blank lines and ``#`` comments, inspects the first row against known
    header column names, and yields the stripped first two columns of each data
    row.

    Args:
        path: Input delimited file path.
        delimiter: Field separator character (e.g., ``","`` or ``"\\t"``).

    Yields:
        ``(source, target)`` vertex identifier strings.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as file:
        lines = (line for line in file if line.strip() and not line.startswith("#"))
        reader = csv.reader(lines, delimiter=delimiter)
        first = next(reader, None)
        if first is None or len(first) < 2:
            return

        header = {field.strip().lower() for field in first}
        has_header = bool(
            header
            & {
                "source_subreddit", "target_subreddit", "source", "target",
                "id_1", "id_2", "id1", "id2", "node_1", "node_2",
                "numeric_id_1", "numeric_id_2",
            }
        )
        if not has_header:
            yield first[0].strip(), first[1].strip()

        for fields in reader:
            if len(fields) >= 2:
                yield fields[0].strip(), fields[1].strip()


def read_mooc(path: Path) -> Iterator[tuple[str, str]]:
    """Yield bipartite user-to-target edges from ``mooc_actions.tsv``.

    Prefixes vertex names with ``user:`` and ``target:`` so identical numeric
    IDs across the two partitions remain distinct vertices.

    Args:
        path: Path to ``mooc_actions.tsv``.

    Yields:
        ``("user:<USERID>", "target:<TARGETID>")`` pairs.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for line in reader:
            yield f"user:{line['USERID']}", f"target:{line['TARGETID']}"


def read_wiki_rfa(path: Path) -> Iterator[tuple[str, str]]:
    """Yield voter-to-candidate edges from Wikipedia RfA key-value records.

    Pairs each ``SRC:`` line with the next ``TGT:`` line.

    Args:
        path: Path to ``wiki-RfA.txt``.

    Yields:
        ``(source, target)`` username strings.
    """
    source: str | None = None
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            if line.startswith("SRC:"):
                source = line[4:].strip()
            elif line.startswith("TGT:") and source is not None:
                yield source, line[4:].strip()
                source = None


def read_temporal_matrix(path: Path) -> Iterator[tuple[str, str]]:
    """Aggregate temporal ``P<i>_TO_P<j>`` interaction matrices into one graph.

    Emits marker self-loops ``(vertex, vertex)`` for every participant declared
    in the CSV header so inactive participants are registered before loops are
    stripped, then yields each undirected pair that has a non-zero value at any
    timestep.

    Args:
        path: Path to a ``network<N>.csv`` temporal matrix file.

    Yields:
        Participant marker self-loops followed by unique undirected edges.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as file:
        reader = csv.reader(file)
        header = next(reader, [])
        columns: list[tuple[int, str, str]] = []
        vertices: set[str] = set()
        for index, name in enumerate(header):
            match = re.fullmatch(r"P(\d+)_TO_P(\d+)", name)
            if match:
                source, target = f"P{match.group(1)}", f"P{match.group(2)}"
                vertices.update((source, target))
                if source != target:
                    columns.append((index, source, target))

        # Register all known vertices; conversion removes these marker loops.
        for vertex in sorted(vertices):
            yield vertex, vertex

        seen: set[tuple[str, str]] = set()
        for line in reader:
            for index, source, target in columns:
                if index < len(line) and float(line[index]) != 0:
                    edge = tuple(sorted((source, target)))
                    if edge not in seen:
                        seen.add(edge)
                        yield edge


def read_wiki_elections(path: Path) -> Iterator[tuple[str, str]]:
    """Read voter-to-candidate edges from SNAP Wikipedia election records.

    Tracks election boundaries (``E``), candidate records (``U``, emitted as
    marker self-loops so candidates with zero votes are retained), and vote
    records (``V``).

    Args:
        path: Path to ``wikiElec.ElecBs3.txt``.

    Yields:
        Candidate marker self-loops and ``(voter_id, candidate_id)`` pairs.

    Raises:
        ValueError: If a vote record appears without an active candidate or
            fewer than three whitespace-separated fields.
    """
    candidate: str | None = None
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            fields = line.split()
            if not fields or fields[0].startswith("#"):
                continue
            record = fields[0].rstrip(":")
            if record == "E":
                candidate = None
            elif record == "U":
                candidate = fields[1]
                yield candidate, candidate
            elif record == "V":
                if candidate is None or len(fields) < 3:
                    raise ValueError("Election vote without a candidate or voter")
                yield fields[2], candidate


def extract_edges(path: Path, file_format: str) -> Iterable[tuple[str, str]]:
    """Dispatch a SNAP input file to the parser matching ``file_format``.

    Args:
        path: Path to the source SNAP file.
        file_format: Format identifier returned by :func:`classify_file`.

    Returns:
        Iterable of raw ``(source, target)`` vertex identifier pairs.

    Raises:
        ValueError: If ``file_format`` is not recognized.
    """
    if file_format == "whitespace":
        return read_whitespace(path)
    if file_format == "csv":
        return read_delimited(path, ",")
    if file_format == "tsv":
        return read_delimited(path, "\t")
    if file_format == "mooc":
        return read_mooc(path)
    if file_format == "wiki_rfa":
        return read_wiki_rfa(path)
    if file_format == "wiki_elections":
        return read_wiki_elections(path)
    if file_format == "temporal_matrix":
        return read_temporal_matrix(path)
    raise ValueError(f"Unknown SNAP format: {file_format}")


def declared_vertex_count(path: Path) -> int | None:
    """Extract the ``# Nodes: N`` metadata count from the top of a ``.txt`` file.

    Inspects at most the first 20 lines of ``.txt`` files. Non-``.txt`` files
    return ``None`` immediately.

    Args:
        path: Path to the source SNAP file.

    Returns:
        Declared node count if present in the first 20 lines; otherwise ``None``.
    """
    if path.suffix.lower() != ".txt":
        return None
    pattern = re.compile(r"#\s*Nodes:\s*(\d+)", re.IGNORECASE)
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for _ in range(20):
            line = file.readline()
            if not line:
                break
            match = pattern.search(line)
            if match:
                return int(match.group(1))
    return None


def convert_file(
    path: Path, output: Path, file_format: str, remove_isolated: bool = False
) -> str:
    """Convert one SNAP file to DIMACS ``.col`` using a temporary SQLite store.

    Streams canonicalized undirected pairs into an on-disk SQLite database in
    50,000-row batches, registers all observed vertices prior to deleting
    self-loops, reconciles against ``# Nodes:`` metadata, optionally discards
    graphs with isolated vertices, and writes sorted 1-based edges.

    Args:
        path: Source SNAP file path.
        output: Directory receiving ``<stem>.col`` and the temporary SQLite DB.
        file_format: Parser name from :func:`classify_file`.
        remove_isolated: If ``True``, discard the entire graph (and remove any
            existing output file) when any vertex has no non-loop incident edge.

    Returns:
        Outcome status code: ``"ok"``, ``"isolated"``, or ``"failed"``.
    """
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{path.stem}.col"
    database_path: Path | None = None
    connection: sqlite3.Connection | None = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix="snap_", suffix=".sqlite3", dir=output, delete=False
        ) as database:
            database_path = Path(database.name)

        connection = sqlite3.connect(database_path)
        # Sacrifice crash durability for throughput on a disposable per-file DB,
        # while keeping temporary B-trees on disk and capping page cache to ~16 MiB.
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA cache_size=-16384")
        connection.execute("CREATE TABLE edges (u TEXT, v TEXT, PRIMARY KEY (u, v)) WITHOUT ROWID")

        batch: list[tuple[str, str]] = []
        for source, target in extract_edges(path, file_format):
            if not source or not target:
                continue
            u, v = sorted((source, target))
            batch.append((u, v))
            if len(batch) == 50_000:
                connection.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?)", batch)
                batch.clear()
        if batch:
            connection.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?)", batch)
        connection.commit()

        connection.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY, original TEXT UNIQUE)"
        )
        # UNION inserts each vertex once, producing contiguous IDs 1..n.
        # Register endpoints before removing loops so loop-only vertices survive.
        connection.execute(
            "INSERT INTO nodes(original) SELECT u FROM edges UNION SELECT v FROM edges"
        )
        connection.execute("DELETE FROM edges WHERE u = v")
        observed_vertices = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        declared_vertices = declared_vertex_count(path)
        total_vertices = max(observed_vertices, declared_vertices or 0)
        total_edges = connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        if remove_isolated:
            active_vertices = connection.execute(
                "SELECT COUNT(*) FROM (SELECT u FROM edges UNION SELECT v FROM edges)"
            ).fetchone()[0]
            isolated_vertices = total_vertices - active_vertices
            if isolated_vertices:
                destination.unlink(missing_ok=True)
                print(
                    f"REMOVED/SKIPPED: {path.name}: {isolated_vertices} isolated vertices"
                )
                return "isolated"

        with destination.open("w", encoding="utf-8", newline="\n") as file:
            file.write(f"c Converted from {path.name}; direction, weights, and time ignored\n")
            file.write(f"p edge {total_vertices} {total_edges}\n")
            query = """
                SELECT nu.id, nv.id
                FROM edges e
                JOIN nodes nu ON nu.original = e.u
                JOIN nodes nv ON nv.original = e.v
                ORDER BY nu.id, nv.id
            """
            for u, v in connection.execute(query):
                file.write(f"e {u} {v}\n")

        print(f"OK: {path.name} -> {total_vertices} vertices, {total_edges} edges")
        return "ok"
    except Exception as error:
        destination.unlink(missing_ok=True)
        print(f"FAILED: {path}: {error}")
        return "failed"
    finally:
        if connection is not None:
            connection.close()
        if database_path is not None:
            database_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    """Validate ``PROJECT_ROOT`` and parse SNAP converter CLI arguments.

    Returns:
        Parsed CLI arguments containing ``input``, ``output``, ``analyze``,
        and ``remove_isolated``.

    Raises:
        RuntimeError: If ``PROJECT_ROOT`` is unset.
    """
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError("PROJECT_ROOT is not set.")
    root = Path(project_root) / "datasets" / "snap"

    parser = argparse.ArgumentParser(description="Convert SNAP graphs to DIMACS .col")
    parser.add_argument("--input", type=Path, default=root / "raw", help="SNAP input directory")
    parser.add_argument("--output", type=Path, default=root / "processed", help="DIMACS output directory")
    parser.add_argument("--analyze", action="store_true", help="Only classify files")
    parser.add_argument(
        "--remove-isolated", action="store_true",
        help="Discard the entire output graph if any vertex has no non-loop edge",
    )
    return parser.parse_args()


def main() -> None:
    """Classify SNAP raw files and convert all supported graphs to ``.col``.

    Raises:
        RuntimeError: If ``PROJECT_ROOT`` is unset.
        ValueError: If multiple selected inputs map to the same ``<stem>.col``.
    """
    args = parse_args()
    files = sorted(path for path in args.input.rglob("*") if path.is_file())
    selected = [(path, file_format) for path in files if (file_format := classify_file(path))]
    ignored = len(files) - len(selected)
    print(f"Found: {len(selected)} graphs; {ignored} auxiliary files ignored")

    selected_paths = {path for path, _ in selected}
    for path, file_format in selected:
        print(f"KEEP [{file_format}]: {path}")
    for path in files:
        if path not in selected_paths:
            print(f"IGNORE [auxiliary]: {path}")

    if args.analyze:
        print("Analysis complete; no files were converted.")
        return

    output_names: set[str] = set()
    for path, _ in selected:
        name = f"{path.stem}.col"
        if name in output_names:
            raise ValueError(f"Multiple input files would overwrite {name}")
        output_names.add(name)

    counts = {"ok": 0, "isolated": 0, "failed": 0}
    format_counts: dict[str, int] = {}
    excluded: list[dict[str, str]] = []
    for path, file_format in selected:
        format_counts[file_format] = format_counts.get(file_format, 0) + 1
        status = convert_file(path, args.output, file_format, args.remove_isolated)
        counts[status] += 1
        if status != "ok":
            excluded.append({"file": path.name, "format": file_format, "reason": status})

    converted = counts["ok"]
    metadata_path = update_metadata(
        resolve_metadata_path(args.output),
        "conversion",
        {
            **base_provenance(),
            "format": "snap",
            "input_path": str(args.input),
            "output_directory": str(args.output),
            "parameters": {"remove_isolated": args.remove_isolated},
            "summary": {
                "total_files": len(files),
                "supported_graphs": len(selected),
                "ignored_auxiliary": ignored,
                "converted": converted,
                "skipped_isolated": counts["isolated"],
                "failed": counts["failed"],
                "formats": dict(sorted(format_counts.items())),
            },
            "excluded": excluded,
        },
    )
    print(f"Completed: {converted} of {len(selected)} graphs converted")
    print(f"Metadata saved to {metadata_path}")


if __name__ == "__main__":
    main()
