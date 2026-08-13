#!/usr/bin/env python3
"""Baixa a coleção Harwell-Boeing do SuiteSparse."""

from __future__ import annotations

import os
from pathlib import Path
from shutil import move

import ssgetpy


def mover_matrizes_para_raiz(destination: Path) -> int:
    """Move os arquivos .mtx extraídos para a raiz do diretório raw."""
    moved_files = 0

    for source in destination.rglob("*.mtx"):
        if source.parent == destination:
            continue

        target = destination / source.name
        if target.exists():
            raise FileExistsError(
                f"Não foi possível mover {source}: o arquivo {target} já existe."
            )

        move(source, target)
        moved_files += 1

    # Remove somente os diretórios que ficaram vazios após a movimentação.
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
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    destination = Path(project_root) / "datasets" / "harwell_boeing" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

    matrices = ssgetpy.search(group="HB", limit=10_000)
    if not matrices:
        raise RuntimeError("Nenhuma matriz da coleção Harwell-Boeing foi encontrada.")

    print(f"Baixando {len(matrices)} matrizes para {destination}")
    matrices.download(format="MM", destpath=str(destination), extract=True)

    moved_files = mover_matrizes_para_raiz(destination)
    print(f"{moved_files} arquivos .mtx movidos para {destination}")


if __name__ == "__main__":
    main()
