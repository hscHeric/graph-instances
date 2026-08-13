#!/usr/bin/env python3
"""Baixa a coleção DIMACS hospedada no Network Repository."""

from __future__ import annotations

import io
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import move

import requests


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

BASE_URL = "https://nrvis.com/download/data/dimacs"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def tentar_download(url: str, destino: Path) -> bool:
    """Baixa e extrai um ZIP válido no diretório de destino."""
    resposta = requests.get(url, headers=HEADERS, timeout=60)
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

    destination = Path(project_root) / "datasets" / "dimacs" / "raw"
    destination.mkdir(parents=True, exist_ok=True)

    try:
        workers = int(os.environ.get("DIMACS_DOWNLOAD_WORKERS", "78"))
    except ValueError as error:
        raise ValueError(
            "DIMACS_DOWNLOAD_WORKERS deve ser um número inteiro."
        ) from error
    if workers < 1:
        raise ValueError("DIMACS_DOWNLOAD_WORKERS deve ser maior ou igual a 1.")

    print(
        f"Baixando {len(DIMACS_INSTANCES)} instâncias para {destination} "
        f"com {workers} downloads simultâneos"
    )
    falhas: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(baixar_e_extrair, nome, destination): nome
            for nome in DIMACS_INSTANCES
        }
        for concluido, future in enumerate(as_completed(downloads), start=1):
            nome = downloads[future]
            sucesso = future.result()
            print(
                f"  [{concluido}/{len(DIMACS_INSTANCES)}] "
                f"{'OK' if sucesso else 'FALHOU'}: {nome}"
            )
            if not sucesso:
                falhas.append(nome)

    moved_files = mover_arquivos_para_raiz(destination)
    print(f"{moved_files} arquivos movidos para {destination}")

    if falhas:
        print(f"{len(falhas)} instâncias falharam: {', '.join(falhas)}")


if __name__ == "__main__":
    main()
