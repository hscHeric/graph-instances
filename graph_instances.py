#!/usr/bin/env python3
"""Download, convert, generate, select, and filter graph instances.

Unified command-line dispatcher over the individual workflow scripts under
``scripts/``. Runs each child script with the active Python interpreter
(``sys.executable``), sets the working directory to the repository root, and
injects ``PROJECT_ROOT`` into the child environment so workflows work without
``mise`` or manual environment setup.

Workflows:
    * ``download``: Fetch raw collections into ``datasets/<name>/raw/``.
    * ``convert``: Normalize raw files into simple undirected DIMACS ``.col``
      graphs under ``datasets/<name>/processed/``.
    * ``generate``: Build synthetic graphs into ``datasets/<model>/processed/``.
    * ``select``: Cluster structural features and copy representative subsets
      into ``datasets/<name>/selected/``.
    * ``filter``: Inspect (and optionally delete with ``--remove-isolated``)
      ``.col`` graphs containing isolated vertices in ``processed/`` and
      ``selected/``.

Usage:
    python graph_instances.py COMMAND DATASET [SCRIPT_OPTIONS ...]

Example:
    python graph_instances.py generate cubic --vertices 100 200 --count 3
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


# Absolute path to the repository root; exported to child processes as PROJECT_ROOT.
ROOT = Path(__file__).resolve().parent

# Canonical hyphenated names for downloaded and synthetic datasets.
DOWNLOADED = ("harwell-boeing", "dimacs", "bhoslib", "snap")
GENERATED = ("erdos-renyi", "barabasi-albert", "watts-strogatz", "cubic")
DATASETS = (*DOWNLOADED, *GENERATED)

# Default target subset size passed to representative_selection.py per dataset.
SELECTION_SIZES = {
    "harwell-boeing": 50, "dimacs": 80, "bhoslib": 30, "snap": 20,
    "erdos-renyi": 30, "barabasi-albert": 30, "watts-strogatz": 30, "cubic": 30,
}

# Allowed dataset/model targets for each top-level CLI subcommand.
COMMAND_TARGETS = {
    "download": DOWNLOADED, "convert": DOWNLOADED, "generate": GENERATED,
    "select": DATASETS, "filter": DATASETS,
}


def main() -> None:
    """Parse CLI arguments and dispatch the requested graph workflow.

    Resolves dataset/model targets (supporting hyphenated or underscored names
    as well as ``all``), injects dataset-specific default arguments, sets
    ``PROJECT_ROOT`` to the repository root, and executes each child script
    sequentially using the active Python interpreter.

    Note:
        User-supplied options are appended *after* injected defaults so later
        argparse value options override preset defaults. When ``dataset`` is
        ``all``, child scripts execute sequentially in catalog order and stop
        immediately on the first nonzero exit code (no rollback is performed).

    Raises:
        SystemExit: Propagated with the child's nonzero exit code if any
            dispatched script fails, or raised by ``argparse`` on invalid CLI
            arguments.
    """
    parser = argparse.ArgumentParser(
        description="Download, convert, generate, select, and filter graph instances.",
        epilog="Example: python graph_instances.py generate cubic --vertices 100 200 --count 3",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "download": "Download original datasets to datasets/<name>/raw",
        "convert": "Convert downloaded datasets to simple undirected .col graphs",
        "generate": "Generate synthetic graphs in datasets/<model>/processed",
        "select": "Select representative graphs from processed datasets",
        "filter": "Find isolated vertices; add --remove-isolated to delete entire graphs",
    }
    for command, description in descriptions.items():
        child = commands.add_parser(command, help=description, description=description)
        targets = COMMAND_TARGETS[command]
        child.add_argument(
            "dataset", type=lambda value: value.replace("_", "-"),
            choices=(*targets, "all"), help="Dataset/model name, or all",
        )
        child.add_argument(
            "options", nargs=argparse.REMAINDER,
            help="Script options, such as --vertices, --count, --seed, --remove-isolated; use DATASET --help for details",
        )
    args = parser.parse_args()
    options = args.options
    # Strip a single leading "--" separator (e.g. from mise or manual passthrough).
    if options[:1] == ["--"]:
        options = options[1:]
    if args.dataset == "all":
        targets = COMMAND_TARGETS[args.command]
    else:
        targets = (args.dataset,)

    def run(script: str, parameters: list[str]) -> None:
        """Execute a repository script as a subprocess with ``PROJECT_ROOT`` set.

        Args:
            script: Path to the target script relative to ``ROOT``.
            parameters: Command-line arguments forwarded to the script.

        Raises:
            SystemExit: If the child process exits with a nonzero status code.
        """
        result = subprocess.run(
            [sys.executable, str(ROOT / script), *parameters],
            cwd=ROOT, env={**os.environ, "PROJECT_ROOT": str(ROOT)},
        )
        if result.returncode:
            raise SystemExit(result.returncode)

    if args.command == "filter":
        if args.dataset == "all":
            run("scripts/filter/isolated_vertices.py", options)
        else:
            directories = [
                ROOT / "datasets" / args.dataset.replace("-", "_") / stage
                for stage in ("processed", "selected")
            ]
            found = False
            for directory in directories:
                if directory.is_dir():
                    found = True
                    run("scripts/filter/isolated_vertices.py", [str(directory), *options])
            if not found:
                # Still honor --help/-h even before processed/ or selected/ exist.
                if "--help" in options or "-h" in options:
                    run("scripts/filter/isolated_vertices.py", ["--help"])
                else:
                    print(f"No processed or selected directory for {args.dataset}.")
        return

    for target in targets:
        name = target.replace("-", "_")
        base = Path("datasets") / name
        if args.command == "download":
            run(f"scripts/download/download_{name}.py", options)
        elif args.command == "convert":
            if name == "snap":
                run("scripts/convert/snap.py", ["--input", str(base / "raw"), "--output", str(base / "processed"), *options])
            else:
                run("scripts/convert/matrix_market.py", [str(base / "raw"), *options])
        elif args.command == "generate":
            run(f"scripts/generate/{name}.py", ["--out", str(base / "processed"), *options])
        elif args.command == "select":
            defaults = [
                "--input-dir", str(base / "processed"), "--out", str(base / "selected"),
                "--db-name", name, "--count", str(SELECTION_SIZES[target]), "--use-all-if-fewer",
            ]
            if name == "snap":
                defaults.extend(["--skip-edgeless", "--max-per-series", "2"])
            run("scripts/select/representative_selection.py", [*defaults, *options])


if __name__ == "__main__":
    main()
