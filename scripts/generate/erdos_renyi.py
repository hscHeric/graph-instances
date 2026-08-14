#!/usr/bin/env python3
"""Gera grafos Erdős-Rényi G(n, p) no formato DIMACS .col."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import networkx as nx


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
        description="Gera grafos Erdős-Rényi G(n, p) em formato DIMACS .col"
    )
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        required=True,
        help="Números de vértices",
    )
    parser.add_argument(
        "--p",
        type=float,
        nargs="+",
        required=True,
        help="Probabilidades de aresta",
    )
    parser.add_argument(
        "--reps",
        type=int,
        required=True,
        help="Repetições por combinação",
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Seed inicial para reprodutibilidade",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Pasta de saída",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        parser.exit()
    return parser.parse_args()


def validar_argumentos(args: argparse.Namespace) -> None:
    """Valida os parâmetros antes de gerar qualquer arquivo."""
    if args.reps < 1:
        raise ValueError("reps deve ser maior ou igual a 1")

    for n in args.n:
        if n < 1:
            raise ValueError(f"n deve ser positivo (recebido n={n})")

    for probabilidade in args.p:
        if not 0 < probabilidade <= 1:
            raise ValueError(
                f"p deve estar no intervalo (0, 1] (recebido {probabilidade})"
            )


def main() -> None:
    args = parse_args()
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    validar_argumentos(args)

    pasta_saida = args.out
    pasta_saida.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in args.n:
        for probabilidade in args.p:
            for repeticao in range(args.reps):
                graph = nx.erdos_renyi_graph(n, probabilidade, seed=seed)
                nome = f"er_n{n}_p{probabilidade}_r{repeticao}.col"
                caminho = pasta_saida / nome
                salvar_dimacs(
                    graph,
                    caminho,
                    comentario=(
                        f"Erdős-Rényi n={n} p={probabilidade} "
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
