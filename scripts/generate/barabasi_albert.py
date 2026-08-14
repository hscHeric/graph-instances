#!/usr/bin/env python3
"""Gera grafos Barabási-Albert no formato DIMACS .col."""

from __future__ import annotations

import argparse
from pathlib import Path

import networkx as nx


def gerar_progressao_geometrica(inicio: int, fator: float, passos: int) -> list[int]:
    """Gera uma progressão geométrica de tamanhos sem duplicatas."""
    return list(dict.fromkeys(round(inicio * fator**passo) for passo in range(passos)))


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
        default=None,
        help="Números de vértices; sobrepõe --doubling",
    )
    parser.add_argument(
        "--doubling", type=float, nargs=3, default=[200, 1.15, 30],
        metavar=("INÍCIO", "FATOR", "PASSOS"),
        help="Progressão geométrica (padrão: 200 1.15 30)",
    )
    parser.add_argument(
        "--m",
        type=int,
        nargs="+",
        default=[4],
        help="Números de arestas por novo vértice (padrão: 4)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=1,
        help="Repetições por combinação (padrão: 1)",
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
        default=Path("aleatorios_barabasi_albert"),
        help="Pasta de saída (padrão: aleatorios_barabasi_albert)",
    )
    return parser.parse_args()


def obter_valores_n(args: argparse.Namespace) -> list[int]:
    if args.n is not None:
        return args.n
    inicio, fator, passos = args.doubling
    valores = gerar_progressao_geometrica(int(inicio), fator, int(passos))
    print(f"Progressão geométrica gerada ({len(valores)} tamanhos): {valores}")
    return valores


def validar_argumentos(args: argparse.Namespace, valores_n: list[int]) -> None:
    """Valida os parâmetros antes de gerar qualquer arquivo."""
    if args.reps < 1:
        raise ValueError("reps deve ser maior ou igual a 1")

    for n in valores_n:
        if n < 1:
            raise ValueError(f"n deve ser positivo (recebido n={n})")
        for m in args.m:
            if m < 1 or m >= n:
                raise ValueError(
                    f"m deve ser positivo e menor que n (recebido n={n}, m={m})"
                )


def main() -> None:
    args = parse_args()
    valores_n = obter_valores_n(args)
    validar_argumentos(args, valores_n)

    pasta_saida = args.out
    pasta_saida.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in valores_n:
        for m in args.m:
            for repeticao in range(args.reps):
                graph = nx.barabasi_albert_graph(n, m, seed=seed)
                nome = f"ba_n{n}_m{m}_{repeticao}.col"
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
