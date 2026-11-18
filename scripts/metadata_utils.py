"""Shared provenance and JSON metadata utilities for graph-instances scripts.

Provides atomic read-merge-write helpers for per-dataset ``metadata.json``
files so each pipeline stage (``conversion``, ``generation``, ``selection``)
records its parameters, seeds, software environment, and summaries without
overwriting metadata from earlier stages.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_metadata_path(output_directory: Path) -> Path:
    """Resolve the ``metadata.json`` path associated with an output directory.

    When ``output_directory`` follows the standard dataset layout
    (``datasets/<name>/{raw,processed,selected}``), places ``metadata.json`` at
    ``datasets/<name>/metadata.json``; otherwise places it directly inside
    ``output_directory``.

    Args:
        output_directory: Stage output directory (e.g. ``datasets/cubic/processed``).

    Returns:
        Path to the target ``metadata.json`` file.
    """
    if output_directory.name in {"raw", "processed", "selected"}:
        return output_directory.parent / "metadata.json"
    return output_directory / "metadata.json"


def git_commit_short(repo_root: Path = REPO_ROOT) -> str | None:
    """Return the short Git HEAD hash of ``repo_root``, or ``None`` if unavailable.

    Args:
        repo_root: Repository directory to inspect.

    Returns:
        Abbreviated commit hash string, or ``None`` if Git is unavailable or
        the directory is not a Git repository.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def package_versions(packages: Iterable[str]) -> dict[str, str]:
    """Look up installed versions for a sequence of Python distribution names.

    Args:
        packages: Distribution names (e.g. ``("numpy", "scipy", "scikit-learn")``).

    Returns:
        Mapping from package name to installed version string (or ``"unknown"``
        if distribution metadata cannot be resolved).
    """
    versions: dict[str, str] = {}
    for name in packages:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def base_provenance(packages: Iterable[str] = ()) -> dict[str, Any]:
    """Build standard execution provenance metadata for a pipeline stage.

    Args:
        packages: Direct third-party package names relevant to the stage.

    Returns:
        Dictionary with ``timestamp_utc``, ``git_commit``, ``python_version``,
        ``packages``, and ``command``.
    """
    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit_short(),
        "python_version": platform.python_version(),
        "packages": package_versions(packages),
        "command": list(sys.argv),
    }


def sha256_file(path: Path) -> str:
    """Compute the hexadecimal SHA-256 digest of a file by streaming 1 MiB chunks.

    Args:
        path: Path to an existing file.

    Returns:
        Lowercase 64-character SHA-256 hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_metadata(
    metadata_path: Path, section: str, payload: dict[str, Any]
) -> Path:
    """Atomically merge ``payload`` into ``section`` of a JSON metadata file.

    Preserves other top-level sections already stored in ``metadata_path`` and
    writes via a temporary sibling file before replacing the target.

    Args:
        metadata_path: Destination ``metadata.json`` path.
        section: Top-level section name (e.g. ``"generation"``, ``"selection"``).
        payload: JSON-serializable dictionary describing the stage execution.

    Returns:
        Path to the updated metadata file.
    """
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except (OSError, json.JSONDecodeError):
            data = {}

    data[section] = payload
    temp_path = metadata_path.with_name(f"{metadata_path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(metadata_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return metadata_path
