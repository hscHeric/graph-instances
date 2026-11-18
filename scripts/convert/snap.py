#!/usr/bin/env python3
"""Convert SNAP edge lists to simple DIMACS .col graphs."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path


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
    """Identify the appropriate parser or return None for auxiliary files."""
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
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            text = line.strip()
            if not text or text.startswith(("#", "%")):
                continue
            fields = text.split()
            if len(fields) >= 2:
                yield fields[0], fields[1]


def read_delimited(path: Path, delimiter: str) -> Iterator[tuple[str, str]]:
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
    with path.open("r", encoding="utf-8", errors="replace", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for line in reader:
            yield f"user:{line['USERID']}", f"target:{line['TARGETID']}"


def read_wiki_rfa(path: Path) -> Iterator[tuple[str, str]]:
    source: str | None = None
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            if line.startswith("SRC:"):
                source = line[4:].strip()
            elif line.startswith("TGT:") and source is not None:
                yield source, line[4:].strip()
                source = None


def read_temporal_matrix(path: Path) -> Iterator[tuple[str, str]]:
    """Aggregate connections observed at any time into one graph."""
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
    """Read voter-to-candidate edges from SNAP election records."""
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
) -> bool:
    """Convert a file using SQLite to limit memory usage."""
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
                return False

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
        return True
    except Exception as error:
        destination.unlink(missing_ok=True)
        print(f"FAILED: {path}: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()
        if database_path is not None:
            database_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
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

    converted = sum(
        convert_file(path, args.output, file_format, args.remove_isolated)
        for path, file_format in selected
    )
    print(f"Completed: {converted} of {len(selected)} graphs converted")


if __name__ == "__main__":
    main()
