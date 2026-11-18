#!/usr/bin/env python3
"""Download the BHOSLIB collection hosted by Network Repository.

Fetches the 36 configured BHOSLIB benchmark archives (``frb100-40`` plus five
instances for each of seven ``frb<v>-<c>`` families) in parallel, verifies ZIP
headers in memory before extraction, flattens extracted files into
``$PROJECT_ROOT/datasets/bhoslib/raw/``, discards ``readme*`` files, and
removes empty staging directories.

Environment Variables:
    PROJECT_ROOT: Required path to the repository root containing ``datasets/``.
    BHOSLIB_DOWNLOAD_WORKERS: Optional positive integer controlling thread pool
        concurrency (default: ``36``).

Notes:
    * Reruns re-download archives (no completion markers or HTTP Range resume);
      byte-identical files already at the raw root are deduplicated during
      flattening, while conflicting filenames raise ``FileExistsError``.
    * Downloaded source matrices still require conversion via
      ``scripts/convert/matrix_market.py`` to produce DIMACS ``.col`` files.
"""

from __future__ import annotations

import argparse
import io
import filecmp
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import move

import requests

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from metadata_utils import base_provenance, resolve_metadata_path, update_metadata


# Checked-in catalog of 36 BHOSLIB instances hosted on Network Repository.
BHOSLIB_INSTANCES = [
    "frb100-40",
    *[
        f"frb{vertices}-{clique}-{instance}"
        for vertices, clique in (
            (30, 15),
            (35, 17),
            (40, 19),
            (45, 21),
            (50, 23),
            (53, 24),
            (59, 26),
        )
        for instance in range(1, 6)
    ],
]

# Base URL and browser-like User-Agent expected by nrvis.com.
BASE_URL = "https://nrvis.com/download/data/bhoslib"
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
    response = requests.get(url, headers=HEADERS, timeout=120)
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        return False

    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        zip_file.extractall(destination)
    return True


def download_and_extract(name: str, destination: Path) -> bool:
    """Try downloading a BHOSLIB instance using candidate capitalizations.

    Attempts the original, lowercase, and uppercase archive names (deduplicated
    in order) and extracts the first successful archive into
    ``<destination>/.downloads/<name>/``.

    Args:
        name: Configured BHOSLIB instance name.
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
    """Download all configured BHOSLIB instances and flatten extracted files.

    Reads ``PROJECT_ROOT`` (required) and ``BHOSLIB_DOWNLOAD_WORKERS``
    (optional positive integer, default ``36``) from the environment.

    Raises:
        RuntimeError: If ``PROJECT_ROOT`` is unset.
        ValueError: If ``BHOSLIB_DOWNLOAD_WORKERS`` is not a positive integer.
        FileExistsError: If flattening encounters conflicting filenames.
        SystemExit: Exits with status ``1`` if one or more instances fail.
    """
    argparse.ArgumentParser(
        description="Download the BHOSLIB collection hosted by Network Repository."
    ).parse_args()
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set. Run this script through mise or "
            "set the environment variable explicitly."
        )

    destination = Path(project_root) / "datasets" / "bhoslib" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

    try:
        workers = int(os.environ.get("BHOSLIB_DOWNLOAD_WORKERS", "36"))
    except ValueError as error:
        raise ValueError("BHOSLIB_DOWNLOAD_WORKERS must be an integer.") from error
    if workers < 1:
        raise ValueError("BHOSLIB_DOWNLOAD_WORKERS must be at least 1.")

    print(
        f"Downloading {len(BHOSLIB_INSTANCES)} instances to {destination} "
        f"with {workers} concurrent downloads"
    )
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(download_and_extract, name, destination): name
            for name in BHOSLIB_INSTANCES
        }
        for completed, future in enumerate(as_completed(downloads), start=1):
            name = downloads[future]
            success = future.result()
            print(
                f"  [{completed}/{len(BHOSLIB_INSTANCES)}] "
                f"{'OK' if success else 'FAILED'}: {name}"
            )
            if not success:
                failures.append(name)

    moved_files = move_files_to_root(destination)
    metadata_path = update_metadata(
        resolve_metadata_path(destination),
        "download",
        {
            **base_provenance(("requests",)),
            "source": BASE_URL,
            "destination": str(destination),
            "workers": workers,
            "summary": {
                "catalog_size": len(BHOSLIB_INSTANCES),
                "succeeded": len(BHOSLIB_INSTANCES) - len(failures),
                "failed": len(failures),
                "moved_files": moved_files,
            },
            "failures": sorted(failures),
        },
    )
    print(f"{moved_files} files moved to {destination}")
    print(f"Metadata saved to {metadata_path}")
    if failures:
        print(f"{len(failures)} instances failed: {', '.join(sorted(failures))}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
