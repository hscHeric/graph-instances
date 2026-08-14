#!/usr/bin/env python3
"""Baixa a coleção BHOSLIB hospedada no Network Repository."""

from __future__ import annotations

import io
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


def tentar_download(url: str, destino: Path) -> bool:
    """Baixa e extrai um ZIP válido no diretório de destino."""
    resposta = requests.get(url, headers=HEADERS, timeout=120)
    if resposta.status_code != 200 or not resposta.content.startswith(b"PK"):
        return False

    with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo_zip:
        arquivo_zip.extractall(destino)
    return True


def baixar_e_extrair(nome: str, destino: Path) -> bool:
    """Tenta baixar uma instância usando diferentes capitalizações."""
    destino_instancia = destino / ".downloads" / nome
    destino_instancia.mkdir(parents=True, exist_ok=True)

    candidatos = list(dict.fromkeys((nome, nome.lower(), nome.upper())))
    for candidato in candidatos:
        url = f"{BASE_URL}/{candidato}.zip"
        try:
            if tentar_download(url, destino_instancia):
                return True
        except (requests.RequestException, zipfile.BadZipFile, OSError) as error:
            print(f"  Erro ao tentar {url}: {error}")

    print(f"  Nenhuma variação de nome funcionou para {nome}: {candidatos}")
    return False


def mover_arquivos_para_raiz(destination: Path) -> int:
    """Descarta readmes e move os demais arquivos para a raiz de raw."""
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
            raise FileExistsError(
                f"Não foi possível mover {source}: o arquivo {target} já existe."
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
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    destination = Path(project_root) / "datasets" / "bhoslib" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

    try:
        workers = int(os.environ.get("BHOSLIB_DOWNLOAD_WORKERS", "36"))
    except ValueError as error:
        raise ValueError("BHOSLIB_DOWNLOAD_WORKERS deve ser um número inteiro.") from error
    if workers < 1:
        raise ValueError("BHOSLIB_DOWNLOAD_WORKERS deve ser maior ou igual a 1.")

    print(
        f"Baixando {len(BHOSLIB_INSTANCES)} instâncias para {destination} "
        f"com {workers} downloads simultâneos"
    )
    falhas: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(baixar_e_extrair, nome, destination): nome
            for nome in BHOSLIB_INSTANCES
        }
        for concluido, future in enumerate(as_completed(downloads), start=1):
            nome = downloads[future]
            sucesso = future.result()
            print(
                f"  [{concluido}/{len(BHOSLIB_INSTANCES)}] "
                f"{'OK' if sucesso else 'FALHOU'}: {nome}"
            )
            if not sucesso:
                falhas.append(nome)

    moved_files = mover_arquivos_para_raiz(destination)
    print(f"{moved_files} arquivos movidos para {destination}")
    if falhas:
        print(f"{len(falhas)} instâncias falharam: {', '.join(sorted(falhas))}")


if __name__ == "__main__":
    main()
