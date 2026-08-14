#!/usr/bin/env python3
"""Gera grafos cúbicos aleatórios no formato DIMACS .col."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import networkx as nx


GRAU = 3


def gerar_progressao_geometrica(
    inicio: int, fator: float, passos: int
) -> list[int]:
    """Gera uma progressão geométrica arredondada para inteiros pares."""
    valores: list[int] = []
    atual = float(inicio)
    for _ in range(passos):
        n = round(atual)
        if n % 2 != 0:
            n += 1
        valores.append(n)
        atual *= fator

    return list(dict.fromkeys(valores))


def salvar_dimacs(graph: nx.Graph, path: Path, comentario: str = "") -> None:
    """Salva um grafo simples no formato DIMACS .col, com vértices 1..n."""
    mapping = {node: indice for indice, node in enumerate(graph.nodes(), start=1)}
    arestas = sorted(
        tuple(sorted((mapping[u], mapping[v]))) for u, v in graph.edges()
    )

    with path.open("w", encoding="utf-8", newline="\n") as arquivo:
        if comentario:
            arquivo.write(f"c {comentario}\n")
        arquivo.write(f"p edge {graph.number_of_nodes()} {len(arestas)}\n")
        for u, v in arestas:
            arquivo.write(f"e {u} {v}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera grafos cúbicos aleatórios em formato DIMACS .col"
    )
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        help="Lista explícita de números de vértices; sobrepõe --doubling",
    )
    parser.add_argument(
        "--doubling",
        type=float,
        nargs=3,
        default=[100.0, 2.0, 5.0],
        metavar=("INÍCIO", "FATOR", "PASSOS"),
        help="Progressão geométrica (padrão: 100 2.0 5)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=5,
        help="Repetições por tamanho (padrão: 5)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed inicial para reprodutibilidade (padrão: 42)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Pasta de saída (padrão: datasets/cubicos/processed)",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        parser.exit()
    return parser.parse_args()


def obter_valores_n(args: argparse.Namespace) -> list[int]:
    """Obtém e valida os tamanhos solicitados."""
    if args.n is not None:
        valores_n = args.n
    else:
        inicio, fator, passos_float = args.doubling
        if not inicio.is_integer() or not passos_float.is_integer():
            raise ValueError("INÍCIO e PASSOS de --doubling devem ser inteiros")
        if fator <= 0:
            raise ValueError("FATOR de --doubling deve ser positivo")
        if passos_float < 1:
            raise ValueError("PASSOS de --doubling deve ser maior ou igual a 1")
        valores_n = gerar_progressao_geometrica(
            int(inicio), fator, int(passos_float)
        )
        print(f"Progressão geométrica gerada: {valores_n}")

    for n in valores_n:
        if n <= GRAU:
            raise ValueError(f"n deve ser maior que {GRAU} (recebido n={n})")
        if n % 2 != 0:
            raise ValueError(
                f"não existe grafo 3-regular com n={n}: n deve ser par"
            )
    return valores_n


def main() -> None:
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    args = parse_args()
    if args.reps < 1:
        raise ValueError("reps deve ser maior ou igual a 1")
    valores_n = obter_valores_n(args)

    pasta_saida = args.out or (
        Path(project_root) / "datasets" / "cubicos" / "processed"
    )
    pasta_saida.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in valores_n:
        for repeticao in range(args.reps):
            graph = nx.random_regular_graph(GRAU, n, seed=seed)
            if any(grau != GRAU for _, grau in graph.degree()):
                raise RuntimeError("o NetworkX gerou um grafo que não é 3-regular")

            nome = f"cubico_n{n}_r{repeticao}.col"
            caminho = pasta_saida / nome
            salvar_dimacs(
                graph,
                caminho,
                comentario=(
                    f"Cúbico (3-regular) n={n} "
                    f"repeticao={repeticao} seed={seed}"
                ),
            )
            print(
                f"OK: {nome}: {graph.number_of_nodes()} vértices, "
                f"{graph.number_of_edges()} arestas"
            )
            total += 1
            seed += 1

    print(f"Concluído: {total} grafos gerados em {pasta_saida}")


if __name__ == "__main__":
    main()
