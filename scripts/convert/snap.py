#!/usr/bin/env python3
"""Converte listas de arestas SNAP para grafos simples DIMACS .col."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path


AUXILIARY_TERMS = (
    "readme",
    "target",
    "feature",
    "label",
    "citing",
    "description",
    "article",
    "category",
    "page-name",
    "username",
    "network_list",
    "shortest-path-distance-matrix",
    "path_finished",
    "path_unfinished",
)


def classificar_arquivo(path: Path) -> str | None:
    """Identifica o parser aplicável ou retorna None para arquivo auxiliar."""
    nome = path.name.lower()
    if any(parte.startswith(".") for parte in path.parts):
        return None
    if nome.startswith((".", "._")) or any(term in nome for term in AUXILIARY_TERMS):
        return None
    if path.suffix.lower() in {".json", ".py", ".md"}:
        return None
    if nome == "wiki-rfa.txt":
        return "wiki_rfa"
    if re.fullmatch(r"network\d+\.csv", nome):
        return "matriz_temporal"
    if nome.endswith("_weighted.csv"):
        return None
    if nome == "mooc_actions.tsv":
        return "mooc"
    if path.suffix.lower() in {".edge", ".edges", ".edgelist"}:
        return "espacos"
    if path.suffix.lower() == ".csv":
        if "edge" in nome or nome.startswith("soc-sign-bitcoin"):
            return "csv"
        return None
    if path.suffix.lower() == ".tsv":
        if nome == "links.tsv" or nome.startswith("soc-reddithyperlinks"):
            return "tsv"
        return None
    if path.suffix.lower() == ".txt":
        return "espacos"
    return None


def ler_espacos(path: Path) -> Iterator[tuple[str, str]]:
    with path.open("r", encoding="utf-8", errors="replace") as arquivo:
        for linha in arquivo:
            texto = linha.strip()
            if not texto or texto.startswith(("#", "%")):
                continue
            campos = texto.split()
            if len(campos) >= 2:
                yield campos[0], campos[1]


def ler_delimitado(path: Path, delimitador: str) -> Iterator[tuple[str, str]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as arquivo:
        linhas = (linha for linha in arquivo if linha.strip() and not linha.startswith("#"))
        leitor = csv.reader(linhas, delimiter=delimitador)
        primeira = next(leitor, None)
        if primeira is None or len(primeira) < 2:
            return

        cabecalho = {campo.strip().lower() for campo in primeira}
        tem_cabecalho = bool(
            cabecalho
            & {
                "source_subreddit", "target_subreddit", "source", "target",
                "id_1", "id_2", "id1", "id2", "node_1", "node_2",
                "numeric_id_1", "numeric_id_2",
            }
        )
        if not tem_cabecalho:
            yield primeira[0].strip(), primeira[1].strip()

        for campos in leitor:
            if len(campos) >= 2:
                yield campos[0].strip(), campos[1].strip()


def ler_mooc(path: Path) -> Iterator[tuple[str, str]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as arquivo:
        leitor = csv.DictReader(arquivo, delimiter="\t")
        for linha in leitor:
            yield f"usuario:{linha['USERID']}", f"alvo:{linha['TARGETID']}"


def ler_wiki_rfa(path: Path) -> Iterator[tuple[str, str]]:
    origem: str | None = None
    with path.open("r", encoding="utf-8", errors="replace") as arquivo:
        for linha in arquivo:
            if linha.startswith("SRC:"):
                origem = linha[4:].strip()
            elif linha.startswith("TGT:") and origem is not None:
                yield origem, linha[4:].strip()
                origem = None


def ler_matriz_temporal(path: Path) -> Iterator[tuple[str, str]]:
    """Agrega em um grafo as conexões observadas em qualquer instante."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as arquivo:
        leitor = csv.reader(arquivo)
        cabecalho = next(leitor, [])
        colunas: list[tuple[int, str, str]] = []
        for indice, nome in enumerate(cabecalho):
            match = re.fullmatch(r"P(\d+)_TO_P(\d+)", nome)
            if match and match.group(1) != match.group(2):
                colunas.append((indice, f"P{match.group(1)}", f"P{match.group(2)}"))

        vistas: set[tuple[str, str]] = set()
        for linha in leitor:
            for indice, origem, alvo in colunas:
                if indice < len(linha) and float(linha[indice]) != 0:
                    aresta = tuple(sorted((origem, alvo)))
                    if aresta not in vistas:
                        vistas.add(aresta)
                        yield aresta


def extrair_arestas(path: Path, formato: str) -> Iterable[tuple[str, str]]:
    if formato == "espacos":
        return ler_espacos(path)
    if formato == "csv":
        return ler_delimitado(path, ",")
    if formato == "tsv":
        return ler_delimitado(path, "\t")
    if formato == "mooc":
        return ler_mooc(path)
    if formato == "wiki_rfa":
        return ler_wiki_rfa(path)
    if formato == "matriz_temporal":
        return ler_matriz_temporal(path)
    raise ValueError(f"formato SNAP desconhecido: {formato}")


def numero_vertices_declarado(path: Path) -> int | None:
    if path.suffix.lower() != ".txt":
        return None
    pattern = re.compile(r"#\s*Nodes:\s*(\d+)", re.IGNORECASE)
    with path.open("r", encoding="utf-8", errors="replace") as arquivo:
        for _ in range(20):
            linha = arquivo.readline()
            if not linha:
                break
            match = pattern.search(linha)
            if match:
                return int(match.group(1))
    return None


def converter_arquivo(path: Path, output: Path, formato: str) -> bool:
    """Converte um arquivo usando SQLite para limitar o uso de memória."""
    output.mkdir(parents=True, exist_ok=True)
    destino = output / f"{path.stem}.col"
    banco_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix="snap_", suffix=".sqlite3", dir=output, delete=False
        ) as banco:
            banco_path = Path(banco.name)

        conexao = sqlite3.connect(banco_path)
        conexao.execute("PRAGMA journal_mode=OFF")
        conexao.execute("PRAGMA synchronous=OFF")
        conexao.execute("CREATE TABLE edges (u TEXT, v TEXT, PRIMARY KEY (u, v)) WITHOUT ROWID")

        lote: list[tuple[str, str]] = []
        for origem, alvo in extrair_arestas(path, formato):
            if not origem or not alvo or origem == alvo:
                continue
            u, v = sorted((origem, alvo))
            lote.append((u, v))
            if len(lote) == 50_000:
                conexao.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?)", lote)
                lote.clear()
        if lote:
            conexao.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?)", lote)
        conexao.commit()

        conexao.execute(
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, original TEXT UNIQUE)"
        )
        conexao.execute("INSERT OR IGNORE INTO nodes(original) SELECT u FROM edges")
        conexao.execute("INSERT OR IGNORE INTO nodes(original) SELECT v FROM edges")
        vertices_observados = conexao.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        vertices_declarados = numero_vertices_declarado(path)
        total_vertices = max(vertices_observados, vertices_declarados or 0)
        total_arestas = conexao.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        if total_vertices == 0 or total_arestas == 0:
            conexao.close()
            destino.unlink(missing_ok=True)
            print(
                f"PULADO: {path.name}: grafo vazio ou somente com "
                "vértices isolados"
            )
            return False

        with destino.open("w", encoding="utf-8", newline="\n") as arquivo:
            arquivo.write(f"c Convertido de {path.name}; direção, pesos e tempo ignorados\n")
            arquivo.write(f"p edge {total_vertices} {total_arestas}\n")
            consulta = """
                SELECT nu.id, nv.id
                FROM edges e
                JOIN nodes nu ON nu.original = e.u
                JOIN nodes nv ON nv.original = e.v
                ORDER BY nu.id, nv.id
            """
            for u, v in conexao.execute(consulta):
                arquivo.write(f"e {u} {v}\n")

        conexao.close()
        print(f"OK: {path.name} -> {total_vertices} vértices, {total_arestas} arestas")
        return True
    except Exception as error:
        destino.unlink(missing_ok=True)
        print(f"FALHOU: {path}: {error}")
        return False
    finally:
        if banco_path is not None:
            banco_path.unlink(missing_ok=True)


def verificar_e_remover_grafos_invalidos(output: Path) -> int:
    """Remove .col sem vértices, sem arestas ou apenas com vértices isolados."""
    removidos = 0
    if not output.exists():
        return removidos

    print("Verificando os grafos convertidos...")
    for caminho in sorted(output.glob("*.col")):
        vertices: int | None = None
        arestas_declaradas: int | None = None
        encontrou_aresta = False

        try:
            with caminho.open("r", encoding="utf-8", errors="replace") as arquivo:
                for linha in arquivo:
                    if linha.startswith("p "):
                        partes = linha.split()
                        if len(partes) >= 4:
                            vertices = int(partes[2])
                            arestas_declaradas = int(partes[3])
                    elif linha.startswith("e "):
                        encontrou_aresta = True

                    if (
                        vertices is not None
                        and arestas_declaradas is not None
                        and encontrou_aresta
                    ):
                        break
        except (OSError, ValueError):
            vertices = None
            arestas_declaradas = None

        valido = (
            vertices is not None
            and vertices > 0
            and arestas_declaradas is not None
            and arestas_declaradas > 0
            and encontrou_aresta
        )
        if not valido:
            caminho.unlink(missing_ok=True)
            removidos += 1
            print(f"REMOVIDO [grafo inválido]: {caminho}")

    print(f"Verificação concluída: {removidos} grafo(s) inválido(s) removido(s).")
    return removidos


def parse_args() -> argparse.Namespace:
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError("PROJECT_ROOT não está definida.")
    raiz = Path(project_root) / "datasets" / "snap"

    parser = argparse.ArgumentParser(description="Converte grafos SNAP para DIMACS .col")
    parser.add_argument("--input", type=Path, default=raiz / "raw", help="Pasta SNAP de entrada")
    parser.add_argument("--output", type=Path, default=raiz / "processed", help="Pasta DIMACS de saída")
    parser.add_argument("--analisar", action="store_true", help="Apenas classifica os arquivos")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    arquivos = sorted(path for path in args.input.rglob("*") if path.is_file())
    selecionados = [(path, formato) for path in arquivos if (formato := classificar_arquivo(path))]
    ignorados = len(arquivos) - len(selecionados)
    print(f"Encontrados: {len(selecionados)} grafos; {ignorados} arquivos auxiliares ignorados")

    selecionados_paths = {path for path, _ in selecionados}
    for path, formato in selecionados:
        print(f"MANTER [{formato}]: {path}")
    for path in arquivos:
        if path not in selecionados_paths:
            print(f"IGNORAR [auxiliar]: {path}")

    if args.analisar:
        print("Análise concluída; nenhum arquivo foi convertido.")
        return

    convertidos = sum(
        converter_arquivo(path, args.output, formato)
        for path, formato in selecionados
    )
    verificar_e_remover_grafos_invalidos(args.output)
    print(f"Concluído: {convertidos} de {len(selecionados)} grafos convertidos")


if __name__ == "__main__":
    main()
