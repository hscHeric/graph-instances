#!/usr/bin/env python3
"""Download the Harwell-Boeing collection from SuiteSparse.

Queries the SuiteSparse Matrix Collection (``group="HB"``) through ``ssgetpy``,
downloads missing Matrix Market (``.mtx``) archives concurrently into
``$PROJECT_ROOT/datasets/harwell_boeing/raw/``, and flattens nested ``.mtx``
files into the raw directory root.

Environment Variables:
    PROJECT_ROOT: Required path to the repository root containing ``datasets/``.
    HARWELL_BOEING_DOWNLOAD_WORKERS: Optional positive integer controlling
        concurrent download threads (default: ``15``).

Notes:
    * Existing flat ``<matrix.name>.mtx`` files are skipped without checksum
      verification; any download exception propagates immediately.
    * Not all Harwell-Boeing matrices are square and symmetric; eligibility is
      filtered later by ``scripts/convert/matrix_market.py``.
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import move

def download_matrix(matrix, destination: Path) -> bool:
    """Download and extract a SuiteSparse matrix if its ``.mtx`` is absent.

    Args:
        matrix: SuiteSparse ``Matrix`` descriptor returned by ``ssgetpy``.
        destination: Raw dataset directory where the matrix is stored.

    Returns:
        ``True`` if the matrix was downloaded; ``False`` if
        ``<destination>/<matrix.name>.mtx`` already existed.
    """
    if (destination / f"{matrix.name}.mtx").exists():
        return False

    matrix.download(format="MM", destpath=str(destination), extract=True)
    return True


def download_matrices_in_parallel(matrices, destination: Path, workers: int) -> None:
    """Download and extract a collection of matrices concurrently.

    Args:
        matrices: Iterable of SuiteSparse ``Matrix`` descriptors.
        destination: Target raw dataset directory.
        workers: Maximum number of concurrent worker threads.

    Raises:
        Exception: Propagates any download or extraction error raised by
            ``ssgetpy`` in a worker thread.
    """
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(download_matrix, matrix, destination): matrix
            for matrix in matrices
        }

        total = len(downloads)
        for completed, future in enumerate(as_completed(downloads), start=1):
            matrix = downloads[future]
            downloaded = future.result()
            status = "OK" if downloaded else "ALREADY EXISTS"
            print(f"  [{completed}/{total}] {status}: {matrix.name}")


def move_matrices_to_root(destination: Path) -> int:
    """Move nested ``.mtx`` files into the raw directory root.

    Removes subdirectories left empty after moving matrices. Non-``.mtx``
    files are left in place.

    Args:
        destination: Raw dataset root directory.

    Returns:
        Number of ``.mtx`` files moved into ``destination``.

    Raises:
        FileExistsError: If a destination ``.mtx`` file already exists.
    """
    moved_files = 0

    for source in destination.rglob("*.mtx"):
        if source.parent == destination:
            continue

        target = destination / source.name
        if target.exists():
            raise FileExistsError(
                f"Could not move {source}: file {target} already exists."
            )

        move(source, target)
        moved_files += 1

    # Remove only directories left empty after moving files.
    directories = sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass

    return moved_files


def main() -> None:
    """Query SuiteSparse for the Harwell-Boeing group, download, and flatten.

    Imports ``ssgetpy`` after argument parsing so ``--help`` works without
    initializing the library. Reads ``PROJECT_ROOT`` (required) and
    ``HARWELL_BOEING_DOWNLOAD_WORKERS`` (optional positive integer, default
    ``15``) from the environment.

    Raises:
        RuntimeError: If ``PROJECT_ROOT`` is unset or SuiteSparse returns no
            Harwell-Boeing matrices.
        ValueError: If ``HARWELL_BOEING_DOWNLOAD_WORKERS`` is invalid.
        FileExistsError: If flattening collides with an existing ``.mtx`` file.
    """
    argparse.ArgumentParser(
        description="Download the Harwell-Boeing collection from SuiteSparse."
    ).parse_args()
    # Defer ssgetpy import so --help succeeds without initializing SuiteSparse metadata.
    import ssgetpy

    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set. Run this script through mise or "
            "set the environment variable explicitly."
        )

    destination = Path(project_root) / "datasets" / "harwell_boeing" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

    matrices = ssgetpy.search(group="HB", limit=10_000)
    if not matrices:
        raise RuntimeError("No matrices were found in the Harwell-Boeing collection.")

    try:
        workers = int(os.environ.get("HARWELL_BOEING_DOWNLOAD_WORKERS", "15"))
    except ValueError as error:
        raise ValueError(
            "HARWELL_BOEING_DOWNLOAD_WORKERS must be an integer."
        ) from error
    if workers < 1:
        raise ValueError(
            "HARWELL_BOEING_DOWNLOAD_WORKERS must be at least 1."
        )

    print(
        f"Downloading {len(matrices)} matrices to {destination} "
        f"with {workers} concurrent downloads"
    )
    download_matrices_in_parallel(matrices, destination, workers)

    moved_files = move_matrices_to_root(destination)
    print(f"{moved_files} .mtx files moved to {destination}")


if __name__ == "__main__":
    main()
