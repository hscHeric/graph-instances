#!/usr/bin/env python3
"""Baixa os grafos em lista de arestas da coleção Stanford SNAP."""

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

# Arquivos conferidos nas páginas individuais oficiais do SNAP.
ARQUIVOS_NA_RAIZ = [
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
        f"oregon{familia}_{data}.txt.gz"
        for familia in (1, 2)
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

SNAP_URLS = [f"{BASE_URL}/{nome}" for nome in ARQUIVOS_NA_RAIZ]
SNAP_URLS += [
    f"{BASE_URL}/bigdata/communities/{nome}"
    for nome in (
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
        description="Baixa e extrai os grafos da coleção Stanford SNAP."
    )
    parser.add_argument(
        "--incluir-friendster",
        action="store_true",
        help="Inclui o grafo com-Friendster, que é excepcionalmente grande.",
    )
    return parser.parse_args()


def baixar_arquivo(url: str, destino: Path) -> tuple[str, Path | None]:
    """Baixa um arquivo de forma incremental e permite retomar a task."""
    nome = Path(urlparse(url).path).name
    marcador = destino / ".concluidos" / nome
    if marcador.exists():
        return "JÁ EXISTIA", None

    nome_saida = nome.removesuffix(".gz") if not nome.endswith(".tar.gz") else nome
    caminho_saida = destino / nome_saida
    if caminho_saida.exists():
        return "JÁ EXISTIA", caminho_saida

    temporario = destino / ".downloads" / nome
    temporario.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, timeout=300, stream=True) as resposta:
        resposta.raise_for_status()
        with temporario.open("wb") as arquivo:
            for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                if bloco:
                    arquivo.write(bloco)

    return "OK", temporario


def extrair_arquivo(nome: str, origem: Path, destino: Path) -> int:
    """Extrai o arquivo baixado e retorna a quantidade de arquivos produzidos."""
    if nome.endswith(".tar.gz"):
        with tarfile.open(origem, "r:gz") as arquivo:
            arquivo.extractall(destino, filter="data")
        origem.unlink()
    elif nome.endswith(".zip"):
        with zipfile.ZipFile(origem) as arquivo:
            arquivo.extractall(destino)
        origem.unlink()
    elif nome.endswith(".gz"):
        saida = destino / nome.removesuffix(".gz")
        with gzip.open(origem, "rb") as compactado, saida.open("wb") as arquivo:
            copyfileobj(compactado, arquivo)
        origem.unlink()
    else:
        origem.replace(destino / Path(urlparse(nome).path).name)

    marcador = destino / ".concluidos" / nome
    marcador.parent.mkdir(parents=True, exist_ok=True)
    marcador.touch()
    return 1


def mover_arquivos_para_raiz(destination: Path) -> tuple[int, int]:
    """Move arquivos de subpastas para raw e descarta arquivos readme."""
    movidos = 0
    descartados = 0

    for source in sorted(destination.rglob("*"), key=lambda path: str(path)):
        if source.is_dir():
            continue

        relativo = source.relative_to(destination)
        if relativo.parts[0] in {".downloads", ".concluidos"}:
            continue

        if source.name.lower().startswith("readme") or source.name.startswith("._"):
            source.unlink()
            descartados += 1
            continue

        if source.parent == destination:
            continue

        target = destination / source.name
        if target.exists():
            prefixo = "_".join(relativo.parent.parts)
            target = destination / f"{prefixo}_{source.name}"

        contador = 2
        candidato = target
        while candidato.exists():
            candidato = target.with_name(
                f"{target.stem}_{contador}{target.suffix}"
            )
            contador += 1

        move(source, candidato)
        movidos += 1

    directories = sorted(
        (
            path
            for path in destination.rglob("*")
            if path.is_dir()
            and path.relative_to(destination).parts[0]
            not in {".downloads", ".concluidos"}
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass

    return movidos, descartados


def main() -> None:
    args = parse_args()
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    destino = Path(project_root) / "datasets" / "snap" / "raw"
    destino.mkdir(parents=True, exist_ok=True)
    urls = [*SNAP_URLS, FRIENDSTER_URL] if args.incluir_friendster else SNAP_URLS

    try:
        workers = int(os.environ.get("SNAP_DOWNLOAD_WORKERS", str(len(urls))))
    except ValueError as error:
        raise ValueError("SNAP_DOWNLOAD_WORKERS deve ser um número inteiro.") from error
    if workers < 1:
        raise ValueError("SNAP_DOWNLOAD_WORKERS deve ser maior ou igual a 1.")

    print(
        f"Baixando {len(urls)} arquivos SNAP para {destino} "
        f"com {workers} downloads simultâneos"
    )
    falhas: list[str] = []
    baixados: list[tuple[str, Path]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        downloads = {
            executor.submit(baixar_arquivo, url, destino): url
            for url in urls
        }
        for concluido, future in enumerate(as_completed(downloads), start=1):
            url = downloads[future]
            nome = Path(urlparse(url).path).name
            try:
                status, caminho = future.result()
                print(f"  [{concluido}/{len(urls)}] {status}: {nome}")
                if status == "OK" and caminho is not None:
                    baixados.append((nome, caminho))
            except (requests.RequestException, OSError) as error:
                print(f"  [{concluido}/{len(urls)}] FALHOU: {nome}: {error}")
                falhas.append(nome)

    for nome, caminho in baixados:
        try:
            extrair_arquivo(nome, caminho, destino)
        except (OSError, gzip.BadGzipFile, tarfile.TarError, zipfile.BadZipFile) as error:
            print(f"  FALHOU ao extrair {nome}: {error}")
            falhas.append(nome)

    movidos, descartados = mover_arquivos_para_raiz(destino)
    print(
        f"Organização concluída: {movidos} arquivos movidos para {destino} "
        f"e {descartados} arquivos auxiliares descartados."
    )

    if falhas:
        print(f"{len(falhas)} arquivos falharam: {', '.join(sorted(set(falhas)))}")
    else:
        print("Todos os arquivos SNAP foram baixados e extraídos.")


if __name__ == "__main__":
    main()
