#!/usr/bin/env python3
"""Baixa a coleção Harwell-Boeing do SuiteSparse."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import move

import ssgetpy


def baixar_matriz(matrix, destination: Path) -> bool:
    """Baixa e extrai uma matriz que ainda não existe no destino."""
    if (destination / f"{matrix.name}.mtx").exists():
        return False

    matrix.download(format="MM", destpath=str(destination), extract=True)
    return True


def baixar_matrizes_em_paralelo(matrices, destination: Path, workers: int) -> None:
    """Baixa e extrai matrizes usando múltiplas threads."""
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(baixar_matriz, matrix, destination): matrix
            for matrix in matrices
        }

        total = len(downloads)
        for concluido, future in enumerate(as_completed(downloads), start=1):
            matrix = downloads[future]
            baixada = future.result()
            status = "OK" if baixada else "JÁ EXISTIA"
            print(f"  [{concluido}/{total}] {status}: {matrix.name}")


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

    try:
        workers = int(os.environ.get("HARWELL_BOEING_DOWNLOAD_WORKERS", "15"))
    except ValueError as error:
        raise ValueError(
            "HARWELL_BOEING_DOWNLOAD_WORKERS deve ser um número inteiro."
        ) from error
    if workers < 1:
        raise ValueError(
            "HARWELL_BOEING_DOWNLOAD_WORKERS deve ser maior ou igual a 1."
        )

    print(
        f"Baixando {len(matrices)} matrizes para {destination} "
        f"com {workers} downloads simultâneos"
    )
    baixar_matrizes_em_paralelo(matrices, destination, workers)

    moved_files = mover_matrizes_para_raiz(destination)
    print(f"{moved_files} arquivos .mtx movidos para {destination}")


if __name__ == "__main__":
    main()
