#!/usr/bin/env python3
"""Download the BHOSLIB collection hosted by Network Repository."""

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

BASE_URL = "https://nrvis.com/download/data/bhoslib"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def try_download(url: str, destination: Path) -> bool:
    """Download and extract a valid ZIP into the destination directory."""
    response = requests.get(url, headers=HEADERS, timeout=120)
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        return False

    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        zip_file.extractall(destination)
    return True


def download_and_extract(name: str, destination: Path) -> bool:
    """Try downloading an instance using different capitalizations."""
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
    """Discard readmes and move other files to the raw directory root."""
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
    argparse.ArgumentParser(description=__doc__).parse_args()
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
    print(f"{moved_files} files moved to {destination}")
    if failures:
        print(f"{len(failures)} instances failed: {', '.join(sorted(failures))}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
