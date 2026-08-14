#!/usr/bin/env python3
"""Gera grafos Barabási-Albert no formato DIMACS .col."""

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
        description="Gera grafos Barabási-Albert em formato DIMACS .col"
    )
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        default=[100, 250, 500],
        help="Números de vértices (padrão: 100 250 500)",
    )
    parser.add_argument(
        "--m",
        type=int,
        nargs="+",
        default=[2, 4, 6],
        help="Números de arestas por novo vértice (padrão: 2 4 6)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=3,
        help="Repetições por combinação (padrão: 3)",
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
        help="Pasta de saída (padrão: datasets/barabasi_albert/processed)",
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
        for m in args.m:
            if m < 1 or m >= n:
                raise ValueError(
                    f"m deve ser positivo e menor que n (recebido n={n}, m={m})"
                )


def main() -> None:
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    args = parse_args()
    validar_argumentos(args)

    pasta_saida = args.out or (
        Path(project_root) / "datasets" / "barabasi_albert" / "processed"
    )
    pasta_saida.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in args.n:
        for m in args.m:
            for repeticao in range(args.reps):
                graph = nx.barabasi_albert_graph(n, m, seed=seed)
                nome = f"ba_n{n}_m{m}_r{repeticao}.col"
                caminho = pasta_saida / nome
                salvar_dimacs(
                    graph,
                    caminho,
                    comentario=(
                        f"Barabási-Albert n={n} m={m} "
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
