#!/usr/bin/env python3
"""Download, convert, generate, select, and filter graph instances."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
DOWNLOADED = ("harwell-boeing", "dimacs", "bhoslib", "snap")
GENERATED = ("erdos-renyi", "barabasi-albert", "watts-strogatz", "cubic")
DATASETS = (*DOWNLOADED, *GENERATED)
SELECTION_SIZES = {
    "harwell-boeing": 50, "dimacs": 80, "bhoslib": 30, "snap": 20,
    "erdos-renyi": 30, "barabasi-albert": 30, "watts-strogatz": 30, "cubic": 30,
}
COMMAND_TARGETS = {
    "download": DOWNLOADED, "convert": DOWNLOADED, "generate": GENERATED,
    "select": DATASETS, "filter": DATASETS,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
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
    if options[:1] == ["--"]:
        options = options[1:]
    if args.dataset == "all":
        targets = COMMAND_TARGETS[args.command]
    else:
        targets = (args.dataset,)

    def run(script: str, parameters: list[str]) -> None:
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
                defaults.append("--skip-edgeless")
            run("scripts/select/representative_selection.py", [*defaults, *options])


if __name__ == "__main__":
    main()
