#!/usr/bin/env python3
"""Download the DIMACS collection hosted by Network Repository.

Fetches the 78 configured DIMACS clique/coloring benchmark archives in parallel
with a 60-second per-request timeout, verifies ZIP magic bytes in memory before
extraction, flattens extracted files into ``$PROJECT_ROOT/datasets/dimacs/raw/``,
discards ``readme*`` files, and prunes empty staging directories.

Environment Variables:
    PROJECT_ROOT: Required path to the repository root containing ``datasets/``.
    DIMACS_DOWNLOAD_WORKERS: Optional positive integer controlling thread pool
        concurrency (default: ``78``).

Notes:
    * Reruns re-download archives (no completion markers or HTTP Range resume);
      byte-identical files already at the raw root are deduplicated during
      flattening, while conflicting filenames raise ``FileExistsError``.
    * Downloaded Matrix Market files still require conversion via
      ``scripts/convert/matrix_market.py`` to produce DIMACS ``.col`` files.
"""

from __future__ import annotations

import argparse
import io
import filecmp
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import move

import requests


# Checked-in catalog of 78 DIMACS benchmark instances hosted on Network Repository.
DIMACS_INSTANCES = [
    "C1000-9", "C125-9", "C2000-5", "C2000-9", "C250-9", "C4000-5", "C500-9",
    "DSJC1000-5", "DSJC500-5",
    "MANN-a27", "MANN-a45", "MANN-a81", "MANN-a9",
    "brock200-1", "brock200-2", "brock200-3", "brock200-4",
    "brock400-1", "brock400-2", "brock400-3", "brock400-4",
    "brock800-1", "brock800-2", "brock800-3", "brock800-4",
    "c-fat200-1", "c-fat200-2", "c-fat200-5",
    "c-fat500-1", "c-fat500-10", "c-fat500-2", "c-fat500-5",
    "gen200-p0-9-44", "gen200-p0-9-55", "gen400-p0-9-55",
    "gen400-p0-9-65", "gen400-p0-9-75",
    "hamming10-2", "hamming10-4", "hamming6-2", "hamming6-4",
    "hamming8-2", "hamming8-4",
    "johnson16-2-4", "johnson32-2-4", "johnson8-2-4", "johnson8-4-4",
    "keller4", "keller5", "keller6",
    "p-hat1000-1", "p-hat1000-2", "p-hat1000-3",
    "p-hat1500-1", "p-hat1500-2", "p-hat1500-3",
    "p-hat300-3", "p-hat500-1", "p-hat500-2", "p-hat500-3",
    "p-hat700-1", "p-hat700-2", "p-hat700-3",
    "san1000", "san200-0-7-1", "san200-0-7-2",
    "san200-0-9-1", "san200-0-9-2", "san200-0-9-3",
    "san400-0-5-1", "san400-0-7-1", "san400-0-7-2",
    "san400-0-7-3", "san400-0-9-1",
    "sanr200-0-7", "sanr200-0-9", "sanr400-0-5", "sanr400-0-7",
]

# Base URL and browser-like User-Agent expected by nrvis.com.
BASE_URL = "https://nrvis.com/download/data/dimacs"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def try_download(url: str, destination: Path) -> bool:
    """Download and extract a valid ZIP archive into a destination directory.

    Validates that the HTTP response returns status 200 and starts with the
    ZIP magic bytes (``PK``) before extracting in memory.

    Args:
        url: Remote ZIP archive URL.
        destination: Directory where archive members are extracted.

    Returns:
        ``True`` if the response was a valid ZIP and extraction succeeded;
        ``False`` if the response status was not 200 or lacked a ZIP header.

    Raises:
        requests.RequestException: If the HTTP request fails or times out.
        zipfile.BadZipFile: If the payload starts with ``PK`` but is corrupt.
        OSError: If writing extracted files fails.
    """
    response = requests.get(url, headers=HEADERS, timeout=60)
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        return False

    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        zip_file.extractall(destination)
    return True


def download_and_extract(name: str, destination: Path) -> bool:
    """Try downloading a DIMACS instance using candidate capitalizations.

    Attempts the original, lowercase, and uppercase archive names (deduplicated
    in order) and extracts the first successful archive into
    ``<destination>/.downloads/<name>/``.

    Args:
        name: Configured DIMACS instance name.
        destination: Raw dataset root directory.

    Returns:
        ``True`` if any capitalization variant downloaded and extracted
        successfully; ``False`` otherwise.
    """
    instance_destination = destination / ".downloads" / name
    instance_destination.mkdir(parents=True, exist_ok=True)
    candidates = list(dict.fromkeys((name, name.lower(), name.upper())))
    for candidate in candidates:
        url = f"{BASE_URL}/{candidate}.zip"
        try:
            if try_download(url, instance_destination):
                return True
        except (requests.RequestException, zipfile.BadZipFile, OSError) as error:
            print(f"  Error trying {url}: {error}")

    print(f"  No name variant worked for {name}: {candidates}")
    return False


def move_files_to_root(destination: Path) -> int:
    """Flatten extracted files into the raw directory root and discard readmes.

    Deletes files whose lowercase name starts with ``readme``, removes
    byte-identical duplicates already present at the root, raises on
    conflicting filenames, and prunes empty subdirectories.

    Args:
        destination: Raw dataset root directory containing staged downloads.

    Returns:
        Number of files moved into ``destination``.

    Raises:
        FileExistsError: If a flattened file collides with an existing file
            whose contents differ.
    """
    moved_files = 0
    for source in destination.rglob("*"):
        if source.is_dir():
            continue

        if source.name.lower().startswith("readme"):
            source.unlink()
            continue

        if source.parent == destination:
            continue

        target = destination / source.name
        if target.exists():
            if filecmp.cmp(source, target, shallow=False):
                source.unlink()
                continue
            raise FileExistsError(
                f"Could not move {source}: file {target} already exists."
            )
        move(source, target)
        moved_files += 1

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
    """Download all configured DIMACS instances and flatten extracted files.

    Reads ``PROJECT_ROOT`` (required) and ``DIMACS_DOWNLOAD_WORKERS`` (optional
    positive integer, default ``78``) from the environment.

    Raises:
        RuntimeError: If ``PROJECT_ROOT`` is unset.
        ValueError: If ``DIMACS_DOWNLOAD_WORKERS`` is not a positive integer.
        FileExistsError: If flattening encounters conflicting filenames.
        SystemExit: Exits with status ``1`` if one or more instances fail.
    """
    argparse.ArgumentParser(
        description="Download the DIMACS collection hosted by Network Repository."
    ).parse_args()
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set. Run this script through mise or "
            "set the environment variable explicitly."
        )

    destination = Path(project_root) / "datasets" / "dimacs" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

    try:
        workers = int(os.environ.get("DIMACS_DOWNLOAD_WORKERS", "78"))
    except ValueError as error:
        raise ValueError(
            "DIMACS_DOWNLOAD_WORKERS must be an integer."
        ) from error
    if workers < 1:
        raise ValueError("DIMACS_DOWNLOAD_WORKERS must be at least 1.")

    print(
        f"Downloading {len(DIMACS_INSTANCES)} instances to {destination} "
        f"with {workers} concurrent downloads"
    )
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(download_and_extract, name, destination): name
            for name in DIMACS_INSTANCES
        }
        for completed, future in enumerate(as_completed(downloads), start=1):
            name = downloads[future]
            success = future.result()
            print(
                f"  [{completed}/{len(DIMACS_INSTANCES)}] "
                f"{'OK' if success else 'FAILED'}: {name}"
            )
            if not success:
                failures.append(name)

    moved_files = move_files_to_root(destination)
    print(f"{moved_files} files moved to {destination}")

    if failures:
        print(f"{len(failures)} instances failed: {', '.join(failures)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
