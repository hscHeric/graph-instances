"""Convert Matrix Market matrices to DIMACS .col graphs."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import scipy.io
import scipy.sparse


def read_matrix(mtx_path: Path):
    """Read a matrix, correcting inconsistent coordinate metadata in a copy."""
    with mtx_path.open("r", encoding="utf-8") as file:
        banner = file.readline()
        if not banner.lower().startswith("%%matrixmarket matrix coordinate"):
            return scipy.io.mmread(mtx_path, spmatrix=True)

        dimensions_line_number = 1
        for line in file:
            dimensions_line_number += 1
            text = line.strip()
            if text and not text.startswith("%"):
                dimensions = text.split()
                break
        else:
            raise ValueError("Matrix Market header has no dimensions")

        if len(dimensions) == 1:
            n_rows = n_columns = int(dimensions[0])
        elif len(dimensions) >= 2:
            n_rows, n_columns = map(int, dimensions[:2])
        else:
            raise ValueError("invalid dimensions in Matrix Market header")

        actual_entries = sum(
            1
            for line in file
            if line.strip() and not line.lstrip().startswith("%")
        )

    corrected_header = f"{n_rows} {n_columns} {actual_entries}"
    if len(dimensions) >= 3 and dimensions[:3] == corrected_header.split():
        return scipy.io.mmread(mtx_path, spmatrix=True)

    original_header = " ".join(dimensions)
    print(
        f"CORRECTING header for {mtx_path.name}: "
        f"'{original_header}' -> '{corrected_header}'"
    )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".mtx", delete=False
        ) as temporary, mtx_path.open("r", encoding="utf-8") as source:
            temporary_path = Path(temporary.name)
            for line_number, line in enumerate(source, start=1):
                if line_number == dimensions_line_number:
                    temporary.write(f"{corrected_header}\n")
                else:
                    temporary.write(line)

        return scipy.io.mmread(temporary_path, spmatrix=True)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def convert_file(
    mtx_path: Path, output_directory: Path, remove_isolated: bool = False
) -> bool:
    """Convert a square symmetric matrix to an undirected graph."""
    col_path = output_directory / f"{mtx_path.stem}.col"

    try:
        matrix = read_matrix(mtx_path)
    except Exception as error:
        print(f"FAILED to read {mtx_path}: {error}")
        return False

    n_rows, n_columns = matrix.shape
    if n_rows != n_columns:
        print(
            f"SKIPPED {mtx_path.stem}: non-square matrix "
            f"({n_rows}x{n_columns}), does not represent a graph"
        )
        return False

    csr_matrix = scipy.sparse.csr_matrix(matrix)
    csr_matrix.eliminate_zeros()

    if (csr_matrix != csr_matrix.transpose()).nnz != 0:
        print(
            f"SKIPPED {mtx_path.stem}: asymmetric matrix, "
            "does not represent an undirected graph"
        )
        return False

    csr_matrix.setdiag(0)
    csr_matrix.eliminate_zeros()
    if remove_isolated and (csr_matrix.getnnz(axis=1) == 0).any():
        col_path.unlink(missing_ok=True)
        print(f"REMOVED/SKIPPED: {mtx_path.name}: isolated vertices")
        return False

    coo_matrix = csr_matrix.tocoo()
    edges: set[tuple[int, int]] = set()

    for line, column, value in zip(
        coo_matrix.row, coo_matrix.col, coo_matrix.data
    ):
        if line == column or value == 0:
            continue

        u, v = sorted((int(line) + 1, int(column) + 1))
        edges.add((u, v))

    output_directory.mkdir(parents=True, exist_ok=True)
    with col_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(
            f"c Automatically converted from {mtx_path.name}\n"
        )
        file.write(f"p edge {n_rows} {len(edges)}\n")
        for u, v in sorted(edges):
            file.write(f"e {u} {v}\n")

    print(
        f"OK: {mtx_path.stem} -> {n_rows} vertices, "
        f"{len(edges)} edges -> {col_path}"
    )
    return True


def find_matrices(input_path: Path) -> list[Path]:
    """Recursively find .mtx files in the input path."""
    if input_path.is_dir():
        return sorted(input_path.rglob("*.mtx"))
    if input_path.is_file() and input_path.suffix.lower() == ".mtx":
        return [input_path]
    raise ValueError(f"Input must be an .mtx file or a directory: {input_path}")


def find_datasets(datasets: Path) -> list[tuple[Path, Path]]:
    """Find raw and processed directories for all datasets."""
    return [
        (raw, raw.parent / "processed")
        for raw in sorted(datasets.glob("*/raw"))
        if raw.is_dir()
    ]


def convert_dataset(
    input_path: Path, output_directory: Path, remove_isolated: bool = False
) -> tuple[int, int]:
    """Convert all Matrix Market files in a dataset."""
    matrices = find_matrices(input_path)
    if not matrices:
        print(f"No .mtx files found in {input_path}")
        return 0, 0

    names: set[str] = set()
    for path in matrices:
        if path.stem in names:
            raise ValueError(f"Multiple matrices would overwrite {path.stem}.col")
        names.add(path.stem)

    print(f"Converting {len(matrices)} matrices from {input_path} to {output_directory}")
    converted = sum(
        convert_file(file_path, output_directory, remove_isolated) for file_path in matrices
    )
    return converted, len(matrices)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Matrix Market file or directory")
    parser.add_argument(
        "--remove-isolated", action="store_true",
        help="Discard the entire output graph if any vertex has no non-loop edge",
    )
    args = parser.parse_args()
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set. Run this script through mise or "
            "set the environment variable explicitly."
        )

    datasets = Path(project_root) / "datasets"
    if args.input is not None:
        input_path = args.input
        base_directory = input_path if input_path.is_dir() else input_path.parent
        output_directory = (
            base_directory.parent / "processed"
            if base_directory.name == "raw"
            else base_directory / "processed"
        )
        datasets_to_convert = [(input_path, output_directory)]
    else:
        datasets_to_convert = find_datasets(datasets)

    if not datasets_to_convert:
        raise RuntimeError(f"No dataset with a raw directory found in {datasets}")

    total_converted = 0
    total_matrices = 0
    for input_path, output_directory in datasets_to_convert:
        converted, found = convert_dataset(input_path, output_directory, args.remove_isolated)
        total_converted += converted
        total_matrices += found

    if total_matrices == 0:
        raise RuntimeError("No .mtx files were found in the datasets.")

    print(
        f"Completed: {total_converted} of {total_matrices} matrices converted."
    )


if __name__ == "__main__":
    main()
