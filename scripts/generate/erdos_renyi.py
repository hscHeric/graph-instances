#!/usr/bin/env python3
"""Gera grafos Erdős-Rényi G(n, p) no formato DIMACS .col."""

from __future__ import annotations

import argparse
from pathlib import Path

import networkx as nx


def gerar_progressao_geometrica(
    inicio: int, fator: float, passos: int
) -> list[int]:
    """Gera uma progressão geométrica de tamanhos sem duplicatas."""
    valores = [round(inicio * fator**passo) for passo in range(passos)]
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
        description="Gera grafos Erdős-Rényi G(n, p) em formato DIMACS .col"
    )
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        default=None,
        help="Números de vértices; sobrepõe --doubling",
    )
    parser.add_argument(
        "--doubling",
        type=float,
        nargs=3,
        default=[200, 1.15, 30],
        metavar=("INÍCIO", "FATOR", "PASSOS"),
        help="Progressão geométrica (padrão: 200 1.15 30)",
    )
    parser.add_argument(
        "--avg-degree",
        type=float,
        nargs="+",
        default=[10.0],
        help="Grau médio alvo usado para calcular p = grau/(n-1)",
    )
    parser.add_argument(
        "--p",
        type=float,
        nargs="+",
        default=None,
        help="Probabilidades fixas; se informado, substitui --avg-degree",
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
        default=Path("aleatorios_erdos_renyi"),
        help="Pasta de saída (padrão: aleatorios_erdos_renyi)",
    )
    return parser.parse_args()


def obter_valores_n(args: argparse.Namespace) -> list[int]:
    if args.n is not None:
        valores_n = args.n
    else:
        inicio, fator, passos_float = args.doubling
        if not inicio.is_integer() or not passos_float.is_integer():
            raise ValueError("INÍCIO e PASSOS de --doubling devem ser inteiros")
        valores_n = gerar_progressao_geometrica(
            int(inicio), fator, int(passos_float)
        )
        print(f"Progressão geométrica gerada ({len(valores_n)} tamanhos): {valores_n}")
    return valores_n


def validar_argumentos(args: argparse.Namespace, valores_n: list[int]) -> None:
    """Valida os parâmetros antes de gerar qualquer arquivo."""
    if args.reps < 1:
        raise ValueError("reps deve ser maior ou igual a 1")

    for n in valores_n:
        if n < 2:
            raise ValueError(f"n deve ser maior que 1 (recebido n={n})")

    if args.p is not None:
        for probabilidade in args.p:
            if not 0 < probabilidade <= 1:
                raise ValueError(
                    f"p deve estar no intervalo (0, 1] (recebido {probabilidade})"
                )
    for grau in args.avg_degree:
        if grau <= 0:
            raise ValueError("avg-degree deve ser positivo")


def main() -> None:
    args = parse_args()
    valores_n = obter_valores_n(args)
    validar_argumentos(args, valores_n)

    pasta_saida = args.out
    pasta_saida.mkdir(parents=True, exist_ok=True)

    total = 0
    seed = args.seed
    for n in valores_n:
        if args.p is not None:
            densidades = [(f"p{p}", p, f"p={p}") for p in args.p]
        else:
            densidades = [
                (
                    f"deg{grau}",
                    min(grau / (n - 1), 1.0),
                    f"grau_medio_alvo={grau}",
                )
                for grau in args.avg_degree
            ]
        for tag, probabilidade, comentario_densidade in densidades:
            for repeticao in range(args.reps):
                graph = nx.erdos_renyi_graph(n, probabilidade, seed=seed)
                nome = f"er_n{n}_{tag}_{repeticao}.col"
                caminho = pasta_saida / nome
                salvar_dimacs(
                    graph,
                    caminho,
                    comentario=(
                        f"Erdős-Rényi n={n} {comentario_densidade} "
                        f"p_calculado={probabilidade:.8f} "
                        f"repeticao={repeticao} seed={seed}"
                    ),
                )
                print(
                    f"OK: {nome}: {graph.number_of_nodes()} vértices, "
                    f"{graph.number_of_edges()} arestas, "
                    f"grau médio real={2 * graph.number_of_edges() / n:.2f}"
                )
                total += 1
                seed += 1

    print(f"Concluído: {total} grafos gerados em {pasta_saida}")


if __name__ == "__main__":
    main()
