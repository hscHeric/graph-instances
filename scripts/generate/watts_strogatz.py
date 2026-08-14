#!/usr/bin/env python3
"""Gera grafos Watts-Strogatz no formato DIMACS .col."""

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
        description="Gera grafos Watts-Strogatz em formato DIMACS .col"
    )
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        required=True,
        help="Números de vértices",
    )
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        required=True,
        help="Números de vizinhos no anel inicial",
    )
    parser.add_argument(
        "--p-rewire",
        type=float,
        nargs="+",
        required=True,
        help="Probabilidades de reconexão",
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
        for k in args.k:
            if k < 1 or k >= n:
                raise ValueError(
                    f"k deve ser positivo e menor que n (recebido n={n}, k={k})"
                )
            if k % 2 != 0:
                print(f"AVISO: k={k} é ímpar; prefira valores pares.")

    for probabilidade in args.p_rewire:
        if not 0 <= probabilidade <= 1:
            raise ValueError(
                "p-rewire deve estar entre 0 e 1 "
                f"(recebido {probabilidade})"
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
        for k in args.k:
            for p_rewire in args.p_rewire:
                for repeticao in range(args.reps):
                    graph = nx.watts_strogatz_graph(n, k, p_rewire, seed=seed)
                    nome = f"ws_n{n}_k{k}_p{p_rewire}_r{repeticao}.col"
                    caminho = pasta_saida / nome
                    salvar_dimacs(
                        graph,
                        caminho,
                        comentario=(
                            f"Watts-Strogatz n={n} k={k} "
                            f"p_rewire={p_rewire} repeticao={repeticao} seed={seed}"
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
