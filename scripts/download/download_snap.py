#!/usr/bin/env python3
"""Download edge-list graphs from the Stanford SNAP collection."""

from __future__ import annotations

import argparse
import gzip
import os
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import copyfileobj, move
from urllib.parse import urlparse

import requests


BASE_URL = "https://snap.stanford.edu/data"

# Files verified against their individual official SNAP pages.
ROOT_FILES = [
    "facebook_combined.txt.gz", "gplus_combined.txt.gz", "twitter_combined.txt.gz",
    "soc-Epinions1.txt.gz", "soc-LiveJournal1.txt.gz",
    "soc-pokec-relationships.txt.gz", "soc-Slashdot0811.txt.gz",
    "soc-Slashdot0902.txt.gz", "wiki-Vote.txt.gz", "wiki-RfA.txt.gz",
    "email-Eu-core.txt.gz", "wiki-topcats.txt.gz", "email-EuAll.txt.gz",
    "email-Enron.txt.gz", "wiki-Talk.txt.gz", "cit-HepPh.txt.gz",
    "cit-HepTh.txt.gz", "cit-Patents.txt.gz", "ca-AstroPh.txt.gz",
    "ca-CondMat.txt.gz", "ca-GrQc.txt.gz", "ca-HepPh.txt.gz", "ca-HepTh.txt.gz",
    "web-BerkStan.txt.gz", "web-Google.txt.gz", "web-NotreDame.txt.gz",
    "web-Stanford.txt.gz", "amazon0302.txt.gz", "amazon0312.txt.gz",
    "amazon0505.txt.gz", "amazon0601.txt.gz", "p2p-Gnutella04.txt.gz",
    "p2p-Gnutella05.txt.gz", "p2p-Gnutella06.txt.gz", "p2p-Gnutella08.txt.gz",
    "p2p-Gnutella09.txt.gz", "p2p-Gnutella24.txt.gz", "p2p-Gnutella25.txt.gz",
    "p2p-Gnutella30.txt.gz", "p2p-Gnutella31.txt.gz", "roadNet-CA.txt.gz",
    "roadNet-PA.txt.gz", "roadNet-TX.txt.gz", "as-733.tar.gz",
    "as-skitter.txt.gz", "as-caida.tar.gz",
    *[
        f"oregon{family}_{data}.txt.gz"
        for family in (1, 2)
        for data in (
            "010331", "010407", "010414", "010421", "010428",
            "010505", "010512", "010519", "010526",
        )
    ],
    "soc-sign-bitcoinotc.csv.gz", "soc-sign-bitcoinalpha.csv.gz",
    "soc-sign-epinions.txt.gz", "soc-sign-Slashdot081106.txt.gz",
    "soc-sign-Slashdot090216.txt.gz", "soc-sign-Slashdot090221.txt.gz",
    "loc-gowalla_edges.txt.gz", "loc-brightkite_edges.txt.gz",
    "sx-stackoverflow.txt.gz", "sx-mathoverflow.txt.gz", "sx-superuser.txt.gz",
    "sx-askubuntu.txt.gz", "wiki-talk-temporal.txt.gz",
    "email-Eu-core-temporal.txt.gz", "CollegeMsg.txt.gz",
    "gemsec_deezer_dataset.tar.gz", "gemsec_facebook_dataset.tar.gz",
    "soc-redditHyperlinks-body.tsv", "soc-redditHyperlinks-title.tsv",
    "comm-f2f-Resistance-network.tar.gz", "act-mooc.tar.gz", "twitch.zip",
    "git_web_ml.zip", "deezer_europe.zip", "lastfm_asia.zip",
    "twitch_gamers.zip", "congress_network.zip", "wikiElec.ElecBs3.txt.gz",
    "wikipedia.zip",
]

SNAP_URLS = [f"{BASE_URL}/{name}" for name in ROOT_FILES]
SNAP_URLS += [
    f"{BASE_URL}/bigdata/communities/{name}"
    for name in (
        "com-lj.ungraph.txt.gz",
        "com-orkut.ungraph.txt.gz",
        "com-youtube.ungraph.txt.gz",
        "com-dblp.ungraph.txt.gz",
        "com-amazon.ungraph.txt.gz",
    )
]
SNAP_URLS.append(
    f"{BASE_URL}/wikispeedia/wikispeedia_paths-and-graph.tar.gz"
)
FRIENDSTER_URL = f"{BASE_URL}/bigdata/communities/com-friendster.ungraph.txt.gz"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and extract graphs from the Stanford SNAP collection."
    )
    parser.add_argument(
        "--include-friendster",
        action="store_true",
        help="Include the exceptionally large com-Friendster graph.",
    )
    return parser.parse_args()


def download_file(url: str, destination: Path) -> tuple[str, Path | None]:
    """Stream a file download and allow the task to resume."""
    name = Path(urlparse(url).path).name
    marker = destination / ".completed" / name
    if marker.exists() or (destination / ".concluidos" / name).exists():
        return "ALREADY EXISTS", None

    temporary = destination / ".downloads" / name
    temporary.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, timeout=300, stream=True) as response:
        response.raise_for_status()
        with temporary.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)

    return "OK", temporary


def extract_file(name: str, source: Path, destination: Path) -> int:
    """Extract a downloaded file and return the number of files produced."""
    if name.endswith(".tar.gz"):
        with tarfile.open(source, "r:gz") as file:
            file.extractall(destination, filter="data")
        source.unlink()
    elif name.endswith(".zip"):
        with zipfile.ZipFile(source) as file:
            file.extractall(destination)
        source.unlink()
    elif name.endswith(".gz"):
        output_file = destination / name.removesuffix(".gz")
        temporary_output = source.with_name(source.name + ".extracted")
        try:
            with gzip.open(source, "rb") as compressed, temporary_output.open("wb") as file:
                copyfileobj(compressed, file)
            temporary_output.replace(output_file)
        finally:
            temporary_output.unlink(missing_ok=True)
        source.unlink()
    else:
        source.replace(destination / Path(urlparse(name).path).name)

    marker = destination / ".completed" / name
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return 1


def move_files_to_root(destination: Path) -> tuple[int, int]:
    """Move files from subdirectories to raw and discard readme files."""
    moved = 0
    discarded = 0

    for source in sorted(destination.rglob("*"), key=lambda path: str(path)):
        if source.is_dir():
            continue

        relative = source.relative_to(destination)
        if relative.parts[0] in {".downloads", ".completed", ".concluidos"}:
            continue

        if source.name.lower().startswith("readme") or source.name.startswith("._"):
            source.unlink()
            discarded += 1
            continue

        if source.parent == destination:
            continue

        target = destination / source.name
        if target.exists():
            prefix = "_".join(relative.parent.parts)
            target = destination / f"{prefix}_{source.name}"

        counter = 2
        candidate = target
        while candidate.exists():
            candidate = target.with_name(
                f"{target.stem}_{counter}{target.suffix}"
            )
            counter += 1

        move(source, candidate)
        moved += 1

    directories = sorted(
        (
            path
            for path in destination.rglob("*")
            if path.is_dir()
            and path.relative_to(destination).parts[0]
            not in {".downloads", ".completed", ".concluidos"}
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass

    return moved, discarded


def main() -> None:
    args = parse_args()
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT is not set. Run this script through mise or "
            "set the environment variable explicitly."
        )

    destination = Path(project_root) / "datasets" / "snap" / "raw"
    destination.mkdir(parents=True, exist_ok=True)
    urls = [*SNAP_URLS, FRIENDSTER_URL] if args.include_friendster else SNAP_URLS

    try:
        workers = int(os.environ.get("SNAP_DOWNLOAD_WORKERS", str(len(urls))))
    except ValueError as error:
        raise ValueError("SNAP_DOWNLOAD_WORKERS must be an integer.") from error
    if workers < 1:
        raise ValueError("SNAP_DOWNLOAD_WORKERS must be at least 1.")

    print(
        f"Downloading {len(urls)} SNAP files to {destination} "
        f"with {workers} concurrent downloads"
    )
    failures: list[str] = []
    downloaded_files: list[tuple[str, Path]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(download_file, url, destination): url
            for url in urls
        }
        for completed, future in enumerate(as_completed(downloads), start=1):
            url = downloads[future]
            name = Path(urlparse(url).path).name
            try:
                status, file_path = future.result()
                print(f"  [{completed}/{len(urls)}] {status}: {name}")
                if status == "OK" and file_path is not None:
                    downloaded_files.append((name, file_path))
            except (requests.RequestException, OSError) as error:
                print(f"  [{completed}/{len(urls)}] FAILED: {name}: {error}")
                failures.append(name)

    for name, file_path in downloaded_files:
        try:
            extract_file(name, file_path, destination)
        except (OSError, EOFError, gzip.BadGzipFile, tarfile.TarError, zipfile.BadZipFile) as error:
            print(f"  FAILED to extract {name}: {error}")
            failures.append(name)

    moved, discarded = move_files_to_root(destination)
    print(
        f"Organization complete: {moved} files moved to {destination} "
        f"and {discarded} auxiliary files discarded."
    )

    if failures:
        print(f"{len(failures)} files failed: {', '.join(sorted(set(failures)))}")
        raise SystemExit(1)
    else:
        print("All SNAP files have been downloaded and extracted.")


if __name__ == "__main__":
    main()
