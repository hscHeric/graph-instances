#!/usr/bin/env python3
"""Download the Harwell-Boeing collection from NIST Matrix Market.

Discovers all Harwell-Boeing matrix sets from the official NIST Matrix Market
index (``https://math.nist.gov/pub/MatrixMarket2/Harwell-Boeing/``), downloads
compressed Matrix Market (``.mtx.gz``) files concurrently, decompresses each
matrix atomically into ``$PROJECT_ROOT/datasets/harwell_boeing/raw/<name>.mtx``,
and skips matrices already present at the destination.

This script queries NIST Matrix Market directly instead of ``ssgetpy`` /
SuiteSparse (whose static S3 files can be offline with HTTP 403).

Environment Variables:
    PROJECT_ROOT: Required path to the repository root containing ``datasets/``.
    HARWELL_BOEING_DOWNLOAD_WORKERS: Optional positive integer controlling
        concurrent download threads (default: ``15``).

Notes:
    * Existing ``<name>.mtx`` files are skipped without checksum verification.
    * Matrices that lack a ``.mtx.gz`` download on Matrix Market are reported
      as ``NO MTX`` and skipped without failing the run.
    * Not all Harwell-Boeing matrices are square and symmetric; graph
      eligibility is filtered later by ``scripts/convert/matrix_market.py``.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import copyfileobj

import requests


# Official NIST Matrix Market URLs hosting the Harwell-Boeing collection.
NIST_BASE_URL = "https://math.nist.gov"
HB_INDEX_URL = f"{NIST_BASE_URL}/pub/MatrixMarket2/Harwell-Boeing/"
HB_DATA_URL = f"{NIST_BASE_URL}/MatrixMarket/data/Harwell-Boeing"
HB_PUB_URL = f"{NIST_BASE_URL}/pub/MatrixMarket2/Harwell-Boeing"

REQUEST_TIMEOUT_S = 120
SET_LINK_PATTERN = re.compile(
    r'href=["\']/MatrixMarket/data/Harwell-Boeing/([^/"\']+)/\1\.html["\']',
    re.IGNORECASE,
)
MATRIX_LINK_PATTERN = re.compile(
    r'<li[^>]*>\s*<a\s+href=["\']([^/"\']+)\.html["\']',
    re.IGNORECASE,
)
MTX_GZ_LINK_PATTERN = re.compile(
    r'href=["\'](/pub/MatrixMarket2/Harwell-Boeing/[^"\']+\.mtx\.gz)["\']',
    re.IGNORECASE,
)


def discover_sets() -> list[str]:
    """Fetch the NIST Harwell-Boeing index and return all matrix set names.

    Returns:
        Ordered list of unique set identifiers (e.g. ``"bcspwr"``, ``"bcsstruc1"``).

    Raises:
        requests.RequestException: If fetching the index page fails.
        RuntimeError: If no Harwell-Boeing set links are found in the index.
    """
    response = requests.get(HB_INDEX_URL, timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    sets = list(dict.fromkeys(SET_LINK_PATTERN.findall(response.text)))
    if not sets:
        raise RuntimeError(
            f"No Harwell-Boeing matrix sets were found at {HB_INDEX_URL}."
        )
    return sets


def discover_set_matrices(set_name: str) -> list[tuple[str, str]]:
    """Fetch a NIST Harwell-Boeing set page and list its matrices.

    Args:
        set_name: Harwell-Boeing set slug (e.g. ``"bcspwr"``).

    Returns:
        List of ``(set_name, matrix_name)`` tuples discovered on the set page.

    Raises:
        requests.RequestException: If fetching the set page fails.
    """
    url = f"{HB_DATA_URL}/{set_name}/{set_name}.html"
    response = requests.get(url, timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    matrices = [
        name
        for name in dict.fromkeys(MATRIX_LINK_PATTERN.findall(response.text))
        if name.lower() != set_name.lower()
    ]
    return [(set_name, name) for name in matrices]


def discover_matrices(workers: int) -> list[tuple[str, str]]:
    """Discover all Harwell-Boeing ``(set_name, matrix_name)`` entries in parallel.

    Args:
        workers: Maximum number of concurrent HTTP requests.

    Returns:
        Sorted list of unique ``(set_name, matrix_name)`` pairs.

    Raises:
        RuntimeError: If no matrices are discovered across all sets.
    """
    sets = discover_sets()
    entries: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(discover_set_matrices, set_name): set_name
            for set_name in sets
        }
        for future in as_completed(futures):
            for set_name, matrix_name in future.result():
                entries.setdefault(matrix_name, set_name)

    if not entries:
        raise RuntimeError("No matrices were found in the Harwell-Boeing collection.")
    return sorted((set_name, matrix_name) for matrix_name, set_name in entries.items())


def resolve_fallback_mtx_url(set_name: str, matrix_name: str) -> str | None:
    """Inspect a matrix's HTML page to find its exact ``.mtx.gz`` download link.

    Args:
        set_name: Harwell-Boeing set slug.
        matrix_name: Matrix identifier on the set page.

    Returns:
        Full URL to the ``.mtx.gz`` file if linked on the matrix page;
        otherwise ``None``.
    """
    page_url = f"{HB_DATA_URL}/{set_name}/{matrix_name}.html"
    response = requests.get(page_url, timeout=REQUEST_TIMEOUT_S)
    if response.status_code != 200:
        return None
    match = MTX_GZ_LINK_PATTERN.search(response.text)
    if not match:
        return None
    return f"{NIST_BASE_URL}{match.group(1)}"


def download_matrix(set_name: str, matrix_name: str, destination: Path) -> str:
    """Download and atomically decompress one ``.mtx.gz`` file if absent.

    Streams the compressed archive to ``.downloads/``, decompresses into a
    temporary ``.mtx.tmp`` file, and atomically replaces ``<matrix_name>.mtx``
    only after decompression completes cleanly.

    Args:
        set_name: Harwell-Boeing set slug.
        matrix_name: Matrix identifier.
        destination: Raw dataset root directory.

    Returns:
        ``"ALREADY EXISTS"`` if ``<matrix_name>.mtx`` already exists, ``"OK"``
        if downloaded and decompressed, or ``"NO MTX"`` if the matrix has no
        Matrix Market archive on NIST.

    Raises:
        requests.RequestException: If downloading fails with an HTTP/network error.
        gzip.BadGzipFile: If the downloaded stream is not valid gzip data.
        OSError: If writing or renaming temporary files fails.
    """
    target = destination / f"{matrix_name}.mtx"
    if target.exists():
        return "ALREADY EXISTS"

    staging = destination / ".downloads"
    staging.mkdir(parents=True, exist_ok=True)
    archive_path = staging / f"{matrix_name}.mtx.gz.tmp"
    extracted_path = staging / f"{matrix_name}.mtx.tmp"

    url = f"{HB_PUB_URL}/{set_name}/{matrix_name}.mtx.gz"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_S, stream=True)
        if response.status_code == 404:
            response.close()
            fallback_url = resolve_fallback_mtx_url(set_name, matrix_name)
            if fallback_url is None:
                return "NO MTX"
            response = requests.get(fallback_url, timeout=REQUEST_TIMEOUT_S, stream=True)

        with response:
            response.raise_for_status()
            with archive_path.open("wb") as archive_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        archive_file.write(chunk)

        with gzip.open(archive_path, "rb") as compressed, extracted_path.open("wb") as output:
            copyfileobj(compressed, output)
        extracted_path.replace(target)
        return "OK"
    finally:
        archive_path.unlink(missing_ok=True)
        extracted_path.unlink(missing_ok=True)


def download_matrices_in_parallel(
    matrices: list[tuple[str, str]], destination: Path, workers: int
) -> tuple[int, int, list[str]]:
    """Download and decompress discovered matrices concurrently.

    Args:
        matrices: Sequence of ``(set_name, matrix_name)`` tuples.
        destination: Raw dataset root directory.
        workers: Maximum number of concurrent worker threads.

    Returns:
        Tuple ``(downloaded_count, skipped_no_mtx_count, failed_names)``.
    """
    downloaded_count = 0
    no_mtx_count = 0
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(download_matrix, set_name, matrix_name, destination): matrix_name
            for set_name, matrix_name in matrices
        }

        total = len(downloads)
        for completed, future in enumerate(as_completed(downloads), start=1):
            matrix_name = downloads[future]
            try:
                status = future.result()
                print(f"  [{completed}/{total}] {status}: {matrix_name}")
                if status == "OK":
                    downloaded_count += 1
                elif status == "NO MTX":
                    no_mtx_count += 1
            except (requests.RequestException, gzip.BadGzipFile, EOFError, OSError) as error:
                print(f"  [{completed}/{total}] FAILED: {matrix_name}: {error}")
                failures.append(matrix_name)

    staging = destination / ".downloads"
    try:
        staging.rmdir()
    except OSError:
        pass

    return downloaded_count, no_mtx_count, failures


def main() -> None:
    """Discover Harwell-Boeing matrices on NIST Matrix Market and download them.

    Reads ``PROJECT_ROOT`` (required) and ``HARWELL_BOEING_DOWNLOAD_WORKERS``
    (optional positive integer, default ``15``) from the environment.

    Raises:
        RuntimeError: If ``PROJECT_ROOT`` is unset or no matrices are found.
        ValueError: If ``HARWELL_BOEING_DOWNLOAD_WORKERS`` is invalid.
        SystemExit: Exits with status ``1`` if one or more downloads fail.
    """
    argparse.ArgumentParser(
        description="Download the Harwell-Boeing collection from NIST Matrix Market."
    ).parse_args()

    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set. Run this script through mise or "
            "set the environment variable explicitly."
        )

    destination = Path(project_root) / "datasets" / "harwell_boeing" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

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

    print(f"Discovering Harwell-Boeing matrices from {HB_INDEX_URL}")
    matrices = discover_matrices(workers)
    print(
        f"Downloading {len(matrices)} matrices to {destination} "
        f"with {workers} concurrent downloads"
    )
    downloaded_count, no_mtx_count, failures = download_matrices_in_parallel(
        matrices, destination, workers
    )
    print(
        f"Completed: {downloaded_count} downloaded, "
        f"{no_mtx_count} without .mtx.gz, {len(failures)} failed."
    )
    if failures:
        print(f"{len(failures)} matrices failed: {', '.join(sorted(failures))}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
